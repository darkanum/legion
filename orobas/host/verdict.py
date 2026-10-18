"""Orobas host checks: which lines take a comment, which review state to post, and how the text is cleaned.

The gate files are the output of models that read an untrusted diff, so every rule that matters is decided here,
not by a model: the state, the lanes, the mentions, the lines. See docs/OROBAS.md.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402
from github import ATTRIBUTION, team_mention  # noqa: E402

from diff import right_rows  # noqa: E402

MAX_COMMENTS = 30
MAX_TEXT = 4000
MENTION = re.compile(r"@(?=[A-Za-z0-9-])")
DASH = re.compile(r"\s*—\s*")
HTML_COMMENT = re.compile(r"<!--|-->")
# Who must approve where the bot cannot: the team, written by the host after clean(), so this is the one live mention.
ENGINEERING = team_mention()
SIGNATURE = f"<sub>Orobas, the review agent. The rules are in `{config.RULES_DIR}/`.</sub>"
RANK = {"blocker": 0, "important": 1, "note": 2, "followup": 3}
# Rules that request changes even while OROBAS_CAN_BLOCK is false (OROBAS_ALWAYS_BLOCK): by default a PR with no issue,
# which is the one a team merges by mistake most often.
ALWAYS_BLOCK = set(config.items("OROBAS_ALWAYS_BLOCK", "STD-01"))
# The order in which a source wins a line: the earlier gate reads the rules the later ones assume.
SOURCE_ORDER = ("gate0", "gate1", "lanes", "gate3")


def commentable_lines(files: list[dict]) -> dict[str, set[int]]:
    """Right-side line numbers of each file's diff: GitHub accepts an inline comment only on these."""
    return {f["filename"]: {number for number, _ in right_rows(f.get("patch"))} for f in files}


def clean(text: str) -> str:
    """No attribution line, no em dash, no HTML comment and no live mention: a model must not ping anybody or forge a marker."""
    kept = [row for row in str(text).splitlines() if not ATTRIBUTION.search(row)]
    text = DASH.sub(", ", "\n".join(kept)).replace("–", "-")
    text = HTML_COMMENT.sub("", text)
    return MENTION.sub("@​", text)[:MAX_TEXT].strip()


def collect(ladder: dict) -> dict:
    """One review-shaped dict from every source that produced a result: {comments, process, resolved}.

    Each finding is tagged with its source. The lanes that did not finish are not in `ladder["lanes"]`.
    """
    comments, process, resolved = [], [], set()
    results = [("gate0", ladder.get("gate0")), ("gate1", ladder.get("gate1"))]
    results += [(f"lane-{lane}", result) for lane, result in (ladder.get("lanes") or {}).items()]
    results.append(("gate3", ladder.get("gate3")))
    results.append(("recheck", ladder.get("recheck")))
    for source, result in results:
        if not result:
            continue
        comments += [{**c, "source": source} for c in result.get("comments", []) if isinstance(c, dict)]
        process += [{**p, "source": source} for p in result.get("process", []) if isinstance(p, dict)]
        resolved |= {int(i) for i in result.get("resolved", []) if str(i).isdigit()}
    return {"comments": comments, "process": process, "resolved": sorted(resolved)}


def bot_threads(threads: list[dict], me: str) -> list[tuple[dict, dict]]:
    """(thread, first comment) for every unresolved thread the bot started."""
    found = []
    for thread in threads:
        first = (thread.get("comments", {}).get("nodes") or [{}])[0]
        if not thread.get("isResolved") and (first.get("author") or {}).get("login") == me:
            found.append((thread, first))
    return found


def open_threads(threads: list[dict], me: str, resolved: list[int]) -> int:
    """The bot's earlier threads that no gate marked as fixed. Gates do not repeat them, so they must still count."""
    return sum(first.get("databaseId") not in set(resolved) for _, first in bot_threads(threads, me))


def dedupe(comments: list[dict], threads: list[dict], me: str) -> list[dict]:
    """One comment per line, the most severe wins; a line that already has an open bot thread gets no new comment."""
    open_lines = {(thread.get("path"), thread.get("line")) for thread, _ in bot_threads(threads, me)}
    best: dict[tuple, dict] = {}
    for c in comments:
        key = (c.get("path"), c.get("line"))
        if key in open_lines:
            continue
        held = best.get(key)
        if held is None or RANK.get(c.get("severity"), 9) < RANK.get(held.get("severity"), 9):
            best[key] = c
    return sorted(best.values(), key=lambda c: RANK.get(c.get("severity"), 9))


def blocker_count(review: dict, verdicts: list[str]) -> int:
    """Every blocker finding counts, and a gate that says `fail` with no blocker line still counts as one."""
    found = sum(f.get("severity") == "blocker" for f in review.get("comments", []) + review.get("process", []))
    return max(found, 1 if "fail" in verdicts else 0)


RULE_ID = re.compile(r"^[A-Z]{2,6}-\d{1,3}$")


def tag(item: dict) -> str:
    """`Blocker · STD-12: `, so the author sees the severity and the rule every finding cites. A rule id that is not one
    is dropped: it comes from a model."""
    severity = str(item.get("severity") or "note").capitalize()
    rule = str(item.get("rule") or "")
    return f"**{severity}** · `{rule}`: " if RULE_ID.match(rule) else f"**{severity}**: "


def split_comments(review: dict, lines: dict[str, set[int]]) -> tuple[list[dict], list[str], int]:
    """(inline comments, lines for the body, Blocker count). A comment off the diff moves to the body."""
    inline: list[dict] = []
    extra: list[str] = []
    blockers = 0
    ordered = sorted(review.get("comments", []), key=lambda c: RANK.get(c.get("severity"), 9))
    for item in ordered[:MAX_COMMENTS]:
        severity = item.get("severity", "note")
        blockers += severity == "blocker"
        body = clean(item.get("body", ""))
        path, line = item.get("path", ""), item.get("line")
        if isinstance(line, int) and line in lines.get(path, set()):
            inline.append({"path": path, "line": line, "side": "RIGHT", "body": tag(item) + body})
        else:
            extra.append(f"- `{path}:{line}`: {tag(item)}{body}")
    for item in review.get("process", []):
        blockers += item.get("severity") == "blocker"
        extra.append(f"- {tag(item)}{clean(item.get('text', ''))}")
    return inline, extra, blockers


ALWAYS_BLOCK_NOTES = {
    "STD-01": "The PR closes no issue. Add `Closes #N` to the body, or the `no-issue` label if it has none on purpose.",
}


def always_block_note(rule: str) -> str:
    return ALWAYS_BLOCK_NOTES.get(rule, f"`{rule}` always blocks: fix it, and I look again.")


def always_blocks(review: dict) -> list[str]:
    """The ALWAYS_BLOCK rules that this review found as a Blocker, in rule order."""
    return sorted({p["rule"] for p in review.get("process", [])
                   if p.get("rule") in ALWAYS_BLOCK and p.get("severity") == "blocker"})


def decide(blockers: int, ci: str, guarded: list[str], bot_author: bool, can_approve: bool, *,
           can_block: bool, incomplete: str = "", stacked: bool = False, still_open: int = 0,
           must_block: list[str] | None = None) -> tuple[str, str]:
    """(review event, a line for the body). APPROVE needs every gate; the model never picks the state."""
    if bot_author:
        # GitHub refuses APPROVE and REQUEST_CHANGES from the PR author, and the bot account writes the Marbas PRs.
        why = "so this one blocks" if blockers else "and nothing blocks from my side"
        return "COMMENT", f"An agent wrote this PR, {why}. A person must approve it."
    if must_block:
        return "REQUEST_CHANGES", " ".join(always_block_note(rule) for rule in must_block)
    if blockers and not can_block:
        return "COMMENT", f"This has {blockers} Blocker(s). I do not block PRs for now, so a person decides."
    if blockers:
        return "REQUEST_CHANGES", ""
    if still_open:
        return "COMMENT", f"{still_open} earlier comment(s) of mine are still open, so I cannot approve yet."
    if incomplete:
        return "COMMENT", f"The review did not finish ({incomplete}), so I cannot approve. A person must look at it."
    if ci == "red":
        return "COMMENT", "CI is red, so I cannot approve."
    if ci != "green":
        return "COMMENT", "CI is still running, so I cannot approve yet. I look again when it ends."
    if guarded:
        return "COMMENT", f"Nothing blocks from my side. This PR touches `{guarded[0]}`, so {ENGINEERING} must approve it."
    if stacked:
        return "COMMENT", f"Nothing blocks from my side. This PR is stacked, so I approve only when its base is `{config.BASE_BRANCH}`."
    if not can_approve:
        return "COMMENT", f"Nothing blocks from my side. {ENGINEERING}, could one of you approve?"
    return "APPROVE", ""


def body_text(body: str, extra: list[str], note: str, report_url: str = "") -> str:
    """The report's short body, the findings with no line, the host's note, the report link and the signature."""
    parts = [clean(body)]
    if extra:
        parts.append("\n".join(extra))
    if note:
        parts.append(note)
    if report_url:
        parts.append(f"Full report: {report_url}")
    parts.append(SIGNATURE)
    return "\n\n".join(p for p in parts if p)
