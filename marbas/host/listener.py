#!/usr/bin/env python3
"""Marbas Slack listener: a bug thread gets a plan with Approve / Reject buttons, and an approved plan gets the fix.

Runs on the VM host as a systemd service, with no model: it only starts sandbox runs (`run.sh`) and posts their results.
Only an engineer (LEGION_TEAM), mapped to Slack through LEGION_SLACK_USER_MAP, can approve, reject or give feedback.

A thread starts when a trusted poster (MARBAS_TRIGGER_USERS, or an engineer) posts a link to an open `bug` issue of
LEGION_REPO in a watched channel. With MARBAS_TRIGGER_TEXT set, the thread parent must hold that text and come from the
same poster, which fits a form or a workflow bot that opens the issue and replies with its link.
"""

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import axiom  # noqa: E402
import config  # noqa: E402
import events  # noqa: E402
import inbox  # noqa: E402
import memory  # noqa: E402
import paths  # noqa: E402
import pr  # noqa: E402
from github import REPO, SECRETS, describe, github, team_logins  # noqa: E402
from pull_request import patch_paths  # noqa: E402
from sandbox_run import sandbox_run  # noqa: E402
from state import STATE, load, open_threads, save  # noqa: E402
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

SERVICE = "marbas"  # the Axiom `service`
RUN_SH = str(paths.REPO_DIR / "marbas/sandbox/run.sh")
RUNS = paths.workspace("marbas") / "runs"
CHANNELS = set(config.items("MARBAS_CHANNELS"))
# The Slack user ids that may start a thread: the bot or app that posts new bug reports. Empty: any engineer.
TRIGGER_USERS = set(config.items("MARBAS_TRIGGER_USERS"))
# In a test channel an engineer may start a thread by hand even when TRIGGER_USERS is set.
TEST_CHANNELS = set(config.items("MARBAS_TEST_CHANNELS"))
# When set, the thread parent must hold this text, as "New bug report:" from a bug form.
TRIGGER_TEXT = config.text("MARBAS_TRIGGER_TEXT")
# Slack wraps a link as <url> or <url|label>.
ISSUE_LINK = re.compile(rf"<https://github\.com/{re.escape(config.REPO)}/issues/(\d+)[|>]")
ENGINEERS_TTL = 600
INBOX_SECONDS = 2  # the hooks service writes the GitHub events here; reading it is local
TIMER_SECONDS = 30
HEARTBEAT_SECONDS = 300
RETRY_SECONDS = 300
# The kill switch: false stops new bug threads. Set it in legion.env, then run ./setup.sh marbas.
ENABLED = config.flag("MARBAS_ENABLED", True)
# Shadow mode: set, a bug in another channel runs in a mirror thread here, and the real thread gets nothing.
SHADOW_CHANNEL = config.text("MARBAS_SHADOW_CHANNEL")
# false (today): a Reject means the engineer takes the bug. true: the bot asks what to change and plans again.
REPLAN_ON_REJECT = config.flag("MARBAS_REPLAN_ON_REJECT", False)
PROVE_DELAYS = [3600, 6 * 3600, 6 * 3600]  # after the deploy, then after each "not yet"
# The Slack app may post for other tools too, so every Marbas message says who it is.
SIGNATURE = ":lion_face: *Marbas*"
HANDOFF = "*A human takes it from here.*"
ONCALL = config.ONCALL  # who a hand-off pings, such as <!subteam^S0123>; empty pings nobody
PLAN_SECTIONS = [("found", "What I found"), ("cause", "What was causing it"), ("fix", "The fix"),
                 ("proof", "How I will prove it")]
WORD_JOINER = chr(0x2060)  # invisible; placed after @ so Slack does not read a mention

log = logging.getLogger("marbas-slack")
web = WebClient(token=(SECRETS / "slack_bot_token").read_text().strip())
jobs: "queue.Queue[tuple[str, str]]" = queue.Queue()
PR_LOCK = threading.Lock()  # the event thread and the timer thread both move PR threads
engineers_cache: tuple[float, set[str]] = (0.0, set())
login_by_slack: dict[str, str] = {}  # names the engineer in a memory note


def engineers() -> set[str]:
    """Slack ids of the engineers. Fails closed: on any error, nobody is an engineer."""
    global engineers_cache
    if time.time() - engineers_cache[0] < ENGINEERS_TTL:
        return engineers_cache[1]
    try:
        # Read from the base branch, never from a PR branch, so a PR cannot add its author to the map.
        raw = github(f"repos/{REPO}/contents/{config.SLACK_USER_MAP}?ref={config.BASE_BRANCH}", "github_sandbox_token")
        slack_by_login = json.loads(urllib.request.urlopen(raw["download_url"], timeout=10).read())
        ids = {slack_by_login[login] for login in team_logins() if login in slack_by_login}
        login_by_slack.update({slack: login for login, slack in slack_by_login.items()})
    except Exception:
        log.exception("could not load the engineers")
        return set()
    engineers_cache = (time.time(), ids)
    return ids


def escape(text: str) -> str:
    # Plan text comes from a model that read untrusted input: escaping stops <!channel>, mentions and links.
    # Slack also links a bare @name (`@org/team` becomes a mention), so break each @.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("@", "@" + WORD_JOINER)


def say(thread: dict, text: str, blocks: list | None = None) -> str:
    if blocks is None:
        # A blank line between lines reads better in a thread than a wall of text.
        text = f"{SIGNATURE} · {text}".replace("\n", "\n\n")
        # Added after escape(), so this is the only mention that can ping anyone.
        if ONCALL:
            text = text.replace(HANDOFF, f"*A human takes it from here:* {ONCALL}")
    response = web.chat_postMessage(channel=thread["channel"], thread_ts=thread["ts"], text=text, blocks=blocks)
    return response["ts"]


def plan_blocks(thread: dict, plan: dict) -> tuple[str, list]:
    summary = plan.get("summary", {})
    if isinstance(summary, list):  # the skill before the four sections
        body = "\n".join(f"• {escape(line)}" for line in summary[:5])
    else:
        body = "\n\n".join(f"*{title}*\n{escape(summary[field])}" for field, title in PLAN_SECTIONS if summary.get(field))
    text = f"{SIGNATURE} · 📋 *Plan for `#{thread['issue']}`, round {thread['round']}*\n\n{body}\n\n"
    text += f"_Files: {len(plan.get('files', []))}, in {', '.join(f'`{escape(p)}`' for p in plan.get('packages', []))}._"
    value = f"{thread['issue']}:{thread['round']}"
    return text, [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "approve", "style": "primary", "value": value,
             "text": {"type": "plain_text", "text": "Approve"}},
            {"type": "button", "action_id": "reject", "style": "danger", "value": value,
             "text": {"type": "plain_text", "text": "Reject"}},
        ]},
    ]


def run(issue: str, args: list[str], key: str) -> dict:
    """Runs one sandbox run and returns its result.json."""
    result, run_dir = sandbox_run(RUN_SH, [issue, *args], RUNS, f"{issue}-*")
    mode = args[0].lstrip("-") if args else "full"
    axiom.emit(SERVICE, "marbas.run.finished", f"{mode} run for #{issue}: {result.get('outcome')}", key,
               level="warn" if result.get("outcome") == "blocked" else "info", issue=int(issue), mode=mode,
               outcome=result.get("outcome"), score=result.get("score"), label=result.get("label"),
               **(axiom.run_fields(run_dir) if run_dir else {}))
    return result


def remember(thread: dict, kind: str, words: str, engineer: str, lesson: str, source: str) -> None:
    """Saves one memory note. A failure here must not stop the bug, so it only logs."""
    try:
        packages = json.loads(Path(thread["plan_file"]).read_text())["plan"].get("packages", [])
        note = memory.write_note(kind, thread["issue"], packages, login_by_slack.get(engineer, engineer), words, lesson,
                                 source)
        log.info("memory note %s", note.name)
        axiom.emit(SERVICE, "marbas.lesson.saved", f"lesson from {kind} on #{thread['issue']}", f"{thread['channel']}-{thread['ts']}",
                   issue=int(thread["issue"]), kind=kind, packages=",".join(packages))
    except Exception:
        log.exception("could not write the memory note for #%s", thread["issue"])


def after_plan(key: str, thread: dict, result: dict) -> None:
    result_file = STATE / key / f"plan{thread['round']}.json"
    result_file.write_text(json.dumps(result))
    if thread.get("feedback_text") and result.get("lesson"):
        remember(thread, "plan-reject", thread.pop("feedback_text"), thread.pop("feedback_by", ""), result["lesson"],
                 f"Plan round {thread['round'] - 1} for #{thread['issue']}, rejected in the Slack thread.")
    if result.get("outcome") in ("plan", "plan changed") and result.get("plan", {}).get("files"):
        thread.update(stage="awaiting_approval", plan_file=str(result_file))
        text, blocks = plan_blocks(thread, result["plan"])
        if result.get("outcome") == "plan changed":
            say(thread, f"📋 The fix needs a change to the approved plan: {escape(result.get('reason', ''))}")
        thread["plan_ts"] = say(thread, text, blocks)
    else:
        thread["stage"] = "needs_human"
        say(thread, f"🚫 No plan for `#{thread['issue']}`: {escape(result.get('outcome', 'blocked'))}. "
                    f"{escape(result.get('reason', ''))} *A human takes it from here.*")
    save(key, thread)


def work() -> None:
    while True:
        key, action = jobs.get()
        thread = load(key)
        try:
            if action == "plan":
                args = ["--plan"]
                if thread.get("feedback_file"):
                    args += ["--feedback", thread["feedback_file"]]
                after_plan(key, thread, run(thread["issue"], args, key))
            elif action == "ci":
                ci_file = STATE / key / f"ci{thread['ci_attempts']}.json"
                ci_file.write_text(json.dumps(thread.get("ci_jobs", []), indent=2))
                args = ["--fix-ci", str(ci_file), "--plan-file", thread["plan_file"], "--branch", thread["branch"]]
                after_ci(key, thread, run(thread["issue"], args, key))
            elif action == "review":
                review_file = STATE / key / f"review{thread['review_attempts']}.json"
                review_file.write_text(json.dumps(thread["review"], indent=2))
                args = ["--address-review", str(review_file), "--plan-file", thread["plan_file"],
                        "--branch", thread["branch"]]
                after_review(key, thread, run(thread["issue"], args, key))
            elif action == "push_review":
                # The wider scope is approved: the same patch goes to the PR branch, with no new run.
                run_dir = Path(thread["run_dir"])
                push_review(key, thread, json.loads((run_dir / "result.json").read_text()), run_dir)
            elif action == "publish":
                # The wider scope is approved: the same patch opens the PR, with no new run.
                publish(key, thread, json.loads((Path(thread["run_dir"]) / "result.json").read_text()))
            elif action == "prove":
                prove(key, thread, run(thread["issue"], ["--prove", str(thread["pr"]), "--since", thread["live_at"]], key))
            elif action == "fix":
                result = run(thread["issue"], ["--from-plan", thread["plan_file"]], key)
                if result.get("outcome") == "plan changed":
                    thread["round"] += 1
                    after_plan(key, thread, result)
                    continue
                if result.get("outcome") == "ready":
                    publish(key, thread, result)
                else:
                    thread["stage"] = "needs_human"
                    save(key, thread)
                    say(thread, f"The fix for `#{thread['issue']}` stopped: {escape(result.get('outcome', 'blocked'))}. "
                                f"{escape(result.get('reason', ''))} *A human takes it from here.*")
        except Exception as error:
            log.exception("job %s %s failed", key, action)
            axiom.emit(SERVICE, "marbas.job.failed", f"{action} job for #{thread.get('issue')} failed on the host", key,
                       level="error", issue=int(thread.get("issue", 0)), mode=action, errorType=type(error).__name__)
            thread["stage"] = "needs_human"
            save(key, thread)
            say(thread, "The run failed on the host. *A human takes it from here.*")


def publish(key: str, thread: dict, result: dict) -> None:
    """Hands a ready fix to the host side: the patch becomes a PR that an engineer approves on GitHub."""
    if thread.get("run_dir"):
        run_dir = Path(thread["run_dir"])
    else:
        run_dir = max(RUNS.glob(f"{thread['issue']}-*"), key=lambda path: path.stat().st_mtime)
    plan = json.loads(Path(thread["plan_file"]).read_text())["plan"]
    patch = run_dir / "fix.patch"
    if patch.exists() and (extra := pr.extra_files(patch_paths(patch.read_text()), plan)):
        ask_scope(key, thread, run_dir, extra)
        return
    try:
        opened = pr.open_pr(thread["issue"], run_dir, result, plan)
    except Exception as error:
        log.exception("could not open the PR for #%s", thread["issue"])
        opened = {"error": describe(error)}
    text = (f"🔧 The fix for `#{thread['issue']}` is ready: score `{result.get('score')}` "
            f"({escape(str(result.get('label')))}). Lint, tests and build are green.")
    if result.get("reason"):
        text += f"\n*Not proved:* {escape(result['reason'])}"
    if result.get("humanCheck"):
        text += f"\n*A human must check:* {escape(result['humanCheck'])}"
    if "error" in opened:
        thread["stage"] = "needs_human"
        text += f"\nI did not open a PR: {escape(opened['error'])}. *A human takes it from here.*"
    else:
        thread.update(stage="pr_open", pr=opened["pr"])
        text += f"\n*`PR #{opened['pr']}`:* {opened['url']}\n{pr.next_step_text()}"
    posted = say(thread, text)
    if thread.get("stage") == "pr_open":
        thread["pr_ts"] = posted
    save(key, thread)


def ask_scope(key: str, thread: dict, run_dir: Path, extra: list[str]) -> None:
    """The patch touched files the plan did not list: an engineer approves the wider scope, and the same patch goes on.
    Before the PR it opens the PR; on a review of the PR it is pushed to the PR branch."""
    files = "\n".join(f"• `{escape(path)}`" for path in extra)
    what, then = ((f"The changes for the review on `PR #{thread['pr']}` are", "push them")
                  if thread.get("pr") else (f"The fix for `#{thread['issue']}` is", "open the PR with these files"))
    text = (f"{SIGNATURE} · 📋 {what} ready, but they also change {len(extra)} "
            f"{'file' if len(extra) == 1 else 'files'} that the approved plan does not list:\n\n{files}\n\n"
            f"Approve to {then}. Reject, and a human takes it.")
    value = f"{thread['issue']}:{thread['round']}"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}},
              {"type": "actions", "elements": [
                  {"type": "button", "action_id": "scope_approve", "style": "primary", "value": value,
                   "text": {"type": "plain_text", "text": "Approve the wider scope"}},
                  {"type": "button", "action_id": "scope_reject", "style": "danger", "value": value,
                   "text": {"type": "plain_text", "text": "Reject"}}]}]
    thread.update(stage="awaiting_scope", scope_extra=extra, run_dir=str(run_dir))
    thread["scope_ts"] = say(thread, text, blocks)
    save(key, thread)


def on_scope(key: str, thread: dict, user: str, approved: bool) -> None:
    verb = "Approved" if approved else "Rejected"
    files = "\n".join(f"• `{escape(path)}`" for path in thread["scope_extra"])
    decided = (f"{SIGNATURE} · Extra files for `#{thread['issue']}`:\n\n{files}\n\n"
               f"{'✅' if approved else '❌'} *{verb}* by <@{user}>")
    web.chat_update(channel=thread["channel"], ts=thread["scope_ts"], text=decided,
                    blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": decided}}])
    react(thread, thread["scope_ts"], "heavy_check_mark" if approved else "x")
    axiom.emit(SERVICE, "marbas.scope.decided", f"wider scope for #{thread['issue']}: {verb.lower()}", key,
               issue=int(thread["issue"]), decision=verb.lower(), extraFiles=len(thread["scope_extra"]),
               engineer=login_by_slack.get(user, user))
    if not approved:
        thread["stage"] = "needs_human"
        save(key, thread)
        undone = "push the review changes" if thread.get("pr") else "open a PR"
        say(thread, f"The wider scope was rejected, so I will not {undone}.{take_over(thread, user)} {HANDOFF}")
        return
    plan_file = Path(thread["plan_file"])
    data = json.loads(plan_file.read_text())
    data["plan"]["files"] = data["plan"]["files"] + thread["scope_extra"]
    data["scopeExtension"] = {"files": thread["scope_extra"], "approvedBy": login_by_slack.get(user, user)}
    extended = plan_file.with_name(f"{plan_file.stem}-scope.json")
    extended.write_text(json.dumps(data))
    if thread.get("pr"):
        thread.update(stage="pushing_review", plan_file=str(extended))
        save(key, thread)
        jobs.put((key, "push_review"))
        return
    thread.update(stage="publishing", plan_file=str(extended))
    save(key, thread)
    jobs.put((key, "publish"))


def after_ci(key: str, thread: dict, result: dict) -> None:
    """A CI run ends in a pushed fix, a re-run of an unrelated failure, or a hand-off."""
    outcome = result.get("outcome")
    pr_ref = f"`PR #{thread['pr']}`"
    if outcome == "ready":
        run_dir = max(RUNS.glob(f"{thread['issue']}-*"), key=lambda path: path.stat().st_mtime)
        plan = json.loads(Path(thread["plan_file"]).read_text())["plan"]
        fix = str(result.get("ciFix") or "fix the red CI check")
        try:
            pushed = pr.push_fix(thread["branch"], run_dir, plan, f"fix: {fix}")
        except Exception as error:
            log.exception("could not push the CI fix for #%s", thread["issue"])
            pushed = {"error": describe(error)}
        if "error" in pushed:
            thread["stage"] = "needs_human"
            text = f"I fixed CI for {pr_ref} but could not push it: {escape(pushed['error'])}. {HANDOFF}"
        else:
            # A new commit voids the old approval, so the ready note may come again.
            thread.update(stage="pr_open", ready_told=False)
            text = (f"🔧 I pushed a fix for the red CI on {pr_ref}: {escape(fix)}\n"
                    "An engineer must approve the new commit.")
    elif outcome == "ci unrelated":
        why = escape(str(result.get("reason", "")))
        problem = pr.rerun_failed(thread.get("ci_jobs", []))
        if problem:
            thread["stage"] = "needs_human"
            text = (f"The red CI on {pr_ref} is not caused by this PR: {why}\n"
                    f"I could not re-run the failed jobs ({escape(problem)}). Re-run them on GitHub. {HANDOFF}")
        else:
            thread["stage"] = "pr_open"
            text = f"🔧 The red CI on {pr_ref} is not caused by this PR: {why}\nI re-ran the failed jobs."
    else:
        thread["stage"] = "needs_human"
        text = (f"I could not fix the red CI on {pr_ref}: `{escape(str(outcome))}`. "
                f"{escape(str(result.get('reason', '')))} {HANDOFF}")
    save(key, thread)
    say(thread, text)


def after_review(key: str, thread: dict, result: dict) -> None:
    """A review run ends in a pushed commit, a wider-scope question, or a hand-off to the reviewer."""
    outcome, reviewer = result.get("outcome"), thread["review"]["reviewer"]
    if outcome == "ready":
        run_dir = max(RUNS.glob(f"{thread['issue']}-*"), key=lambda path: path.stat().st_mtime)
        plan = json.loads(Path(thread["plan_file"]).read_text())["plan"]
        patch = run_dir / "fix.patch"
        if patch.exists() and (extra := pr.extra_files(patch_paths(patch.read_text()), plan)):
            ask_scope(key, thread, run_dir, extra)
            return
        push_review(key, thread, result, run_dir)
        return
    thread["stage"] = "needs_human"
    text = (f"I could not apply the review of `{reviewer}` on `PR #{thread['pr']}`: `{escape(str(outcome))}`. "
            f"{escape(str(result.get('reason', '')))}")
    if pr.assign_issue(thread["issue"], reviewer):
        text += f"\nI assigned `#{thread['issue']}` to `{reviewer}`."
    save(key, thread)
    say(thread, f"{text} {HANDOFF}")


def push_review(key: str, thread: dict, result: dict, run_dir: Path) -> None:
    """Pushes the review changes as a new commit, then asks the reviewer on GitHub to look again."""
    reviewer, pr_ref = thread["review"]["reviewer"], f"`PR #{thread['pr']}`"
    plan = json.loads(Path(thread["plan_file"]).read_text())["plan"]
    summary = str(result.get("reviewFix") or "apply the review")
    try:
        pushed = pr.push_fix(thread["branch"], run_dir, plan, f"fix: {summary}")
    except Exception as error:
        log.exception("could not push the review changes for #%s", thread["issue"])
        pushed = {"error": describe(error)}
    if "error" in pushed:
        thread["stage"] = "needs_human"
        text = f"I applied the review on {pr_ref} but could not push it: {escape(pushed['error'])}. {HANDOFF}"
    else:
        # A new commit voids the old approval, so the ready note may come again.
        thread.update(stage="pr_open", ready_told=False)
        text = f"🔧 I applied the review of `{reviewer}` on {pr_ref}: {escape(summary)}"
        if pr.answer_review(thread["pr"], reviewer, str(result.get("reviewReply") or summary), pushed["sha"],
                            thread["review_attempts"]):
            text += f"\nI asked `{reviewer}` to review the new commit."
        else:
            text += f"\n`{reviewer}` must review the new commit on GitHub."
    save(key, thread)
    say(thread, text)


def prove(key: str, thread: dict, result: dict) -> None:
    """Posts the production proof of a live fix, or schedules another look when users have not reached the flow yet."""
    outcome, production = result.get("outcome"), result.get("production") or {}
    evidence = ""
    if production.get("query"):
        evidence = (f"\n*Query:* `{escape(production['query'])}`\n*Before:* {escape(production.get('before', ''))}"
                    f"\n*After:* {escape(production.get('after', ''))}")
    summary = escape(production.get("summary") or result.get("reason") or "")
    thread["prove_tries"] = thread.get("prove_tries", 0) + 1
    if outcome == "not yet" and thread["prove_tries"] < len(PROVE_DELAYS):
        delay = PROVE_DELAYS[thread["prove_tries"]]
        thread.update(stage="proving", prove_at=time.time() + delay)
        text = f"🚀 Not enough use in production yet to prove the fix. I will look again in *{delay // 3600} hours*."
    elif outcome == "verified":
        thread["stage"] = "done"
        text = f"🚀 *Proved in production.* {summary}{evidence}"
    elif outcome == "no signal":
        thread["stage"] = "done"
        text = (f"🚀 This bug leaves no trail in telemetry, so I cannot prove the fix there. {summary}"
                f"\n*A human must check*{f' ({ONCALL})' if ONCALL else ''}: {escape(result.get('humanCheck', ''))}")
    elif outcome == "regressed":
        thread["stage"] = "needs_human"
        text = f"*The bug is still in production.* {summary}{evidence}\n*A human takes it from here.*"
    else:
        thread["stage"] = "needs_human"
        text = f"I could not prove the fix in production: `{escape(str(outcome))}`. {summary}\n*A human takes it from here.*"
    save(key, thread)
    say(thread, text)


def advance(key: str, event: dict | None = None) -> None:
    """Moves one open PR thread one step: merge after an engineer approves and CI is green, then watch the deploy."""
    with PR_LOCK:
        path = STATE / key / "thread.json"
        thread = json.loads(path.read_text())
        if thread.get("stage") not in events.WATCHED:
            return
        thread.pop("retry_at", None)
        try:
            moved = pr.step(thread)
        except Exception as error:
            cause = describe(error)
            log.warning("PR step for #%s failed: %s", thread["issue"], cause)
            # No poll: a failed step tries again once on a timer, or on the next GitHub event for the PR.
            thread["retry_at"] = time.time() + RETRY_SECONDS
            # Say it once per cause, so a stuck step is visible in the thread, not only in the log.
            if thread.get("last_error") != cause:
                thread["last_error"] = cause
                say(thread, f"I cannot check `PR #{thread['pr']}`: {escape(cause)}. I will try again in *5 minutes*.")
            save(key, thread)
            return
        thread.pop("last_error", None)
        if not moved:
            save(key, thread)
            return
        thread["stage"] = moved[0]
        text = moved[1]
        if moved[0] == "deployed":
            # Users need time to use the flow before telemetry can prove anything.
            thread.update(stage="proving", live_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          prove_at=time.time() + PROVE_DELAYS[0], prove_tries=0)
            text += "\nI will look for proof in production telemetry in *1 hour*."
        if thread.get("changes_by"):
            # The reviewer takes the bug only on a hand-off; while the bot applies the review, it stays with the bot.
            if moved[0] == "needs_human" and pr.assign_issue(thread["issue"], thread["changes_by"]):
                text += f"\nI assigned `#{thread['issue']}` to `{thread['changes_by']}`."
            body = thread.get("changes_body") or ""
            # No model on the host: the first line of the review is the lesson, the full review is kept below it.
            lesson = next((line.strip() for line in body.splitlines() if line.strip()), "See the review.")
            remember(thread, "pr-changes", body, thread.pop("changes_by"), lesson,
                     f"Review of PR #{thread['pr']} for #{thread['issue']}, changes requested.")
            thread.pop("changes_body", None)
        if (name := thread.pop("react", None)) is not None:
            react(thread, thread.get("pr_ts"), name)
        save(key, thread, sinceEventMs=inbox.since_ms(inbox.received_epoch(event)))
        say(thread, escape(text))
        if moved[0] == "ci_fixing":
            jobs.put((key, "ci"))
        if moved[0] == "review_fixing":
            jobs.put((key, "review"))


def watch_events() -> None:
    """GitHub events from the hooks service move the PR threads. This reads a local folder; it never polls GitHub."""
    while True:
        inbox.consume(SERVICE, lambda event: events.deliver(event, open_threads(), advance, event_failed),
                      lambda event, error: event_failed(None, event, error))
        time.sleep(INBOX_SECONDS)


def event_failed(key: str | None, event: dict, error: Exception) -> None:
    """drain() already removed the event file, so the event goes into the log in full: a person can replay it."""
    log.exception("the %s event for #%s failed", event.get("event"), event.get("number"))
    axiom.emit(SERVICE, "marbas.event.failed", f"the {event.get('event')} event failed", key or "inbox",
               level="error", thread=key, event=event.get("event"), action=event.get("action"),
               number=event.get("number"), sha=event.get("sha"), delivery=event.get("delivery"),
               errorType=type(error).__name__, errorMessage=describe(error))


def timers() -> None:
    """Local clocks only: the heartbeat, the proof delay, the deploy timeouts and the retry of a failed step."""
    beat = 0.0
    while True:
        now = time.time()
        if now - beat > HEARTBEAT_SECONDS:
            # The silence monitor reads this: no heartbeat means no bug thread gets an answer.
            axiom.emit(SERVICE, "marbas.heartbeat", "listener alive", "heartbeat", queued=jobs.qsize())
            beat = now
        for path in STATE.glob("*/thread.json"):
            key = path.parent.name
            try:
                tick(key, now)
            except Exception as error:
                log.exception("the timer of %s failed", key)
                axiom.emit(SERVICE, "marbas.timer.failed", "a thread timer failed", key, level="error", thread=key,
                           errorType=type(error).__name__, errorMessage=describe(error))
        time.sleep(TIMER_SECONDS)


def tick(key: str, now: float) -> None:
    """One thread's clocks. The read and the write hold PR_LOCK, so the event thread never loses a stage it just moved."""
    with PR_LOCK:
        thread = load(key)
        if thread.get("stage") == "proving" and now >= thread["prove_at"]:
            thread["stage"] = "proving_run"
            save(key, thread)
            jobs.put((key, "prove"))
            return
        due = events.timeouts_due(thread, now, (pr.NO_DEPLOY_TIMEOUT, pr.DEPLOY_TIMEOUT))
        if due:
            thread["timeouts_checked"] = thread.get("timeouts_checked", []) + due
            save(key, thread)
        retry = bool(thread.get("retry_at")) and now >= thread["retry_at"]
    if due or retry:
        advance(key)  # takes PR_LOCK itself


def take_over(thread: dict, slack_user: str) -> str:
    """Assigns the issue to the engineer who rejected; returns the line for the thread, or nothing."""
    login = login_by_slack.get(slack_user)
    if login and pr.assign_issue(thread["issue"], login):
        return f"\nI assigned `#{thread['issue']}` to `{login}`."
    return ""


def react(thread: dict, ts: str | None, name: str) -> None:
    """The team's reaction convention: 👀 taken, ✔️ approved, ❌ rejected. ✅ (resolved) belongs to the engineer."""
    if not ts:
        return
    try:
        web.reactions_add(channel=thread["channel"], name=name, timestamp=ts)
    except Exception as error:
        # Needs the reactions:write scope; a missing reaction must not stop the bug.
        response = getattr(error, "response", None)
        log.warning("could not react %s on #%s: %s", name, thread["issue"], response.get("error") if response else error)


def claim(thread: dict) -> None:
    """👀 on the bug message says the bot took it. In shadow mode that is the mirror, never the real bug channel."""
    react(thread, thread["ts"], "eyes")


def mirror(source_key: str, thread: dict, parent: dict) -> tuple[str, dict]:
    """Shadow mode: the bug thread gets nothing, and the whole conversation runs in a mirror thread in SHADOW_CHANNEL."""
    link = web.chat_getPermalink(channel=thread["channel"], message_ts=thread["ts"])["permalink"]
    posted = web.chat_postMessage(channel=SHADOW_CHANNEL,
                                  text=f"{SIGNATURE} · 👀 `Shadow run` for {link}\n\n{escape(parent.get('text', ''))}")
    # The stub keeps a second trigger reply in the real thread from starting a second run.
    save(source_key, {"issue": thread["issue"], "stage": "mirrored", "mirror": f"{SHADOW_CHANNEL}-{posted['ts']}"})
    key = f"{SHADOW_CHANNEL}-{posted['ts']}"
    return key, {**thread, "channel": SHADOW_CHANNEL, "ts": posted["ts"], "source": link}


def on_message(event: dict) -> None:
    if event.get("channel") not in CHANNELS or event.get("bot_id") == BOT_ID:
        return
    # A trigger app posts as a bot, so a bot_message counts; an edit, a delete or a join does not.
    if event.get("subtype") not in (None, "bot_message"):
        return
    # With no TRIGGER_TEXT, a link posted at the top of the channel starts a thread on that message itself.
    parent_ts = event.get("thread_ts") or ("" if TRIGGER_TEXT else event.get("ts"))
    if not parent_ts:
        return
    key = f"{event['channel']}-{parent_ts}"
    thread = load(key)
    user = event.get("user", "")

    if not thread or thread.get("stage") == "skipped":  # a skipped thread may try again once the open PR closes
        match = ISSUE_LINK.search(event.get("text", ""))
        trusted = user in TRIGGER_USERS or ((not TRIGGER_USERS or event["channel"] in TEST_CHANNELS) and user in engineers())
        if not match or not trusted:
            log.info("no trigger: link=%s trusted=%s", bool(match), trusted)
            return
        if TRIGGER_TEXT:
            parent = web.conversations_replies(channel=event["channel"], ts=parent_ts, limit=1)["messages"][0]
            if parent.get("user") != user or TRIGGER_TEXT not in parent.get("text", ""):
                log.info("no trigger: the thread parent is not a bug report from the same user")
                return
        else:
            parent = {"text": event.get("text", ""), "user": user}
        issue = github(f"repos/{REPO}/issues/{match.group(1)}", "github_sandbox_token")
        labels = {label["name"] for label in issue.get("labels", [])}
        if issue.get("state") != "open" or "bug" not in labels or any(n.startswith("legion:") for n in labels):
            log.info("no trigger: #%s is %s with labels %s", match.group(1), issue.get("state"), sorted(labels))
            return
        thread = {"issue": match.group(1), "channel": event["channel"], "ts": parent_ts, "round": 1, "stage": "planning"}
        if SHADOW_CHANNEL and event["channel"] != SHADOW_CHANNEL:
            key, thread = mirror(key, thread, parent)
        if not ENABLED:
            # The kill switch stops new bugs only; threads already in flight keep their PR watch and proof.
            thread["stage"] = "skipped"
            save(key, thread)
            say(thread, f"Marbas is off, so I will not start on `#{thread['issue']}`. {HANDOFF}")
            return
        if open_prs := pr.open_prs_for(thread["issue"]):
            thread["stage"] = "skipped"
            save(key, thread)
            names = ", ".join(f"`PR #{n}`" for n in open_prs)
            say(thread, f"👀 {names} already works on `#{thread['issue']}`, so I will not start. "
                        "If that PR does not fix the bug, post the issue link again after it closes.")
            return
        save(key, thread)
        claim(thread)
        say(thread, f"👀 I am on `#{thread['issue']}`. I will post a plan here for an engineer to approve.")
        jobs.put((key, "plan"))
        return

    if thread.get("stage") == "awaiting_feedback" and user in engineers():
        previous = json.loads(Path(thread["plan_file"]).read_text())["plan"]
        feedback = STATE / key / f"feedback{thread['round']}.md"
        feedback.write_text(f"# Rejected plan\n\n```json\n{json.dumps(previous, indent=2)}\n```\n\n"
                            f"# Engineer feedback ({user})\n\n{event.get('text', '')}\n")
        thread.update(stage="planning", feedback_file=str(feedback), round=thread["round"] + 1,
                      feedback_text=event.get("text", ""), feedback_by=user)
        save(key, thread)
        say(thread, "📋 Thanks. I will post a new plan.")
        jobs.put((key, "plan"))


def on_action(payload: dict) -> None:
    action = payload["actions"][0]
    channel, user = payload["channel"]["id"], payload["user"]["id"]
    parent_ts = payload["message"].get("thread_ts", "")
    key = f"{channel}-{parent_ts}"
    thread = load(key)
    current = f"{thread.get('issue')}:{thread.get('round')}"
    if user not in engineers():
        web.chat_postEphemeral(channel=channel, user=user, thread_ts=parent_ts,
                               text="Only the engineering team can approve or reject a plan.")
        return
    if action["action_id"] in ("scope_approve", "scope_reject"):
        if not thread or thread.get("stage") != "awaiting_scope" or action["value"] != current:
            web.chat_postEphemeral(channel=channel, user=user, thread_ts=parent_ts, text="This request is no longer open.")
            return
        on_scope(key, thread, user, action["action_id"] == "scope_approve")
        return
    if not thread or thread.get("stage") != "awaiting_approval" or action["value"] != current:
        web.chat_postEphemeral(channel=channel, user=user, thread_ts=parent_ts, text="This plan is no longer open.")
        return
    verb = "Approved" if action["action_id"] == "approve" else "Rejected"
    text, _ = plan_blocks(thread, json.loads(Path(thread["plan_file"]).read_text())["plan"])
    now = int(time.time())
    # Slack shows <!date> in each reader's timezone; the text after | is the fallback.
    when = f"<!date^{now}^{{date_short_pretty}} at {{time}}|{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))}>"
    decided = f"{text}\n\n{'✅' if verb == 'Approved' else '❌'} *{verb}* by <@{user}> on {when}"
    web.chat_update(channel=channel, ts=thread["plan_ts"], text=decided,
                    blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": decided}}])
    thread["decided_by"] = user
    react(thread, thread["plan_ts"], "heavy_check_mark" if verb == "Approved" else "x")
    axiom.emit(SERVICE, "marbas.plan.decided", f"plan round {thread['round']} for #{thread['issue']}: {verb.lower()}", key,
               issue=int(thread["issue"]), decision=verb.lower(), round=thread["round"],
               engineer=login_by_slack.get(user, user))
    if verb == "Approved":
        thread["stage"] = "fixing"
        save(key, thread)
        say(thread, "🔧 I will fix it inside this plan, then run lint, tests and build.")
        jobs.put((key, "fix"))
    elif REPLAN_ON_REJECT:
        thread["stage"] = "awaiting_feedback"
        save(key, thread)
        say(thread, f"📋 What must change in the plan? Reply in this thread.{take_over(thread, user)}")
    else:
        # The engineer who rejects takes the bug; the bot stops here.
        thread["stage"] = "taken"
        save(key, thread)
        say(thread, f"<@{user}> took the bug, so I stop here.{take_over(thread, user)}")


def handle(client: SocketModeClient, request: SocketModeRequest) -> None:
    # Ack first: Slack retries anything not acked in 3 seconds.
    client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
    event = request.payload.get("event", {})
    log.info("%s %s channel=%s thread=%s subtype=%s", request.type, event.get("type") or request.payload.get("type"),
             event.get("channel"), event.get("thread_ts"), event.get("subtype"))
    try:
        if request.type == "events_api" and request.payload["event"].get("type") == "message":
            on_message(request.payload["event"])
        elif request.type == "interactive" and request.payload.get("type") == "block_actions":
            on_action(request.payload)
    except Exception:
        log.exception("could not handle a %s request", request.type)


def recover() -> None:
    """A restart loses the running job, so it goes back in the queue, and its thread is told."""
    for path in STATE.glob("*/thread.json"):
        thread = json.loads(path.read_text())
        if thread.get("stage") == "proving_run":
            # The proof only reads telemetry, so a lost run is simply tried again.
            subprocess.run(["docker", "rm", "-f", f"marbas-run-{thread['issue']}"], capture_output=True)
            thread.update(stage="proving", prove_at=time.time())
            save(path.parent.name, thread)
        if thread.get("stage") in ("ci_fixing", "review_fixing"):
            subprocess.run(["docker", "rm", "-f", f"marbas-run-{thread['issue']}"], capture_output=True)
            jobs.put((path.parent.name, "ci" if thread["stage"] == "ci_fixing" else "review"))
        if thread.get("stage") == "publishing":
            jobs.put((path.parent.name, "publish"))
        if thread.get("stage") == "pushing_review":
            jobs.put((path.parent.name, "push_review"))
        if thread.get("stage") in ("planning", "fixing"):
            # A stop kills run.sh but not its detached container, which would run on with nobody to read it.
            subprocess.run(["docker", "rm", "-f", f"marbas-run-{thread['issue']}"], capture_output=True)
            # Safe to repeat: a plan only reads, and a fix writes nothing to GitHub before the host opens the PR.
            jobs.put((path.parent.name, "plan" if thread["stage"] == "planning" else "fix"))
            say(thread, f"🔧 The listener restarted during the run for `#{thread['issue']}`. I will run it again.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    BOT_ID = web.auth_test()["bot_id"]
    STATE.mkdir(parents=True, exist_ok=True)
    recover()
    threading.Thread(target=work, daemon=True).start()
    threading.Thread(target=watch_events, daemon=True).start()
    threading.Thread(target=timers, daemon=True).start()
    socket = SocketModeClient(app_token=(SECRETS / "slack_app_token").read_text().strip(), web_client=web)
    socket.socket_mode_request_listeners.append(handle)
    socket.connect()
    log.info("listening on %s", ", ".join(sorted(CHANNELS)))
    threading.Event().wait()
