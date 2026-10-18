#!/usr/bin/env python3
"""Valac's patrol on the Legion host. valac-patrol.timer starts it; no model runs here.

One run: read the merges and rejects from the inbox, run /valac-patrol in the sandbox, open one issue per new finding,
then, when VALAC_CAN_OPEN_PR is true, run /triage-bug on that issue and open the PR that closes it.
Design: docs/VALAC.md.
"""

import logging
import re
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import axiom  # noqa: E402
import config  # noqa: E402
import inbox  # noqa: E402
import jsonstore  # noqa: E402
import paths  # noqa: E402
from github import REPO, describe, github, team_logins  # noqa: E402
from pull_request import labels_for, no_mention, open_pull_request, patch_paths, scope_error, without_attribution  # noqa: E402
from sandbox_run import sandbox_run  # noqa: E402

import issue_text  # noqa: E402
import ledger  # noqa: E402
import notify  # noqa: E402

SERVICE = "valac"  # the Axiom `service`
ENABLED = config.flag("VALAC_ENABLED", False)
# false: the patrol opens issues only, and the team closes the wrong ones while it learns. true: it also opens PRs.
CAN_OPEN_PR = config.flag("VALAC_CAN_OPEN_PR", False)
WORKSPACE = paths.workspace(SERVICE)
STORE = WORKSPACE / "findings"
RUNS = WORKSPACE / "runs"
RUN_SH = str(paths.REPO_DIR / "valac/sandbox/run.sh")
OWNER_DAYS = 60

log = logging.getLogger(SERVICE)


def run(args: list[str], trace: str) -> dict:
    """One sandbox run; returns its result."""
    mode = next((a.lstrip("-") for a in args if a.startswith("--")), "run")
    result, run_dir = sandbox_run(RUN_SH, args, RUNS, "patrol-*" if mode == "patrol" else f"{args[0]}-*")
    totals = axiom.run_fields(run_dir) if run_dir else {}
    axiom.emit(SERVICE, "valac.run.finished", f"{mode} run: {result.get('outcome', 'done')}", trace,
               level="warn" if result.get("outcome") == "blocked" else "info", module="valac", mode=mode,
               outcome=result.get("outcome"), **totals)
    return {**result, "totals": totals} if isinstance(result, dict) else result


def say(text: str, trace: str) -> None:
    """Posts to Valac's Slack channel. A failed post is a log line: Slack never stops a run."""
    try:
        notify.post(text)
    except Exception as error:
        # The type only: an HTTP error can carry the webhook URL, which is a secret.
        axiom.emit(SERVICE, "valac.slack.failed", "the Slack post failed", trace, level="warn",
                   module="valac", errorType=type(error).__name__)


def apply_inbox() -> None:
    """Moves the findings on from every merge and close in the inbox. One failing event never drops the others."""
    inbox.consume(SERVICE, apply_event, event_failed)


def event_failed(event: dict, error: Exception) -> None:
    """drain() already removed the event file, so the event goes into the log in full: a person can replay it."""
    axiom.emit(SERVICE, "valac.event.failed", f"the {event.get('event')} event failed", "inbox", level="error",
               module="valac", event=event.get("event"), action=event.get("action"), number=event.get("number"),
               delivery=event.get("delivery"), errorType=type(error).__name__, errorMessage=describe(error))


def apply_event(event: dict) -> None:
    """A merge, or an issue closed as completed, moves a finding on. Any other close snoozes it."""
    number = event.get("number")
    record = ledger.by_number(STORE, number) if number else None
    if not record or event.get("action") != "closed" or record.get("state") not in ledger.ACTIVE:
        return
    if event["event"] == "pull_request":
        pr = github(f"repos/{REPO}/pulls/{number}")
        merged = bool(pr.get("merged"))
        reason = "the PR was merged" if merged else "the PR was closed without a merge"
    else:
        issue = github(f"repos/{REPO}/issues/{number}")
        completed = issue.get("state_reason") == "completed"
        # A merged PR closes its issue as completed: the pull_request event already moved the finding.
        if completed and record.get("pr"):
            return
        # With no Valac PR, completed means a person fixed it: a right finding, not a reject.
        merged, reason = completed, f"the issue was closed as {issue.get('state_reason') or 'closed'}"
    ledger.closed(STORE, record, merged, reason)
    axiom.emit(SERVICE, "valac.finding.closed", reason, record["fingerprint"], module="valac",
               issue=record.get("issue"), pr=record.get("pr"), merged=merged)


def open_prs_naming(event_type: str) -> list[int]:
    """Open PRs that name the event type. The sandbox cannot search PRs, so the host does it before it opens an issue."""
    query = urllib.parse.quote(f'repo:{REPO} is:pr is:open "{event_type}"')
    return [item["number"] for item in github(f"search/issues?q={query}&per_page=5").get("items", [])]


def owner(file: str) -> str | None:
    """The engineer with the most commits on the file in the last 60 days, so the issue never waits with no owner."""
    if not file:
        return None
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - OWNER_DAYS * 86400))
    commits = github(f"repos/{REPO}/commits?path={urllib.parse.quote(file)}&since={since}&per_page=100")
    engineers = team_logins()
    # A merge commit names who pressed the button, not who wrote the code.
    authors = Counter(c["author"]["login"] for c in commits
                      if len(c.get("parents") or []) < 2 and (c.get("author") or {}).get("login") in engineers)
    return authors.most_common(1)[0][0] if authors else None


def open_issue(finding: dict) -> tuple[int, str]:
    file = (finding.get("location") or {}).get("file", "")
    existing = {label["name"] for label in github(f"repos/{REPO}/labels?per_page=100")}
    body = {"title": issue_text.title(finding), "body": issue_text.body(finding),
            "labels": ["bug", *labels_for({file}, existing)]}
    login = ""
    try:
        if login := owner(file) or "":
            body["assignees"] = [login]
    except Exception as error:
        # No owner is a smaller loss than no issue.
        axiom.emit(SERVICE, "valac.owner.failed", describe(error), finding["fingerprint"], level="warn",
                   module="valac", file=file)
    return github(f"repos/{REPO}/issues", method="POST", body=body)["number"], login


def fix(finding: dict, issue: int) -> None:
    """/triage-bug plans, then fixes inside its own plan. The host checks the scope; the review happens on the PR."""
    store_record = ledger.load(STORE, finding["fingerprint"]) or {}
    planned = run([str(issue), "--plan"], finding["fingerprint"])
    if planned.get("outcome") != "plan" or not planned.get("plan", {}).get("files"):
        return record_outcome(store_record, planned)
    plan_file = RUNS / f"plan-{issue}.json"
    jsonstore.write(plan_file, planned, indent=2)
    result = run([str(issue), "--from-plan", str(plan_file)], finding["fingerprint"])
    if result.get("outcome") != "ready":
        return record_outcome(store_record, result)
    run_dir = max(RUNS.glob(f"{issue}-*"), key=lambda p: p.stat().st_mtime)
    patch_file, base_file = run_dir / "fix.patch", run_dir / "base.sha"
    if not patch_file.exists() or not base_file.exists():
        return record_outcome(store_record, {"outcome": "blocked", "reason": "the run left no patch"})
    patch, base = patch_file.read_text(), base_file.read_text().strip()
    if error := scope_error(patch_paths(patch), planned["plan"]):
        return record_outcome(store_record, {"outcome": "blocked", "reason": error})
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        return record_outcome(store_record, {"outcome": "blocked", "reason": "the base commit is not a sha"})
    title = pr_title(result, finding)
    body = without_attribution(no_mention(str(result.get("prBody") or "")))
    if f"Closes #{issue}" not in body:
        body += f"\n\nCloses #{issue}"
    body += f"\n\n_Valac opened this PR from its patrol. Score: {finding.get('score')}. An engineer reviews and merges it._"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().removeprefix("fix: ")).strip("-")[:40].strip("-")
    opened = open_pull_request(SERVICE, str(issue), base, f"fix/issue-{issue}-{slug}", patch, title, body, planned["plan"])
    store_record.update(state="pr_open", pr=opened["pr"])
    ledger.save(STORE, store_record)
    axiom.emit(SERVICE, "valac.pr.opened", f"PR #{opened['pr']} for #{issue}", finding["fingerprint"],
               module="valac", issue=issue, pr=opened["pr"])


def pr_title(result: dict, finding: dict) -> str:
    """The model's title, or the issue's cause; one line, no mention, a `fix` prefix, at most 72 characters."""
    raw = str(result.get("prTitle") or f"fix: {issue_text.headline(finding)}")
    title = no_mention(raw).splitlines()[0][:72]
    return title if title.startswith("fix") else f"fix: {title}"[:72]


def record_outcome(record: dict, result: dict) -> None:
    """A run that ends without a PR leaves the issue to a person; the reason goes to the log, not to GitHub."""
    record.update(lastOutcome=result.get("outcome"), lastReason=str(result.get("reason") or "")[:300])
    ledger.save(STORE, record)
    axiom.emit(SERVICE, "valac.fix.stopped", f"no PR for #{record.get('issue')}: {result.get('outcome')}",
               record.get("fingerprint", "unknown"), level="warn", module="valac", issue=record.get("issue"),
               outcome=result.get("outcome"))


def fix_failed(finding: dict, issue: int, error: Exception) -> None:
    """A crash in the fix flow, such as a failed push, is an error, not an expected stop."""
    record = ledger.load(STORE, finding["fingerprint"]) or {}
    record.update(lastOutcome="failed", lastReason=type(error).__name__)
    ledger.save(STORE, record)
    axiom.emit(SERVICE, "valac.fix.failed", f"the fix for #{issue} failed", finding["fingerprint"], level="error",
               module="valac", issue=issue, errorType=type(error).__name__, errorMessage=describe(error))


def patrol() -> None:
    apply_inbox()
    if (active := ledger.active_count(STORE)) >= ledger.MAX_ACTIVE:
        # Every place is taken, so no finding could open an issue: skip the cost of the run.
        trace = time.strftime("patrol-%Y%m%d")
        axiom.emit(SERVICE, "valac.run.skipped", f"{active} findings wait for people", trace,
                   module="valac", active=active)
        waiting = sorted(r["issue"] for r in ledger.every(STORE) if r.get("state") in ledger.ACTIVE and r.get("issue"))
        say(notify.patrol_skipped(waiting), trace)
        return
    trace = time.strftime("patrol-%Y%m%d")
    report = run(["--patrol"], trace)
    totals = report.get("totals") or {}
    cost = totals.get("costUsd")
    minutes = totals["durationMs"] / 60000 if totals.get("durationMs") else None
    if report.get("outcome") == "blocked":
        say(notify.patrol_blocked(str(report.get("reason") or "no reason"), cost, minutes), trace)
        return
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    window = report.get("window") or {}
    for finding in findings:
        finding["fingerprint"] = ledger.fingerprint(finding)
    opened = []
    for finding in ledger.select(findings, STORE):
        finding.setdefault("evidence", {}).update(windowStart=window.get("start"), windowEnd=window.get("end"))
        if prs := open_prs_naming(str(finding.get("eventType") or "")):
            # Not saved to the ledger: if the PR closes without a fix, the next patrol raises it again.
            axiom.emit(SERVICE, "valac.finding.covered", f"an open PR names it: {prs}", finding["fingerprint"],
                       module="valac", eventType=finding.get("eventType"), prs=prs)
            continue
        try:
            issue, assignee = open_issue(finding)
        except issue_text.Unsafe as error:
            axiom.emit(SERVICE, "valac.finding.refused", str(error), finding["fingerprint"], level="warn",
                       module="valac", eventType=finding.get("eventType"))
            continue
        ledger.opened(STORE, finding, issue)
        axiom.emit(SERVICE, "valac.issue.opened", f"issue #{issue}", finding["fingerprint"], module="valac",
                   issue=issue, score=finding.get("score"), findingClass=finding.get("class"),
                   dataset=finding.get("dataset"), owner=assignee)
        opened.append(issue)
        say(notify.issue_opened(issue, issue_text.headline(finding), finding, assignee),
            finding["fingerprint"])
        if CAN_OPEN_PR and not finding.get("humanOnly"):
            try:
                fix(finding, issue)
            except Exception as error:
                fix_failed(finding, issue, error)
    say(notify.patrol_done(len(findings), opened, cost, minutes), trace)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not ENABLED:
        log.info("VALAC_ENABLED is not true: no run")
        return
    STORE.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        patrol()
    except Exception as error:
        axiom.emit(SERVICE, "valac.run.failed", "the daily run failed", time.strftime("patrol-%Y%m%d"),
                   level="error", module="valac", errorType=type(error).__name__, errorMessage=describe(error))
        raise


if __name__ == "__main__":
    main()
