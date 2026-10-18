"""Orobas's report: one comment per PR, rendered by the host from the gate files and edited in place on every run.

The host renders every fact (the ladder, the counts, the blockers, the verdict). The report stage's model call writes
only the paragraph in summary.md, which goes through verdict.clean() like every other model string.
"""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402
from github import REPO  # noqa: E402

import stages  # noqa: E402
import verdict  # noqa: E402

MARKER = "<!-- orobas-report:v1 "
SEVERITIES = ("blocker", "important", "note", "followup")
MAX_BLOCKERS = 20
MAX_LENGTH = 60_000
SIGNATURE = ("<sub>🐴 Orobas writes this after each run and edits it in place. I am a bot, and every rule I apply is in "
             f"`{config.RULES_DIR}/`. A person can dismiss a finding; say why in its thread, so the rule gets fixed.</sub>")


def encode(meta: dict) -> str:
    return MARKER + base64.b64encode(json.dumps(meta, separators=(",", ":")).encode()).decode() + " -->"


def decode(body: str) -> dict | None:
    start = (body or "").find(MARKER)
    if start < 0:
        return None
    rest = body[start + len(MARKER):]
    try:
        return json.loads(base64.b64decode(rest.split(" -->", 1)[0]))
    except Exception:
        return None


def find_report(comments: list[dict], me: str) -> dict | None:
    """The bot's own report comment: written by the bot, and carrying the marker."""
    for c in comments:
        if (c.get("user") or {}).get("login") == me and MARKER in (c.get("body") or ""):
            return c
    return None


def counts(result: dict | None) -> dict:
    found = (result or {}).get("comments", []) + (result or {}).get("process", [])
    return {s: sum(f.get("severity") == s for f in found) for s in SEVERITIES}


def result_text(result: dict | None, entry: dict | None) -> str:
    """The icon and one line for a gate or a lane."""
    status = (entry or {}).get("status")
    if status in ("error", "timeout"):
        return f"⚠️ did not finish ({status})"
    if result is None or status == "skipped":
        return "⏭️ did not run"
    c = counts(result)
    if c["blocker"]:
        rest = c["important"] + c["note"] + c["followup"]
        return f"❌ {c['blocker']} blocker" + ("s" if c["blocker"] > 1 else "") + (f", {rest} more" if rest else "")
    parts = [f"{c[s]} {s}" for s in ("important", "note", "followup") if c[s]]
    return "🟡 " + ", ".join(parts) if parts else "✅ nothing to report"


def link(sha: str, path: str, line) -> str:
    if isinstance(line, int):
        return f"[`{path}:{line}`](https://github.com/{REPO}/blob/{sha}/{path}#L{line})"
    return f"`{path}`"


def entry_for(status: dict, stage_id: str) -> dict | None:
    return next((s for s in status.get("stages", []) if s.get("id") == stage_id), None)


def kilo(count: int) -> str:
    return f"{count / 1000:.0f}k" if count >= 1000 else str(count)


def cost_line(usage: dict) -> str:
    """What the review used: tokens, the equivalent cost, and its share of the Claude seat window."""
    total = usage["tokensIn"] + usage["tokensOut"]
    return (f"🧾 This review: {kilo(total)} tokens ({kilo(usage['tokensIn'])} in · {kilo(usage['tokensOut'])} out) · "
            f"{usage['costUsd']:.2f} USD · {usage['windowPct']:g}% of the seat window")


RECHECK_START = "<!-- orobas-recheck -->"
RECHECK_END = "<!-- /orobas-recheck -->"


def recheck_section(sha: str, fixed: int, still_open: int, new_blockers: int, event: str, note: str) -> str:
    """What the light check of one push found. It replaces the previous one in the report."""
    lines = [f"### 🔁 Recheck of `{sha[:7]}`",
             f"This push fixes {fixed} of my comment(s); {still_open} still open."]
    if new_blockers:
        lines.append(f"It adds {new_blockers} new Blocker(s), on their lines.")
    lines.append(f"**Verdict:** {event}. {note}".strip())
    lines.append("I run one full review per PR. To get a full review again, request a review from me.")
    return "\n\n".join(lines)


def with_recheck(body: str, section: str) -> str:
    """The report with this recheck in place of the last one, above the signature."""
    if RECHECK_START in body:
        before, rest = body.split(RECHECK_START, 1)
        body = before.rstrip() + (rest.split(RECHECK_END, 1)[1] if RECHECK_END in rest else "")
    block = f"{RECHECK_START}\n{section}\n{RECHECK_END}"
    if SIGNATURE in body:
        head, tail = body.split(SIGNATURE, 1)
        return f"{head.rstrip()}\n\n{block}\n\n{SIGNATURE}{tail}"[:MAX_LENGTH]
    return f"{body.rstrip()}\n\n{block}"[:MAX_LENGTH]


def render(sha: str, ladder: dict, event: str, note: str, summary: str = "", usage: dict | None = None) -> str:
    """The report body. `ladder` holds gate0, gate1, gate2, lanes {lane: result}, gate3 and status."""
    status = ladder.get("status") or {}
    picked = set((ladder.get("lanes") or {}).keys()) | set(status.get("lanes") or [])
    rows = [("0 · Rules", "🤖 Host", result_text(ladder.get("gate0"), None))]
    for gate_id, label in (("gate1", "1 · Standards"), ("gate2", "2 · Triage")):
        gate = stages.GATES[gate_id]
        text = result_text(ladder.get(gate_id), entry_for(status, gate_id))
        if gate_id == "gate2" and ladder.get("gate2") is not None and text.startswith("✅"):
            text = "✅ " + (", ".join(sorted(picked)) or "no lane needed")
        rows.append((label, gate["persona"], text))
    for lane, spec in stages.LANES.items():
        if lane in picked:
            rows.append((f"Lane · {lane}", spec["persona"],
                         result_text((ladder.get("lanes") or {}).get(lane), entry_for(status, f"lane-{lane}"))))
    rows.append(("3 · Intent", stages.GATES["gate3"]["persona"],
                 result_text(ladder.get("gate3"), entry_for(status, "gate3"))))
    table = ["| Gate | Who | Result |", "| --- | --- | --- |"] + [f"| {a} | {b} | {c} |" for a, b, c in rows]

    blockers = []
    for source, result in sources(ladder):
        persona = persona_of(source)
        for f in result.get("comments", []):
            if f.get("severity") == "blocker":
                blockers.append(f"- {persona} `{f.get('rule', '')}` {link(sha, f.get('path', ''), f.get('line'))}: "
                                f"{verdict.clean(first_line(f.get('body', '')))}")
        for p in result.get("process", []):
            if p.get("severity") == "blocker":
                blockers.append(f"- {persona} `{p.get('rule', '')}`: {verdict.clean(first_line(p.get('text', '')))}")
    stop = ""
    if status.get("stoppedAt"):
        stop = f"\n\nThe ladder stopped at {status['stoppedAt']}: fix the Blockers and push, and I run every gate again."
    parts = [f"## 🐴 Orobas's report for `{sha[:7]}`", "\n".join(table)]
    if blockers:
        extra = len(blockers) - MAX_BLOCKERS
        parts.append("**Blocks**\n\n" + "\n".join(blockers[:MAX_BLOCKERS]) + (f"\n- … and {extra} more" if extra > 0 else ""))
    parts.append(f"**Verdict:** {event}. {note}".strip() + stop)
    if summary.strip():
        parts.append(verdict.clean(summary))
    if usage:
        parts.append(cost_line(usage))
    parts.append(SIGNATURE)
    meta = {"v": 1, "sha": sha, "event": event, "stoppedAt": status.get("stoppedAt"), "lanes": sorted(picked),
            "counts": {source: counts(result) for source, result in sources(ladder)}}
    body = "\n\n".join(parts)[:MAX_LENGTH]
    return body + "\n" + encode(meta)


def sources(ladder: dict) -> list[tuple[str, dict]]:
    """(source id, result) for every gate and lane that produced a result, in ladder order."""
    found = []
    for gate_id in ("gate0", "gate1"):
        if ladder.get(gate_id):
            found.append((gate_id, ladder[gate_id]))
    for lane, result in (ladder.get("lanes") or {}).items():
        if result:
            found.append((f"lane-{lane}", result))
    if ladder.get("gate3"):
        found.append(("gate3", ladder["gate3"]))
    return found


def persona_of(source: str) -> str:
    if source == "gate0":
        return "🤖"
    if source.startswith("lane-"):
        return stages.LANES.get(source[5:], {}).get("persona", "").split(" ")[0]
    return stages.GATES.get(source, {}).get("persona", "").split(" ")[0]


def first_line(text: str) -> str:
    return next((line.strip() for line in str(text).splitlines() if line.strip()), "")
