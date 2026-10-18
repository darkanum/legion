#!/usr/bin/env python3
"""Orobas, the PR review agent: reviews each ready head through a ladder of gates and posts as the host's bot account.

GitHub events reach it through its inbox (the hooks service writes it); it never polls GitHub. A head waits 90 s with
no new push, then Gate 0 runs here with no model, and the sandbox runs the rest of the ladder in a few minutes. This
host decides the review state (verdict.py) and renders the report (report.py). See docs/OROBAS.md.
"""

import json
import logging
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import axiom  # noqa: E402
import config  # noqa: E402
import github as gh  # noqa: E402
import jsonstore  # noqa: E402
import paths  # noqa: E402

import budget  # noqa: E402
import context  # noqa: E402
import gate0  # noqa: E402
import report  # noqa: E402
import stages  # noqa: E402
import verdict  # noqa: E402

REPO = gh.REPO
SERVICE = "orobas"  # the Axiom `service`, the systemd unit and the inbox name
RUN_SH = str(Path(__file__).resolve().parents[1] / "sandbox" / "run.sh")
WORK = paths.workspace(SERVICE)
STATE = WORK / "prs"
BUDGET = WORK / "budget.json"
QUIET_SECONDS = 90  # a push waits this long with no new push
INBOX_SECONDS = 2
HEARTBEAT_SECONDS = 300
MAX_FAILURES = 2  # per head; then the head waits for a new push or a review request
API_ERROR = "api_error"  # the Claude CLI terminal_reason for a vendor fault
RUN_TIMEOUT = stages.WALL_SECONDS + 180  # the container wall clock, plus docker logs and rm
REVIEW_ACTIONS = ("opened", "reopened", "ready_for_review", "synchronize", "review_requested")
# A body or label change can fix a Blocker with no push, so it re-runs only a PR the bot blocks.
UNBLOCK_ACTIONS = ("edited", "labeled", "unlabeled")
ACTIVE = ("queued", "running", "posting")
# The next run of a head: the whole ladder, or the light check after the first full review.
MODE_FULL, MODE_RECHECK = "full", "recheck"
# The kill switch: false stops new reviews. Set it in legion.env, then run ./setup.sh orobas.
ENABLED = config.flag("OROBAS_ENABLED", True)
# false while the team learns to trust it: the bot posts COMMENT where it would APPROVE.
CAN_APPROVE = config.flag("OROBAS_CAN_APPROVE", False)
# false: COMMENT where the bot would REQUEST_CHANGES. verdict.ALWAYS_BLOCK still blocks.
CAN_BLOCK = config.flag("OROBAS_CAN_BLOCK", False)
# The trunks: a PR on one of these is not stacked.
TRUNKS = (config.BASE_BRANCH, *gate0.EXTRA_BASES)

log = logging.getLogger(SERVICE)
jobs: "queue.Queue[tuple[str, int]]" = queue.Queue()
STATE_LOCK = threading.Lock()


class RunFailed(RuntimeError):
    """A run failure in our own words, with no command output, so Axiom can carry the text."""


class SeatLimit(RunFailed):
    """The seat answered 429: the PR waits until the reset, and it is not a failure."""

    def __init__(self, message: str, until: float):
        super().__init__(message)
        self.until = until


class ApiFailed(RunFailed):
    """The Claude API ended a gate: a vendor fault, so the event warns and does not page."""


def describe(error: Exception) -> str:
    return str(error) if isinstance(error, RunFailed) else gh.describe(error)


def load(number: int) -> dict:
    path = STATE / f"{number}.json"
    return migrate(json.loads(path.read_text())) if path.exists() else {}


def migrate(state: dict) -> dict:
    """Reads a state file from the polling version once: it had flat keys and no stage."""
    if "stage" in state or not state:
        return state
    if state.get("failed_sha") and state.get("failures", 0) >= MAX_FAILURES and state["failed_sha"] == state.get("seen_sha"):
        return {"sha": state["failed_sha"], "stage": "failed", "failures": state["failures"]}
    if state.get("reviewed_sha"):
        return {"sha": state["reviewed_sha"], "stage": "done", "reviewed_at": state.get("reviewed_at")}
    return {"sha": state.get("seen_sha"), "stage": "waiting", "seen_at": state.get("seen_at", 0.0), "failures": 0}


def save(number: int, state: dict) -> None:
    """Writes the state and emits `orobas.stage` when the stage changed, as Marbas does for its threads.

    The write is atomic: `prs_at()` reads every state file with no lock, so a reader must never see half a file.
    """
    path = STATE / f"{number}.json"
    before = migrate(json.loads(path.read_text())).get("stage") if path.exists() else None
    jsonstore.write(path, state)
    if state.get("stage") != before:
        axiom.emit(SERVICE, "orobas.stage", f"#{number} {before} -> {state.get('stage')}", trace(number, state.get("sha")),
                   level="warn" if state.get("stage") == "failed" else "info", pr=number, sha=state.get("sha"),
                   stageFrom=before, stageTo=state.get("stage"))


def update(number: int, expect_sha: str | None = None, **changes) -> dict | None:
    """With `expect_sha`, writes only while the state still holds that head: a push in between wins, and this returns None."""
    with STATE_LOCK:
        state = load(number)
        if expect_sha is not None and state.get("sha") != expect_sha:
            return None
        state.update(changes)
        save(number, state)
        return state


def trace(number: int, sha: str | None) -> str:
    return f"pr-{number}-{(sha or '')[:9]}"


def paged(path: str) -> list:
    items, page = [], 1
    while True:
        batch = gh.github(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def graphql(query: str, **variables) -> dict:
    return gh.github("graphql", method="POST", body={"query": query, "variables": variables}).get("data") or {}


THREADS = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  pullRequest(number: $number) { closingIssuesReferences(first: 10) { nodes { number } }
    reviewThreads(first: 100) { nodes { id isResolved path line
      comments(first: 30) { nodes { databaseId author { login } body } } } } } } }"""


def pr_graph(number: int) -> dict:
    owner, name = REPO.split("/")
    return (graphql(THREADS, owner=owner, name=name, number=number).get("repository") or {}).get("pullRequest") or {}


_me = ""


def me() -> str:
    global _me
    _me = _me or gh.github("user")["login"]
    return _me


def kill(number: int) -> None:
    subprocess.run(["docker", "rm", "-f", f"orobas-run-{number}"], capture_output=True)


# --- Events -------------------------------------------------------------------------------------------------------


def handle(event: dict, now: float) -> None:
    """Moves one PR on a GitHub event. The event only names the PR; the worker reads the facts from the API."""
    kind, action, number, sha = event.get("event"), event.get("action"), event.get("number"), event.get("sha")
    event_at = received(event, now)
    if kind == "pull_request" and isinstance(number, int):
        state = load(number)
        # Save the new state before kill(): the worker reads it to tell a superseded run from a failure.
        if action == "closed":
            if state:
                update(number, stage="closed")
            if state.get("stage") == "running":
                kill(number)
            return
        if action in UNBLOCK_ACTIONS:
            if state.get("event") == "REQUEST_CHANGES" and state.get("stage") in ("done", "stopped"):
                update(number, stage="waiting", seen_at=now - QUIET_SECONDS, failures=0, trigger=action, event_at=event_at,
                       **next_mode(state))
            return
        if action not in REVIEW_ACTIONS:
            return
        if action == "review_requested" and state.get("stage") in ("done", "stopped", "failed", "skipped"):
            # A person asked: the full ladder runs again.
            update(number, stage="waiting", seen_at=now - QUIET_SECONDS, failures=0, trigger="request", event_at=event_at,
                   mode=MODE_FULL, since_sha=None)
            return
        if sha and sha != state.get("sha"):
            update(number, sha=sha, stage="waiting", seen_at=now, failures=0, trigger="push", gates={}, lanes=[],
                   stoppedAt=None, event=None, run_dir=None, ci=None, event_at=event_at, **next_mode(state))
            if state.get("stage") == "running":
                kill(number)  # a newer push makes the running review stale
        elif state.get("stage") in (None, "closed", "skipped"):
            update(number, sha=sha, stage="waiting", seen_at=now, failures=0, trigger=action, event_at=event_at)
    elif kind == "check_suite" and action == "completed":
        for number_found in prs_at(sha, number):
            update(number_found, recheck_at=event_at)
            jobs.put(("recheck", number_found))


def next_mode(state: dict) -> dict:
    """After one finished full review, a change gets the light check against the last checked head.

    A head that is still waiting keeps the head its check started from. A review that stopped at Gate 0 or Gate 1
    never ran the ladder, so the next head gets the full one.
    """
    if state.get("stage") == "done" and not state.get("stoppedAt") and state.get("report_comment_id"):
        return {"mode": MODE_RECHECK, "since_sha": state.get("sha")}
    if state.get("mode") == MODE_RECHECK and state.get("since_sha"):
        return {"mode": MODE_RECHECK, "since_sha": state["since_sha"]}
    return {"mode": MODE_FULL, "since_sha": None}


def received(event: dict, now: float) -> float:
    """When the hooks service got the event; the inbox read time when the event carries none."""
    at = inbox_module().received_epoch(event)
    return at if at is not None else now


def inbox_module():
    import inbox  # imported late, so a missing inbox never stops the boot
    return inbox


def prs_at(sha: str | None, number: int | None) -> list[int]:
    """The reviewed PRs whose head is this commit. A PR that Orobas never saw has no state, so it is left out."""
    if isinstance(number, int):
        return [number] if (STATE / f"{number}.json").exists() else []
    found = []
    for path in sorted(STATE.glob("*.json")):
        state = jsonstore.read(path)
        if state is None:
            unreadable(path.stem)
            continue
        state = migrate(state)
        if sha and state.get("sha") == sha and state.get("stage") in ("done", "stopped"):
            found.append(int(path.stem))
    return found


def unreadable(name: str) -> None:
    """One unreadable file is skipped: a single bad state must never drop the GitHub event for every other PR."""
    log.warning("the state of #%s does not parse", name)
    axiom.emit(SERVICE, "orobas.state.unreadable", f"the state of #{name} does not parse", f"pr-{name}", level="warn",
               module="reviewer")


def release(now: float) -> None:
    """Queues every head that waited QUIET_SECONDS with no new push, unless Orobas's seat share is spent."""
    if (reason := budget.blocked(BUDGET, now)) is not None:
        hold(reason, now)
        return
    for reason in (budget.CAP, budget.SEAT_LIMIT):
        _held.pop(reason, None)
    # By number, so the oldest PR goes first when the day's full reviews run out.
    for number in sorted(int(path.stem) for path in STATE.glob("*.json") if path.stem.isdigit()):
        with STATE_LOCK:
            state = load(number)
            if state.get("stage") != "waiting" or now - state.get("seen_at", now) < QUIET_SECONDS:
                continue
            if state.get("mode") != MODE_RECHECK and not budget.take_full_review(BUDGET, now):
                hold(budget.DAILY, now)
                continue
            state["stage"] = "queued"
            save(number, state)
        jobs.put(("review", number))
    if budget.full_reviews(BUDGET, now) < budget.DAILY_FULL_REVIEWS:
        _held.pop(budget.DAILY, None)


_held: dict[str, float] = {}


def hold(reason: str, now: float) -> None:
    """One `orobas.budget.reached` per hold, not one per inbox tick. The heads keep waiting."""
    last = _held.get(reason)
    if last is not None and now - last < budget.WINDOW_SECONDS:
        return
    _held[reason] = now
    spent = budget.spent(BUDGET, now)
    log.info("no new review: %s (%.2f of %.2f USD in the window)", reason, spent, budget.CAP_USD)
    axiom.emit(SERVICE, "orobas.budget.reached", f"no new review: {reason}", "budget", level="warn",
               module="reviewer", reason=reason, costUsd=round(spent, 2), capUsd=budget.CAP_USD,
               fullReviews=budget.full_reviews(BUDGET, now), dailyFullReviews=budget.DAILY_FULL_REVIEWS)


def recover() -> None:
    """At boot: a review that a restart cut runs again from the start; the run reads only, so a repeat is safe."""
    if not STATE.exists():
        return
    for path in STATE.glob("*.json"):
        if not path.stem.isdigit():
            continue
        number = int(path.stem)
        if load(number).get("stage") in ACTIVE:
            kill(number)
            update(number, stage="waiting", seen_at=time.time() - QUIET_SECONDS)


# --- Labels -------------------------------------------------------------------------------------------------------


def ensure_labels() -> None:
    """Creates or fixes every label Orobas uses, from stages.LABELS. Labels live in code, never in the GitHub UI."""
    try:
        existing = {label["name"]: label for label in paged(f"repos/{REPO}/labels")}
        for name, spec in stages.LABELS.items():
            label = existing.get(name)
            if label is None:
                gh.github(f"repos/{REPO}/labels", method="POST", body={"name": name, **spec})
            elif label.get("color") != spec["color"] or label.get("description") != spec["description"]:
                gh.github(f"repos/{REPO}/labels/{urllib.parse.quote(name, safe='')}", method="PATCH", body=spec)
    except Exception as error:
        log.exception("ensure_labels failed")
        axiom.emit(SERVICE, "orobas.labels.failed", "the labels could not be created", "labels", level="warn",
                   errorType=type(error).__name__, errorMessage=describe(error))


def set_lane_labels(number: int, lanes: list[str], current: list[str]) -> None:
    desired = {f"{stages.LABEL_PREFIX}{lane}" for lane in lanes}
    add = sorted(desired - set(current))
    remove = sorted(name for name in current if name.startswith(stages.LABEL_PREFIX) and name not in desired)
    if add:
        gh.github(f"repos/{REPO}/issues/{number}/labels", method="POST", body={"labels": add})
    for name in remove:
        try:
            gh.github(f"repos/{REPO}/issues/{number}/labels/{urllib.parse.quote(name, safe='')}", method="DELETE")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise


# --- Inputs and the run -------------------------------------------------------------------------------------------


def eligible(pull: dict, team: set[str]) -> bool:
    return (pull.get("state") == "open" and not pull.get("draft")
            and (pull["head"].get("repo") or {}).get("full_name") == REPO
            and stages.SKIP_LABEL not in [label["name"] for label in pull["labels"]]
            and pull["user"]["login"] in team)


def human_reviewer(pull: dict) -> str | None:
    """The first person, not the author and not a bot, who reviewed the PR in any state."""
    for review in paged(f"repos/{REPO}/pulls/{pull['number']}/reviews"):
        user = review.get("user") or {}
        if user.get("login") not in (me(), pull["user"]["login"]) and user.get("type") != "Bot":
            return user.get("login")
    return None


def pr_meta(pull: dict, files: list[dict], graph: dict, ci: str) -> dict:
    number = pull["number"]
    linked = [n["number"] for n in (graph.get("closingIssuesReferences") or {}).get("nodes", [])]
    issues = []
    for issue in linked[:5]:
        data = gh.github(f"repos/{REPO}/issues/{issue}")
        issues.append({"number": issue, "title": data.get("title"), "body": (data.get("body") or "")[:8000]})
    try:
        behind = gh.github(f"repos/{REPO}/compare/{pull['head']['sha']}...{pull['base']['ref']}").get("ahead_by")
    except urllib.error.HTTPError:
        behind = None
    return {"number": number, "title": pull["title"], "body": pull.get("body") or "",
            "base": pull["base"]["ref"], "baseSha": pull["base"]["sha"], "head": pull["head"]["ref"],
            "headSha": pull["head"]["sha"], "author": pull["user"]["login"],
            "labels": [label["name"] for label in pull["labels"]],
            "assignees": [a["login"] for a in pull["assignees"]],
            "reviewers": [r["login"] for r in pull["requested_reviewers"]] + [t["slug"] for t in pull["requested_teams"]],
            # GitHub drops a review request once that reviewer reviews, so "reviewers" alone reads as "nobody asked".
            "reviews": {r["user"]["login"]: r["state"] for r in paged(f"repos/{REPO}/pulls/{number}/reviews")},
            # The bot's own login: the recheck keeps only the threads the bot started.
            "bot": me(),
            "linkedIssues": linked, "issues": issues, "mergeable": pull.get("mergeable"), "behindBy": behind,
            "commits": [{"sha": c["sha"], "message": c["commit"]["message"]} for c in paged(f"repos/{REPO}/pulls/{number}/commits")],
            "ci": ci, "packages": sorted({"/".join(f["filename"].split("/")[:2]) for f in files}),
            "files": [{"path": f["filename"], "status": f["status"]} for f in files]}


def open_heads(meta: dict) -> set[str]:
    """Head branches of the open PRs, needed only when the base is not a trunk (a stacked PR)."""
    if meta["base"] in TRUNKS:
        return set()
    return {p["head"]["ref"] for p in paged(f"repos/{REPO}/pulls?state=open")}


def run_gate0(meta: dict, files: list[dict]) -> dict:
    # From the clone at the base commit: a PR must not change the template it is checked against.
    template = context.show(meta["baseSha"], gate0.TEMPLATE)
    if template is None:
        log.info("the base %s has no %s, so Gate 0 does not check the template", meta["baseSha"][:9], gate0.TEMPLATE)
    return gate0.check(meta, files, open_heads(meta), gate0.template_prompts(template or ""))


def write_inputs(folder: Path, meta: dict, files: list[dict], graph: dict, first: dict, mode: str = MODE_FULL) -> None:
    (folder / "pr.json").write_text(json.dumps(meta, indent=1))
    (folder / "diff.patch").write_text("".join(
        f"diff --git a/{f['filename']} b/{f['filename']}\n{f.get('patch') or '(no text diff)'}\n" for f in files))
    (folder / "threads.json").write_text(json.dumps((graph.get("reviewThreads") or {}).get("nodes", []), indent=1))
    (folder / "stages.json").write_text(json.dumps(stages.stages_json(), indent=1))
    (folder / "gate0.json").write_text(json.dumps(first, indent=1))
    (folder / "mode").write_text(mode)


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("comments", "process"):
        if not isinstance(data.get(key, []), list):
            return None
    return data


def load_ladder(run_dir: Path) -> dict:
    """The gate files of one run. Raises when the run produced no usable Gate 1, or Gate 1 or Gate 3 broke."""
    status = read_json(run_dir / "status.json")
    if status is None:
        raise RunFailed("the run wrote no status.json")
    if status.get("error"):
        raise RunFailed(str(status["error"])[:200])
    if (limit := budget.limit_hit(run_dir)) is not None:
        raise SeatLimit(limit[:200], budget.reset_at(limit, time.time()))
    entries = {s.get("id"): s for s in status.get("stages", [])}
    for gate_id in ("gate1", "gate3"):
        if entries.get(gate_id, {}).get("status") in ("error", "timeout"):
            api = (read_json(run_dir / f"claude-{gate_id}.json") or {}).get("terminal_reason") == API_ERROR
            raise (ApiFailed if api else RunFailed)(f"{gate_id} did not finish: {entries[gate_id].get('reason', '')}"[:200])
    finished = {key for key, entry in entries.items() if entry.get("status") in ("pass", "fail")}
    ladder = {"status": status, "gate0": read_json(run_dir / "in" / "gate0.json"), "lanes": {}}
    for gate_id in ("gate1", "gate2", "gate3"):
        ladder[gate_id] = read_json(run_dir / f"{gate_id}.json") if gate_id in finished else None
    if ladder["gate1"] is None:
        raise RunFailed("the run left no gate1.json")
    for lane in stages.valid_lanes(ladder["gate2"] or {}):
        if f"lane-{lane}" in finished:
            ladder["lanes"][lane] = read_json(run_dir / f"lane-{lane}.json") or {}
    status["lanes"] = stages.valid_lanes(ladder["gate2"] or {})
    if status.get("stoppedAt"):
        status["stoppedAt"] = "Gate 1"
    return ladder


def incomplete(ladder: dict) -> str:
    """The stages that the ladder needed but that did not finish."""
    status = ladder.get("status") or {}
    if status.get("stoppedAt"):
        return ""
    missed = [s["id"] for s in status.get("stages", []) if s.get("status") in ("error", "timeout", "skipped")
              and s.get("id") != "report"]
    return ", ".join(missed)


def run_ladder(number: int, pull: dict, files: list[dict], graph: dict, meta: dict, first: dict) -> tuple[Path | None, dict]:
    """Runs the sandbox, or skips it when Gate 0 found a Blocker. Returns (run dir, ladder)."""
    if first["blockers"]:
        return None, {"gate0": first, "lanes": {}, "status": {"stoppedAt": "Gate 0", "stages": []}}
    with tempfile.TemporaryDirectory() as folder:
        write_inputs(Path(folder), meta, files, graph, first)
        context.build(Path(folder), pull["head"]["sha"], files)
        done = subprocess.run([RUN_SH, str(number), pull["head"]["sha"], folder], capture_output=True, text=True,
                              timeout=RUN_TIMEOUT)
    if done.returncode != 0:
        raise RunFailed(f"run.sh exited {done.returncode}")
    run_dir = Path(done.stdout.strip().splitlines()[-1])
    # Counted before the ladder loads: a run that broke halfway still spent its share.
    budget.record(BUDGET, budget.run_cost(run_dir), time.time())
    return run_dir, load_ladder(run_dir)


def changed_since(since_sha: str, sha: str) -> list[dict] | None:
    """The files a push changed since the last checked head. None when the old head is gone (a force push)."""
    try:
        compare = gh.github(f"repos/{REPO}/compare/{since_sha}...{sha}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    return None if compare.get("status") == "diverged" else compare.get("files", [])


def load_recheck(run_dir: Path, first: dict) -> dict:
    status = read_json(run_dir / "status.json")
    if status is None:
        raise RunFailed("the run wrote no status.json")
    if (limit := budget.limit_hit(run_dir)) is not None:
        raise SeatLimit(limit[:200], budget.reset_at(limit, time.time()))
    if status.get("error"):
        raise RunFailed(str(status["error"])[:200])
    entry = next((s for s in status.get("stages", []) if s.get("id") == "recheck"), {})
    if entry.get("status") not in ("pass", "fail"):
        api = (read_json(run_dir / "claude-recheck.json") or {}).get("terminal_reason") == API_ERROR
        raise (ApiFailed if api else RunFailed)(f"recheck did not finish: {entry.get('reason', '')}"[:200])
    return {"status": status, "gate0": first, "recheck": read_json(run_dir / "recheck.json") or {}, "lanes": {}}


def run_recheck(number: int, pull: dict, files: list[dict], graph: dict, meta: dict, first: dict,
                since_sha: str) -> tuple[Path | None, dict] | None:
    """The light check. None when the old head is gone, so the caller runs the full ladder instead."""
    changed = changed_since(since_sha, pull["head"]["sha"])
    if changed is None:
        return None
    empty = {"status": {"stages": [], "mode": "recheck"}, "gate0": first, "lanes": {},
             "recheck": {"resolved": [], "comments": [], "process": []}}
    if first["blockers"] or not changed or not verdict.bot_threads((graph.get("reviewThreads") or {}).get("nodes", []), me()):
        # Nothing for a model to read: a Gate 0 Blocker, no new code, or no open thread of mine.
        return None, empty
    with tempfile.TemporaryDirectory() as folder:
        write_inputs(Path(folder), meta, changed, graph, first, mode=MODE_RECHECK)
        context.build(Path(folder), pull["head"]["sha"], changed)
        done = subprocess.run([RUN_SH, str(number), pull["head"]["sha"], folder], capture_output=True, text=True,
                              timeout=RUN_TIMEOUT)
    if done.returncode != 0:
        raise RunFailed(f"run.sh exited {done.returncode}")
    run_dir = Path(done.stdout.strip().splitlines()[-1])
    budget.record(BUDGET, budget.run_cost(run_dir), time.time())
    return run_dir, load_recheck(run_dir, first)


# --- Posting ------------------------------------------------------------------------------------------------------


def post_review(number: int, sha: str, event: str, body: str, comments: list[dict]) -> dict:
    payload = {"commit_id": sha, "event": event, "body": body, "comments": comments}
    try:
        return gh.github(f"repos/{REPO}/pulls/{number}/reviews", method="POST", body=payload)
    except urllib.error.HTTPError as error:
        if error.code == 422 and comments:
            # A line GitHub does not accept: keep the review, move every comment to the body.
            moved = "\n".join(f"- `{c['path']}:{c['line']}`: {c['body']}" for c in comments)
            return post_review(number, sha, event, f"{body}\n\n{moved}", [])
        if error.code >= 500:
            # The review may exist anyway: list first, so it is never posted twice.
            for review in gh.github(f"repos/{REPO}/pulls/{number}/reviews?per_page=100"):
                if review["commit_id"] == sha and review["user"]["login"] == me():
                    return review
            return gh.github(f"repos/{REPO}/pulls/{number}/reviews", method="POST", body=payload)
        raise


def upsert_report(number: int, comment_id: int | None, body: str) -> tuple[int, str]:
    """Edits the report comment in place; finds it by its marker when the id is lost; creates it the first time."""
    if comment_id:
        try:
            done = gh.github(f"repos/{REPO}/issues/comments/{comment_id}", method="PATCH", body={"body": body})
            return done["id"], done["html_url"]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    found = report.find_report(paged(f"repos/{REPO}/issues/{number}/comments"), me())
    if found:
        done = gh.github(f"repos/{REPO}/issues/comments/{found['id']}", method="PATCH", body={"body": body})
    else:
        done = gh.github(f"repos/{REPO}/issues/{number}/comments", method="POST", body={"body": body})
    return done["id"], done["html_url"]


def existing_report(state: dict) -> str:
    """The body of the report comment, so a recheck edits it instead of writing a new one."""
    if not state.get("report_comment_id"):
        return ""
    try:
        return gh.github(f"repos/{REPO}/issues/comments/{state['report_comment_id']}").get("body") or ""
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return ""
        raise


def clean_up(number: int, event: str, sha: str, graph: dict, resolved: list, still_open: int) -> None:
    """Dismisses the bot's own old REQUEST_CHANGES when no Blocker and no earlier thread is left, and resolves its fixed threads."""
    if event != "REQUEST_CHANGES" and not still_open:
        for review in gh.github(f"repos/{REPO}/pulls/{number}/reviews?per_page=100"):
            if review["user"]["login"] == me() and review["state"] == "CHANGES_REQUESTED":
                gh.github(f"repos/{REPO}/pulls/{number}/reviews/{review['id']}/dismissals", method="PUT",
                          body={"message": f"No Blocker is left at {sha[:9]}.", "event": "DISMISS"})
    fixed = {int(i) for i in resolved if str(i).isdigit()}
    for thread in (graph.get("reviewThreads") or {}).get("nodes", []):
        first = (thread["comments"]["nodes"] or [{}])[0]
        if not thread["isResolved"] and (first.get("author") or {}).get("login") == me() and first.get("databaseId") in fixed:
            graphql("mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }", id=thread["id"])


def read_text(run_dir: Path | None, name: str) -> str:
    try:
        return (run_dir / name).read_text() if run_dir else ""
    except OSError:
        return ""


def judge(pull: dict, files: list[dict], graph: dict, ladder: dict, ci: str, meta: dict) -> dict:
    """Everything the host decides: the comments, the body lines, the blockers and the state. No GitHub call."""
    review = verdict.collect(ladder)
    review["comments"] = verdict.dedupe(review["comments"], (graph.get("reviewThreads") or {}).get("nodes", []), me())
    inline, extra, _ = verdict.split_comments(review, verdict.commentable_lines(files))
    verdicts = [(ladder.get(g) or {}).get("verdict") for g in ("gate1", "gate3")]
    blockers = verdict.blocker_count(review, verdicts)
    bot_author = pull["user"]["login"] == me() or pull["user"].get("type") == "Bot"
    stacked = meta["base"] not in TRUNKS
    still_open = verdict.open_threads((graph.get("reviewThreads") or {}).get("nodes", []), me(), review["resolved"])
    event, note = verdict.decide(blockers, ci, gh.guarded_paths([f["filename"] for f in files]), bot_author, CAN_APPROVE,
                                 can_block=CAN_BLOCK, incomplete=incomplete(ladder), stacked=stacked, still_open=still_open,
                                 must_block=verdict.always_blocks(review))
    return {"review": review, "inline": inline, "extra": extra, "blockers": blockers, "event": event, "note": note,
            "still_open": still_open}


def fallback_body(decision: dict) -> str:
    others = len(decision["review"]["comments"]) + len(decision["review"]["process"]) - decision["blockers"]
    return f"{decision['blockers']} Blocker(s) and {max(others, 0)} other finding(s). Each one is on its line or below."


def post(number: int, pull: dict, files: list[dict], graph: dict, ladder: dict, run_dir: Path | None, ci: str,
         meta: dict, state: dict, dry_run: bool = False) -> dict:
    sha = pull["head"]["sha"]
    decision = judge(pull, files, graph, ladder, ci, meta)
    usage = budget.run_usage(run_dir) if run_dir else None
    recheck = "recheck" in ladder
    if recheck:
        fixed = len((ladder.get("recheck") or {}).get("resolved", []))
        section = report.recheck_section(sha, fixed, decision["still_open"], decision["blockers"], decision["event"],
                                         decision["note"])
        if usage:
            section += "\n\n" + report.cost_line(usage)
        report_body = report.with_recheck(existing_report(state), section)
    else:
        summary = read_text(run_dir, "summary.md")
        report_body = report.render(sha, ladder, decision["event"], decision["note"], summary, usage)
    lanes = list(ladder.get("lanes") or {}) if ladder.get("gate2") is not None else None
    if dry_run:
        print(json.dumps({"event": decision["event"], "comments": decision["inline"], "lanes": lanes,
                          "body": verdict.body_text(read_text(run_dir, "body.md") or fallback_body(decision),
                                                    decision["extra"], decision["note"])}, indent=1))
        print(report_body)
        return decision
    if lanes is not None:
        set_lane_labels(number, (ladder.get("status") or {}).get("lanes") or lanes,
                        [label["name"] for label in pull["labels"]])
    comment_id, url = upsert_report(number, state.get("report_comment_id"), report_body)
    body = verdict.body_text(read_text(run_dir, "body.md") or fallback_body(decision), decision["extra"],
                             decision["note"], url)
    # A recheck posts a review only for a new comment or a new state; the report already says the rest.
    if not recheck or decision["inline"] or decision["extra"] or decision["event"] != state.get("event"):
        post_review(number, sha, decision["event"], body, decision["inline"])
    # The review is out: from here a failure must not count as a failed review, or the next event posts it twice.
    try:
        clean_up(number, decision["event"], sha, graph, decision["review"]["resolved"], decision["still_open"])
    except Exception:
        log.exception("the review of #%s is posted, but its clean-up failed", number)
    emit_gates(number, sha, ladder, run_dir)
    axiom.emit(SERVICE, "orobas.review.posted", f"{decision['event']} on #{number}", trace(number, sha), pr=number,
               event=decision["event"], blockers=decision["blockers"], ci=ci,
               comments=len(decision["inline"]) + len(decision["extra"]),
               stoppedAt=(ladder.get("status") or {}).get("stoppedAt"), lanes=",".join(lanes or []),
               sinceEventMs=inbox_module().since_ms(state.get("event_at")),
               # One dict: both carry costUsd, and two ** with the same key raise.
               **{**(axiom.run_fields(run_dir) if run_dir else {}), **(usage or {})})
    return {**decision, "report_comment_id": comment_id}


def emit_gates(number: int, sha: str, ladder: dict, run_dir: Path | None) -> None:
    """One `orobas.gate.finished` per stage of the run, with its model, time and cost, for tuning stages.py."""
    for entry in (ladder.get("status") or {}).get("stages", []):
        stage_id = entry.get("id", "")
        lane = stage_id[5:] if stage_id.startswith("lane-") else None
        result = (ladder.get("lanes") or {}).get(lane) if lane else ladder.get(stage_id)
        counts = report.counts(result) if isinstance(result, dict) else {}
        persona = stages.LANES.get(lane, {}).get("persona") if lane else stages.GATES.get(stage_id, {}).get("persona")
        bad = entry.get("status") in ("error", "timeout")
        axiom.emit(SERVICE, "orobas.gate.finished", f"{stage_id} {entry.get('status')} on #{number}", trace(number, sha),
                   level="warn" if bad else "info", pr=number, gate="lane" if lane else stage_id, lane=lane,
                   persona=persona, model=entry.get("model"), status=entry.get("status"), reason=entry.get("reason"),
                   findings=sum(counts.values()) if counts else None, blockers=counts.get("blocker"),
                   **(axiom.file_fields(run_dir / f"claude-{stage_id}.json") if run_dir else {}))


# --- The worker ---------------------------------------------------------------------------------------------------


def review(number: int, dry_run: bool = False, from_run: Path | None = None) -> None:
    pull = gh.github(f"repos/{REPO}/pulls/{number}")
    state = load(number)
    if not dry_run and (not eligible(pull, gh.team_logins()) or pull["head"]["sha"] != state.get("sha")):
        # A newer push already reset the state, or the PR left the scope; nothing to post for this head.
        if state.get("stage") == "queued" and pull["head"]["sha"] == state.get("sha"):
            update(number, stage="skipped")
        if state.get("mode") != MODE_RECHECK:
            budget.give_back_full_review(BUDGET, time.time())
        return
    sha = pull["head"]["sha"]
    if not dry_run and state.get("mode") != MODE_RECHECK and state.get("trigger") != "request" and (who := human_reviewer(pull)):
        update(number, expect_sha=sha, stage="skipped")
        budget.give_back_full_review(BUDGET, time.time())
        axiom.emit(SERVICE, "orobas.review.skipped", f"#{number} already has a review from a person", trace(number, sha),
                   pr=number, reason="human_review", reviewer=who)
        return
    ci = gh.checks_state(sha)  # read now, never waited for
    files = paged(f"repos/{REPO}/pulls/{number}/files")
    graph = pr_graph(number)
    meta = pr_meta(pull, files, graph, ci)
    context.refresh(number)
    first = run_gate0(meta, files)
    if not dry_run and update(number, expect_sha=sha, stage="running") is None:
        superseded(number, sha)  # a push landed during the reads above; the new head keeps its own state
        return
    if from_run:
        run_dir, ladder = from_run, load_ladder(from_run)
        ladder["gate0"] = first
    else:
        try:
            light = None
            if state.get("mode") == MODE_RECHECK and state.get("since_sha") and not dry_run:
                light = run_recheck(number, pull, files, graph, meta, first, state["since_sha"])
            run_dir, ladder = light if light is not None else run_ladder(number, pull, files, graph, meta, first)
        except Exception:
            # A newer push or a close kills the container, so the run ends without its files: not a failure.
            if not dry_run and superseded(number, sha):
                return
            raise
    if not dry_run:
        latest = gh.github(f"repos/{REPO}/pulls/{number}")
        if superseded(number, sha, latest) or update(number, expect_sha=sha, stage="posting",
                                                      run_dir=str(run_dir) if run_dir else None) is None:
            return
    done = post(number, pull, files, graph, ladder, run_dir, ci, meta, state, dry_run=dry_run)
    if dry_run:
        return
    stopped = (ladder.get("status") or {}).get("stoppedAt")
    update(number, expect_sha=sha, stage="stopped" if stopped else "done", event=done["event"], ci=ci, stoppedAt=stopped,
           mode=MODE_RECHECK if "recheck" in ladder else MODE_FULL,
           lanes=list(ladder.get("lanes") or {}), failures=0, report_comment_id=done.get("report_comment_id"),
           reviewed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def superseded(number: int, sha: str, latest: dict | None = None) -> bool:
    """True when a newer head or a close replaced the review of `sha`. The new head keeps its own state."""
    state = load(number)
    moved = state.get("sha") != sha or state.get("stage") == "closed"
    if latest is not None:
        moved = moved or latest["head"]["sha"] != sha or latest.get("state") != "open"
    if moved:
        axiom.emit(SERVICE, "orobas.review.superseded", f"the review of #{number} at {sha[:9]} was superseded",
                   trace(number, sha), pr=number, stage=state.get("stage"))
    return moved


def recheck(number: int) -> None:
    """CI ended after the post: decide the state again from the saved run files and post it, with no model."""
    state = load(number)
    if state.get("stage") not in ("done", "stopped"):
        return
    pull = gh.github(f"repos/{REPO}/pulls/{number}")
    sha = pull["head"]["sha"]
    if sha != state.get("sha") or pull.get("state") != "open":
        return
    ci = gh.checks_state(sha)
    if ci == state.get("ci") or ci == "pending":
        return
    files = paged(f"repos/{REPO}/pulls/{number}/files")
    graph = pr_graph(number)
    meta = pr_meta(pull, files, graph, ci)
    first = run_gate0(meta, files)
    run_dir = Path(state["run_dir"]) if state.get("run_dir") else None
    ladder = load_ladder(run_dir) if run_dir else {"gate0": first, "lanes": {}, "status": {"stoppedAt": "Gate 0", "stages": []}}
    ladder["gate0"] = first
    decision = judge(pull, files, graph, ladder, ci, meta)
    report_body = report.render(sha, ladder, decision["event"], decision["note"], read_text(run_dir, "summary.md"),
                                budget.run_usage(run_dir) if run_dir else None)
    comment_id, url = upsert_report(number, state.get("report_comment_id"), report_body)
    if decision["event"] != state.get("event"):
        post_review(number, sha, decision["event"], verdict.body_text(f"CI is {ci} now.", [], decision["note"], url), [])
        axiom.emit(SERVICE, "orobas.review.posted", f"{decision['event']} on #{number} after CI", trace(number, sha),
                   pr=number, event=decision["event"], blockers=decision["blockers"], ci=ci, recheck=True,
                   sinceEventMs=inbox_module().since_ms(state.get("recheck_at")))
    update(number, expect_sha=sha, ci=ci, event=decision["event"], report_comment_id=comment_id)


def work() -> None:
    while True:
        kind, number = jobs.get()
        # The head this job works on: a push while it runs moves the state, and then the failure belongs to no one.
        sha = load(number).get("sha")
        try:
            if kind == "recheck":
                recheck(number)
            else:
                review(number)
        except Exception as error:
            state = load(number)
            if state.get("stage") == "closed":
                log.warning("%s of #%s failed after the PR closed", kind, number)
                continue
            if kind == "recheck":
                # The review is out and calls no model again: keep the stage, and the next check_suite tries again.
                log.exception("recheck of #%s failed", number)
                axiom.emit(SERVICE, "orobas.recheck.failed", f"recheck of #{number} failed", trace(number, state.get("sha")),
                           level="warn", pr=number, stage=state.get("stage"),
                           errorType=type(error).__name__, errorMessage=describe(error))
                continue
            if isinstance(error, SeatLimit):
                budget.pause(BUDGET, error.until)
                update(number, expect_sha=sha, stage="waiting", seen_at=time.time() - QUIET_SECONDS)
                axiom.emit(SERVICE, "orobas.review.parked", f"#{number} waits for the seat reset", trace(number, sha),
                           pr=number, reason=budget.SEAT_LIMIT, errorMessage=describe(error),
                           resetAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(error.until)))
                continue
            failures = state.get("failures", 0) + 1
            stage = "failed" if failures >= MAX_FAILURES else "waiting"
            update(number, expect_sha=sha, failures=failures, stage=stage, seen_at=time.time())
            log.exception("%s of #%s failed", kind, number)
            axiom.emit(SERVICE, "orobas.review.failed", f"{kind} of #{number} failed", trace(number, state.get("sha")),
                       # The last try pages even on a vendor fault: after it the head waits with no review.
                       level="warn" if isinstance(error, ApiFailed) and stage != "failed" else "error", pr=number, stage=state.get("stage"), failures=failures,
                       errorType=type(error).__name__, errorMessage=describe(error))


def heartbeat() -> None:
    while True:
        axiom.emit(SERVICE, "orobas.heartbeat", "orobas is up", "heartbeat", enabled=ENABLED, queued=jobs.qsize())
        time.sleep(HEARTBEAT_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("orobas up: enabled=%s can_approve=%s can_block=%s", ENABLED, CAN_APPROVE, CAN_BLOCK)
    STATE.mkdir(parents=True, exist_ok=True)
    ensure_labels()
    recover()
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=work, daemon=True).start()
    while True:
        try:
            inbox_module().consume(SERVICE, (lambda event: handle(event, time.time())) if ENABLED else (lambda event: None),
                                   event_failed)
            if ENABLED:
                release(time.time())
        except Exception as error:
            log.exception("the inbox loop failed")
            axiom.emit(SERVICE, "orobas.review.failed", "the inbox loop failed", "inbox", level="error", stage="inbox",
                       errorType=type(error).__name__, errorMessage=describe(error))
        time.sleep(INBOX_SECONDS)


def event_failed(event: dict, error: Exception) -> None:
    """drain() already removed the event file, so the event goes into the log in full: a person can replay it."""
    log.exception("the %s event for #%s failed", event.get("event"), event.get("number"))
    axiom.emit(SERVICE, "orobas.review.failed", f"the {event.get('event')} event failed", trace(event.get("number") or 0,
               event.get("sha")), level="error", stage="inbox", pr=event.get("number"), event=event.get("event"),
               action=event.get("action"), sha=event.get("sha"), delivery=event.get("delivery"),
               errorType=type(error).__name__, errorMessage=describe(error))


if __name__ == "__main__":
    # --dry-run <pr> [--from-run <run-dir>]: one review of the PR head, printed and never posted. The state stays untouched.
    if sys.argv[1:2] == ["--dry-run"]:
        source = Path(sys.argv[4]) if sys.argv[3:4] == ["--from-run"] else None
        review(int(sys.argv[2]), dry_run=True, from_run=source)
    else:
        main()
