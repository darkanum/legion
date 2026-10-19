"""Valac's posts to its Slack channel, through that channel's incoming webhook. The text builders are pure, so tested.

No webhook file on the host turns the posts off; the issues and the run log carry the same facts.
"""

import json
import urllib.request

from github import REPO
from paths import SECRETS

WEBHOOK_FILE = SECRETS / "slack_webhook_url"
SIGNATURE = ":dragon: *Valac*"


def post(text: str) -> bool:
    """Posts one message; False when the VM has no webhook. Raises on an HTTP error, and the caller only logs it."""
    if not WEBHOOK_FILE.exists():
        return False
    request = urllib.request.Request(WEBHOOK_FILE.read_text().strip(), method="POST",
                                     data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
    return True


def escape(text) -> str:
    """Slack reads &, < and > as markup."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def link(number: int) -> str:
    return f"<https://github.com/{REPO}/issues/{number}|#{number}>"


def issue_opened(number: int, title: str, finding: dict, owner: str) -> str:
    return (f"{SIGNATURE} opened {link(number)}: {escape(title)}\n"
            f"Score {finding.get('score')} · `{escape(finding.get('dataset'))}` · owner {escape(owner) or 'none'}")


def patrol_done(findings: int, opened: list[int], cost: float | None, minutes: float | None) -> str:
    result = f"opened {' '.join(link(n) for n in opened)}" if opened else "nothing new to open"
    return f"{SIGNATURE} patrol done: {findings} findings, {result}{totals(cost, minutes)}"


def patrol_skipped(waiting: list[int]) -> str:
    return (f"{SIGNATURE} skipped the patrol: {' '.join(link(n) for n in waiting)} wait for people. "
            "Close one, as fixed or as wrong, and the next patrol runs.")


def regressed(issues: list[int]) -> str:
    return (f":warning: {SIGNATURE} the defects of {' '.join(link(n) for n in issues)} are back in the logs after "
            "their fix. Each one is raised again as a new issue.")


def patrol_blocked(reason: str, cost: float | None, minutes: float | None) -> str:
    return f":warning: {SIGNATURE} patrol ended blocked: {escape(reason)}{totals(cost, minutes)}"


def totals(cost: float | None, minutes: float | None) -> str:
    parts = [f"{cost:.2f} USD" if cost is not None else "", f"{minutes:.0f} min" if minutes is not None else ""]
    return "".join(f" · {p}" for p in parts if p)
