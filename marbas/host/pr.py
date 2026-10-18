"""Marbas host side of GitHub: open the PR for a thread, follow it to production (merge only if AUTO_MERGE).

The scope check, the push and the PR are shared with the other agents in `shared/lib/pull_request.py`.
"""

import logging
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402
from github import ENV_FILE, GUARDED, REPO, checks_state, describe, github, me, team_logins, token  # noqa: E402
from pull_request import commit_patch, no_mention, open_pull_request, patch_paths, scope_error, without_attribution  # noqa: E402

SERVICE = "marbas"  # the Axiom `service`

GREEN = {"success", "skipped", "neutral"}
LOG_TAIL_LINES = 120
FAILURE_LINE = re.compile(r"##\[error\]|✖|\bnot ok\b|\bFAIL\b|Error:|AssertionError|ERR_", re.IGNORECASE)

log = logging.getLogger("marbas-pr")

DEPLOY_TIMEOUT = int(config.number("MARBAS_DEPLOY_TIMEOUT_SECONDS", 5400))  # 90 minutes
# A deploy provider reports a GitHub deployment a minute or two after the push; none after this means no deploy started.
NO_DEPLOY_TIMEOUT = int(config.number("MARBAS_NO_DEPLOY_TIMEOUT_SECONDS", 1200))
# The GitHub deployment environments that count as production. Empty: a green merge is the end, with no deploy watch.
DEPLOY_ENVIRONMENT = config.DEPLOY_ENVIRONMENT
# Off by default: a person merges. Set MARBAS_AUTO_MERGE=true in legion.env to turn it on.
AUTO_MERGE = config.flag("MARBAS_AUTO_MERGE", False)
CI_FIX_ATTEMPTS = 2  # then a human takes it
# true: an engineer's "Request changes" starts a run that applies the review on the PR branch. false: a human takes it.
ADDRESS_REVIEW = config.flag("MARBAS_ADDRESS_REVIEW", False)
REVIEW_FIX_ATTEMPTS = 3  # then a human takes it


def assign_issue(issue: str, login: str) -> bool:
    """The engineer who rejects a plan or a PR takes the issue: it moves to them. False when it cannot."""
    try:
        github(f"repos/{REPO}/issues/{issue}", method="PATCH", body={"assignees": [login]})
        return True
    except Exception as error:
        log.warning("could not assign #%s to %s: %s", issue, login, describe(error))
        return False


def open_prs_for(issue: str) -> list[int]:
    """Open PRs that reference the issue. A run on an issue someone already works on wastes the run and two reviews."""
    events = github(f"repos/{REPO}/issues/{issue}/timeline?per_page=100", "github_sandbox_token")
    return sorted({e["source"]["issue"]["number"] for e in events
                   if e.get("event") == "cross-referenced" and e.get("source", {}).get("issue", {}).get("pull_request")
                   and e["source"]["issue"].get("state") == "open"})


def extra_files(paths: set[str], plan: dict) -> list[str]:
    """Files outside the approved plan that an engineer may still approve. Empty when a guarded path is in the patch."""
    if any(path.startswith(GUARDED) or ENV_FILE.search(path) for path in paths):
        return []
    allowed = {entry.split()[0] for entry in plan.get("files", []) if entry.strip()}
    return sorted(path for path in paths if path not in allowed)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def job_log_tail(job_id: int) -> str:
    """The end of a job log. GitHub answers with a redirect to signed storage, which must not get our token."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"Bearer {token()}",
                 "Accept": "application/vnd.github+json", "User-Agent": config.USER_AGENT})
    try:
        urllib.request.build_opener(_NoRedirect).open(request, timeout=20)
        return ""
    except urllib.error.HTTPError as redirect:
        if redirect.code not in (301, 302, 307, 308):
            return f"(no log: GitHub answered {redirect.code})"
        with urllib.request.urlopen(redirect.headers["Location"], timeout=30) as response:
            lines = response.read().decode("utf-8", "replace").splitlines()
    # The job's cleanup steps fill the end of the log, so keep the failure lines from anywhere, then the end for context.
    lines = [re.sub(r"^\S+Z ", "", line) for line in lines]
    failures = [line for line in lines if FAILURE_LINE.search(line)][:LOG_TAIL_LINES]
    return "\n".join(failures + ["--- end of log ---"] + lines[-40:])


def failed_jobs(sha: str) -> list[dict]:
    """The failed jobs of every workflow run on the PR head, with the end of each log."""
    jobs = []
    for run in github(f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100")["workflow_runs"]:
        if run["conclusion"] in GREEN or run["status"] != "completed":
            continue
        for job in github(f"repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]:
            if job["conclusion"] == "failure":
                jobs.append({"name": job["name"], "url": job["html_url"], "run": run["id"], "id": job["id"],
                             "log": job_log_tail(job["id"])})
    return jobs


def rerun_failed(jobs: list[dict]) -> str | None:
    """Re-runs the failed jobs of each run once. Returns why it could not, or None."""
    for run in sorted({job["run"] for job in jobs}):
        try:
            github(f"repos/{REPO}/actions/runs/{run}/rerun-failed-jobs", method="POST", body={})
        except urllib.error.HTTPError as error:
            return f"GitHub answered *{error.code}* when I asked to re-run the failed jobs"
    return None


def review_state(pr: dict) -> tuple[str, str]:
    """("approved", login), ("changes", login) or ("waiting", "") for the PR head, counting engineers only."""
    latest: dict[str, dict] = {}
    for review in github(f"repos/{REPO}/pulls/{pr['number']}/reviews?per_page=100"):
        if review["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[review["user"]["login"]] = review
    team = team_logins()
    for login, review in latest.items():
        if review["state"] == "CHANGES_REQUESTED":
            return "changes", login
    for login, review in latest.items():
        # An approval of an older commit does not cover a later push.
        if review["state"] == "APPROVED" and login in team and review["commit_id"] == pr["head"]["sha"]:
            return "approved", login
    return "waiting", ""


def latest_change_request(number: int, login: str) -> dict:
    """The last "Request changes" review of one reviewer on the PR."""
    reviews = github(f"repos/{REPO}/pulls/{number}/reviews?per_page=100")
    asked = [r for r in reviews if r["user"]["login"] == login and r["state"] == "CHANGES_REQUESTED"]
    return asked[-1] if asked else {"id": 0, "body": ""}


def review_comments(number: int, review_id: int) -> list[dict]:
    """The inline comments of one review, with the code they point at."""
    return [{"path": c["path"], "line": c.get("line") or c.get("original_line"), "body": c["body"],
             "diffHunk": c.get("diff_hunk", "")}
            for c in github(f"repos/{REPO}/pulls/{number}/reviews/{review_id}/comments?per_page=100")]


def changes_text() -> str:
    """What a "Request changes" does, for the Slack thread and the PR footer; it follows the review switch."""
    if ADDRESS_REVIEW:
        return f"*Request changes*, and I apply them, up to {REVIEW_FIX_ATTEMPTS} rounds."
    return "*Request changes*, and a human takes it."


def next_step_text() -> str:
    """What happens after the PR opens, for the Slack thread; it follows the merge and review switches."""
    if AUTO_MERGE:
        return f"An engineer approves it on GitHub. Then I merge it, after CI is green. {changes_text()}"
    return f"An engineer approves and merges it on GitHub. Then I watch the deploy and tell this thread. {changes_text()}"


def open_pr(issue: str, run_dir: Path, result: dict, plan: dict) -> dict:
    """Pushes fix/issue-N-* from the patch and opens the PR. Returns {"pr": n, "url": ...} or {"error": ...}."""
    patch_file, base_file = run_dir / "fix.patch", run_dir / "base.sha"
    if not patch_file.exists() or not base_file.exists():
        return {"error": "the run left no patch"}
    patch = patch_file.read_text()
    if error := scope_error(patch_paths(patch), plan):
        return {"error": error}
    base = base_file.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        return {"error": "the base commit is not a sha"}

    # A bug form titles issues "🐞 [BUG] ..." or "bug: ...": the PR title keeps only the text.
    title = config.TITLE_PREFIX.sub("", github(f"repos/{REPO}/issues/{issue}")["title"])
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    pr_title, body = pr_text(issue, title, result, plan)
    return open_pull_request(SERVICE, issue, base, f"fix/issue-{issue}-{slug}", patch, pr_title, body, plan)


def pr_text(issue: str, title: str, result: dict, plan: dict) -> tuple[str, str]:
    """The PR title and body. The agent writes them from /create-pr; the host adds only the Marbas line."""
    footer = (f"_Marbas opened this PR after an engineer approved the plan in Slack. Score: "
              f"{result.get('score')} ({no_mention(result.get('label'))}). " +
              ("An engineer approves it, and Marbas merges after CI is green." if AUTO_MERGE else
               "An engineer approves and merges it. Then Marbas watches the deploy.") +
              (" Request changes, and Marbas applies them._" if ADDRESS_REVIEW else "_"))
    written = str(result.get("prBody") or "").strip()
    if written:
        body = without_attribution(no_mention(written))
        if f"Closes #{issue}" not in body:
            body += f"\n\nCloses #{issue}"
        pr_title = no_mention(str(result.get("prTitle") or f"fix: {title}")).splitlines()[0][:72]
        if not pr_title.startswith("fix: "):
            pr_title = f"fix: {pr_title}"[:72]
        return pr_title, f"{body}\n\n{footer}"
    # A run without prTitle and prBody (an older skill) gets the evidence list.
    body = "\n".join([
        f"Closes #{issue}.",
        "",
        f"**Cause:** {no_mention(result.get('cause') or plan.get('cause', ''))}",
        f"**Fix:** {no_mention(plan.get('fix', ''))}",
        f"**Proof:** {no_mention(result.get('proof') or plan.get('proof', ''))}",
        *([f"**Not proved:** {no_mention(result['reason'])}"] if result.get("reason") else []),
        *([f"**A human must check:** {no_mention(result['humanCheck'])}"] if result.get("humanCheck") else []),
        "", "## Evidence", *[f"- {no_mention(line)}" for line in result.get("evidence", [])],
        "", "## Commands", *[f"- {no_mention(line)}" for line in result.get("commands", [])],
        "", footer,
    ])
    return f"fix: {title[:72]}", body


def push_fix(branch: str, run_dir: Path, plan: dict, message: str) -> dict:
    """Pushes a CI or review fix as a new commit on the PR branch (never a force push). Returns {"sha"} or {"error"}."""
    patch_file = run_dir / "fix.patch"
    if not patch_file.exists():
        return {"error": "the run left no patch"}
    patch = patch_file.read_text()
    if error := scope_error(patch_paths(patch), plan):
        return {"error": error}
    if not re.fullmatch(r"fix/issue-\d+-[a-z0-9-]+", branch):
        return {"error": "the PR branch is not a Marbas branch"}
    return {"sha": commit_patch(me(), branch, branch, patch, no_mention(message).splitlines()[0][:72], plan)}


def answer_review(number: int, login: str, reply: str, sha: str, attempt: int) -> bool:
    """Tells the reviewer on GitHub what changed, and asks them to review again. False when it cannot."""
    try:
        github(f"repos/{REPO}/issues/{number}/comments", method="POST",
               body={"body": f"{no_mention(reply)}\n\n_Marbas applied the review in {sha[:7]}, round {attempt} "
                             f"of {REVIEW_FIX_ATTEMPTS}._"})
        github(f"repos/{REPO}/pulls/{number}/requested_reviewers", method="POST", body={"reviewers": [login]})
        return True
    except Exception as error:
        log.warning("could not answer the review on PR #%s: %s", number, describe(error))
        return False


def step(thread: dict) -> tuple[str, str] | None:
    """Moves a PR thread one step. Returns (new stage, message for the thread), or None when nothing changed."""
    if thread["stage"] == "pr_open":
        pr = github(f"repos/{REPO}/pulls/{thread['pr']}")
        if pr["merged"]:
            who = (pr.get("merged_by") or {}).get("login", "an engineer")
            thread.update(merge_sha=pr["merge_commit_sha"], merged_at=time.time())
            return "deploying", f"🚀 `{who}` merged `PR #{pr['number']}`. I will watch the deploy to production."
        if pr["state"] == "closed":
            return "needs_human", f"`PR #{pr['number']}` was closed without a merge. *A human takes it from here.*"
        if checks_state(pr["head"]["sha"]) == "red":
            if thread.get("ci_attempts", 0) >= CI_FIX_ATTEMPTS:
                return "needs_human", (f"CI is still red on `PR #{pr['number']}` after {CI_FIX_ATTEMPTS} fixes. "
                                       "*A human takes it from here.*")
            jobs = failed_jobs(pr["head"]["sha"])
            thread.update(ci_attempts=thread.get("ci_attempts", 0) + 1, ci_jobs=jobs, branch=pr["head"]["ref"],
                          head_sha=pr["head"]["sha"])
            names = ", ".join(f"`{job['name']}`" for job in jobs) or "a check"
            return "ci_fixing", f"🔧 CI is red on `PR #{pr['number']}`: {names}. I will read the logs and fix it."
        state, login = review_state(pr)
        if state == "changes":
            review = latest_change_request(pr["number"], login)
            # A change request stays open after the bot pushes; it waits for the reviewer to look again.
            if review["id"] in thread.get("reviews_done", []):
                return None
            # Kept for the memory note: the engineer's words are the lesson.
            thread.update(changes_by=login, changes_body=review.get("body") or "")
            # Only an engineer's review drives a run: the bot writes code from its words.
            if ADDRESS_REVIEW and login in team_logins():
                if thread.get("review_attempts", 0) >= REVIEW_FIX_ATTEMPTS:
                    thread["react"] = "x"
                    return "needs_human", (f"`{login}` still requests changes on `PR #{pr['number']}` after "
                                           f"{REVIEW_FIX_ATTEMPTS} rounds. *A human takes it from here.*")
                thread.update(review_attempts=thread.get("review_attempts", 0) + 1, branch=pr["head"]["ref"],
                              reviews_done=thread.get("reviews_done", []) + [review["id"]],
                              review={"reviewer": login, "id": review["id"], "body": review.get("body") or "",
                                      "comments": review_comments(pr["number"], review["id"])})
                return "review_fixing", (f"🔧 `{login}` requested changes on `PR #{pr['number']}`. "
                                         "I will apply them and push a new commit.")
            thread["react"] = "x"
            return "needs_human", f"`{login}` requested changes on `PR #{pr['number']}`. *A human takes it from here.*"
        if state != "approved":
            return None
        checks = checks_state(pr["head"]["sha"])
        if checks != "green" or pr.get("mergeable") is not True:
            return None
        if not AUTO_MERGE:
            # Merge is off: say once that the PR is ready, then wait for a person to merge it.
            if thread.get("ready_told"):
                return None
            thread.update(ready_told=True, react="heavy_check_mark")
            return "pr_open", (f"🚀 `{login}` approved `PR #{pr['number']}` and CI is green. An engineer merges it "
                               "on GitHub. Then I watch the deploy and tell this thread.")
        merged = github(f"repos/{REPO}/pulls/{pr['number']}/merge", method="PUT",
                        body={"merge_method": "merge", "sha": pr["head"]["sha"]})
        thread.update(merge_sha=merged["sha"], merged_at=time.time(), react="heavy_check_mark")
        return "deploying", f"🚀 `{login}` approved `PR #{pr['number']}` and CI is green. I merged it. I will watch the deploy."
    if thread["stage"] == "deploying":
        sha = thread.get("merge_sha") or github(f"repos/{REPO}/pulls/{thread['pr']}")["merge_commit_sha"]
        thread["merge_sha"] = sha
        thread.setdefault("merged_at", time.time())
        runs = github(f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100")["workflow_runs"]
        failed = [r["name"] for r in runs if r["status"] == "completed" and r["conclusion"] not in GREEN]
        if failed:
            return "needs_human", f"A workflow failed on the merge: `{', '.join(failed)}`. *A human takes it from here.*"
        if not DEPLOY_ENVIRONMENT:
            # No deploy watch: the merge is live once every workflow on it ends green.
            if any(r["status"] != "completed" for r in runs):
                return None
            return "deployed", "🚀 Every workflow on the merge is green, and no deploy watch is set, so I take it as live."
        # The deploy provider reports each service as a GitHub deployment; a skipped workflow deploys nothing.
        deploys = [d for d in github(f"repos/{REPO}/deployments?sha={sha}&per_page=100")
                   if d["environment"].startswith(DEPLOY_ENVIRONMENT)]
        states = {d["environment"]: (github(f"repos/{REPO}/deployments/{d['id']}/statuses?per_page=1") or [{}])[0].get("state")
                  for d in deploys}
        broken = sorted(env for env, state in states.items() if state in ("failure", "error", "inactive"))
        if broken:
            return "needs_human", f"The production deploy failed: `{', '.join(broken)}`. *A human takes it from here.*"
        waited = time.time() - thread["merged_at"]
        if states and all(state == "success" for state in states.values()):
            names = ", ".join(f"`{env.split(' - ')[-1]}`" for env in sorted(states))
            return "deployed", f"🚀 Production is live: {names}, *{int(waited // 60)} minutes* after the merge."
        if not states and waited > NO_DEPLOY_TIMEOUT:
            return "needs_human", "No production deploy started from the merge, so check the deploy provider. *A human takes it from here.*"
        if waited > DEPLOY_TIMEOUT:
            return "needs_human", (f"The production deploy did not finish in *{DEPLOY_TIMEOUT // 60} minutes*. "
                                   "*A human takes it from here.*")
    return None
