"""The agents' host side of a pull request: check the patch scope, push it and open the PR.

Only the host holds the write token. When that account may bypass the base branch protection, nothing on GitHub
checks the patch, so the scope is checked here. Marbas checks approval and CI in `pr.py`.
"""

import logging
import re
import subprocess
import tempfile
import urllib.error
from pathlib import Path

import axiom
import config
# The skills refuse GUARDED paths; scope_error refuses them again, because the patch is the output of a model.
from github import ATTRIBUTION, ENV_FILE, GUARDED, REPO, describe, git_auth, github

# Every bot PR: the bot account is the assignee, and LEGION_PR_REVIEWERS plus LEGION_PR_TEAM_REVIEWERS review it.
PR_REVIEWERS = config.PR_REVIEWERS
PR_TEAM_REVIEWERS = config.PR_TEAM_REVIEWERS

log = logging.getLogger("legion-pull-request")


def no_mention(text: str) -> str:
    # A zero-width space after @ keeps a model's text from pinging a person or a team on GitHub.
    return str(text).replace("@", "@​")


def without_attribution(text: str) -> str:
    """/create-pr forbids any AI attribution; a model can still write one, so the host drops those lines."""
    return "\n".join(line for line in text.splitlines() if not ATTRIBUTION.search(line))


HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


class ScopeRefused(RuntimeError):
    """The patch reaches outside its plan or onto a guarded path. The message names the path and holds no secret."""

    public = True  # github.describe() may show the message as it is


def patch_paths(patch: str) -> set[str]:
    """Both paths of every file header. A header the pattern cannot read (a quoted name, a space) is kept whole, so
    it can never match the plan and the scope check fails closed. commit_patch() checks git's own list again."""
    paths = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        header = HEADER.match(line)
        if header:
            paths.update(header.groups())
        else:
            paths.add(f"<unreadable diff header: {line[11:80]}>")
    return paths


def scope_error(paths: set[str], plan: dict) -> str | None:
    # Plan entries can carry a note ("path (new)"), so only the first word is the path.
    allowed = {entry.split()[0] for entry in plan.get("files", []) if entry.strip()}
    for path in sorted(paths):
        if path.startswith(GUARDED) or ENV_FILE.search(path):
            return f"the patch changes {path}, a path the bot must never change"
        if path not in allowed:
            return f"the patch changes {path}, which the approved plan does not list"
    return None


def git(cwd: Path, *args: str) -> None:
    # No hooks: the tree holds a model's patch, and a hook would run its code on the host.
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, check=True, capture_output=True, text=True)


def staged_paths(repo: Path) -> set[str]:
    """Every path the applied patch changes, from git itself. --no-renames lists both sides of a rename."""
    done = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "diff", "--cached", "--name-only", "--no-renames",
                           "-z", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    return {path for path in done.stdout.split("\0") if path}


def commit_patch(me: dict, ref: str, branch: str, patch: str, message: str, plan: dict) -> str:
    """Applies the patch on `ref` as `branch`, checks the scope on git's own list of the changed paths, commits as the
    host account and pushes. Returns the new commit sha. Raises ScopeRefused before any push when the scope fails."""
    header = git_auth()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (Path(tmp) / "fix.patch").write_text(patch)
        git(repo, "init", "-q")
        git(repo, "remote", "add", "origin", f"https://github.com/{REPO}.git")
        git(repo, "-c", header, "fetch", "-q", "--depth", "1", "origin", ref)
        git(repo, "checkout", "-q", "-b", branch, "FETCH_HEAD")
        git(repo, "apply", "--index", "--binary", str(Path(tmp) / "fix.patch"))
        # The patch text is a model's output: the paths git applied are the ones that count, not the ones it names.
        if error := scope_error(staged_paths(repo), plan):
            raise ScopeRefused(error)
        git(repo, "-c", f"user.name={me['login']}", "-c", f"user.email={me['id']}+{me['login']}@users.noreply.github.com",
            "commit", "-q", "-m", message)
        git(repo, "-c", header, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def open_pull_request(service: str, issue: str, base_sha: str, branch: str, patch: str, title: str, body: str,
                      plan: dict) -> dict:
    """Pushes the patch on commit `base_sha` as `branch`, then opens the PR against the base branch. Returns {"pr", "url"}.

    The caller checks the scope on the patch text first; commit_patch() checks it again on what git applied.
    Reviewers, assignee and labels are best effort: the PR is open already.
    """
    me = github("user")
    commit_patch(me, base_sha, branch, patch, f"{title}\n\nCloses #{issue}.", plan)
    pr = github(f"repos/{REPO}/pulls", method="POST",
                body={"title": title, "head": branch, "base": config.BASE_BRANCH, "body": body})
    # One call each, so a refused team request (GitHub answers 422 for a team with no access) still requests the people.
    for reviewers in ({"reviewers": PR_REVIEWERS}, {"team_reviewers": PR_TEAM_REVIEWERS}):
        if not next(iter(reviewers.values())):
            continue
        try:
            github(f"repos/{REPO}/pulls/{pr['number']}/requested_reviewers", method="POST", body=reviewers)
        except Exception as error:
            # GitHub's message says why, and it holds no secret.
            reason = error.read().decode(errors="replace")[:300] if isinstance(error, urllib.error.HTTPError) else ""
            log.warning("could not request %s on PR #%s: %s %s", reviewers, pr["number"], describe(error), reason)
            axiom.emit(service, f"{service}.pr.reviewers_failed", f"reviewer request failed on PR #{pr['number']}",
                       f"pr-{pr['number']}", level="warn", module="pr", issue=int(issue), pr=pr["number"],
                       errorType=type(error).__name__, errorMessage=f"{describe(error)} {reason}".strip())
    try:
        github(f"repos/{REPO}/issues/{pr['number']}/assignees", method="POST", body={"assignees": [me["login"]]})
    except Exception as error:
        log.warning("could not assign PR #%s: %s", pr["number"], describe(error))
    try:
        if labels := labels_for(patch_paths(patch), {label["name"] for label in github(f"repos/{REPO}/labels?per_page=100")}):
            github(f"repos/{REPO}/issues/{pr['number']}/labels", method="POST", body={"labels": labels})
    except Exception as error:
        log.warning("could not label PR #%s: %s", pr["number"], describe(error))
    return {"pr": pr["number"], "url": pr["html_url"]}


def labels_for(paths: set[str], existing: set[str]) -> list[str]:
    """The labels these files earn, kept only when the repository has them: docs, dependencies and the package labels.

    A package label is `<root>/<package>` for each root in LEGION_PACKAGE_ROOTS, as `apps/web` for `apps/web/src/a.ts`.
    """
    labels = set()
    for path in paths:
        parts = path.split("/")
        if path.startswith("docs/"):
            labels.add("docs")
        elif parts[-1] in config.LOCKFILES:
            labels.add("dependencies")
        elif len(parts) > 2 and parts[0] in config.PACKAGE_ROOTS:
            labels.add(f"{parts[0]}/{parts[1]}")
    return sorted(label for label in labels if label in existing)
