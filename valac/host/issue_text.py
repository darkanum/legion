"""The title and body of the issue Valac opens for a finding, from the patrol JSON. Pure, so it is tested.

The patrol output is a model's text about production logs, so it is untrusted twice. The host refuses a finding that
carries an email, and escapes every mention, before any of it reaches GitHub.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
CLASSES = {"code-bug", "noise", "blind-spot", "wrong-dashboard", "missing-monitor"}


class Unsafe(ValueError):
    """The finding cannot become an issue as it is."""


def strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [value] if isinstance(value, str) else []


def check(finding: dict) -> None:
    if any(EMAIL.search(text) for text in strings(finding)):
        raise Unsafe("the finding holds an email")
    if finding.get("class") not in CLASSES:
        raise Unsafe(f"unknown class {finding.get('class')!r}")
    evidence = finding.get("evidence") or {}
    if not evidence.get("apl") or not isinstance(evidence.get("rows"), int):
        raise Unsafe("a finding without the APL and the counts is not a finding")


def safe(text) -> str:
    # A zero-width space after @ keeps the text from pinging anyone; backticks cannot close the code span.
    return str(text or "").replace("@", "@\u200b").replace("`", "'").strip()


TITLE_MAX = 90  # the text after the prefix


def headline(finding: dict) -> str:
    """The patrol's short title, else the cause. A long one is cut at a word, never inside it."""
    text = next((safe(finding.get(k)).splitlines()[0] for k in ("title", "cause", "eventType") if safe(finding.get(k))), "")
    if len(text) > TITLE_MAX:
        text = text[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def title(finding: dict) -> str:
    """The issue title: the bug prefix of the repository's form (LEGION_ISSUE_TITLE_PREFIX), then the headline."""
    return f"{config.ISSUE_TITLE_PREFIX}{headline(finding)}"


def body(finding: dict) -> str:
    check(finding)
    evidence = finding["evidence"]
    location = finding.get("location") or {}
    where = f"`{safe(location.get('file'))}:{location.get('line')}`" if location.get("file") else "not traced"
    fix = "None: a person decides the fix (auth, billing, data access rules or model prompts)." \
        if finding.get("humanOnly") else safe(finding.get("proposedFix")) or "Not proposed."
    apl = str(evidence["apl"]).replace("```", "'''")
    lines = [
        "## 🐞 What happened", "",
        safe(finding.get("cause")) or "See the evidence below.", "",
        "## 📎 Evidence", "",
        "| Dataset | Event | Rows | Users | Window | Class |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| `{safe(finding.get('dataset'))}` | `{safe(finding.get('eventType'))}` | {evidence['rows']} | "
        f"{evidence.get('users', '?')} | {safe(evidence.get('windowStart'))} to {safe(evidence.get('windowEnd'))} | "
        f"`{finding['class']}` |", "",
        "```kusto", apl, "```", "",
        "## 📦 Where", "",
        where, "",
        "## ✅ Proposed fix", "",
        fix, "",
        f"Found by Valac. Score: {finding.get('score')}. Signals: {', '.join(safe(s) for s in finding.get('signals', []))}.",
    ]
    return "\n".join(lines)
