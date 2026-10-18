"""The thread state of Marbas: one `thread.json` per Slack thread, inside that thread's folder.

Its own module, not part of `listener.py`, because `listener.py` imports `slack_sdk` and the test run installs nothing.
The job, Slack and timer threads all write here while the event thread reads, so every write is atomic.
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import axiom  # noqa: E402
import jsonstore  # noqa: E402
import paths  # noqa: E402

SERVICE = "marbas"
STATE = paths.workspace(SERVICE) / "threads"

log = logging.getLogger("marbas-slack")


def file(key: str) -> Path:
    return STATE / key / "thread.json"


def load(key: str) -> dict:
    path = file(key)
    return json.loads(path.read_text()) if path.exists() else {}


def save(key: str, thread: dict, **fields) -> None:
    """Writes the thread and emits `marbas.thread.stage` when the stage changed."""
    before = load(key).get("stage")
    jsonstore.write(file(key), thread, indent=2)
    if thread.get("stage") != before:
        axiom.emit(SERVICE, "marbas.thread.stage", f"#{thread.get('issue')}: {before} -> {thread.get('stage')}", key,
                   level="warn" if thread.get("stage") == "needs_human" else "info",
                   issue=int(thread.get("issue", 0)), stageFrom=before, stageTo=thread.get("stage"),
                   round=thread.get("round"), pr=thread.get("pr"), **fields)


def open_threads() -> list[tuple[str, dict]]:
    """Every thread on disk. One file that does not parse is skipped, so it never drops the event for the others."""
    found = []
    for path in sorted(STATE.glob("*/thread.json")):
        thread = jsonstore.read(path)
        if thread is None:
            key = path.parent.name
            log.warning("the state of %s does not parse", key)
            axiom.emit(SERVICE, "marbas.state.unreadable", f"the thread state of {key} does not parse", key,
                       level="warn", thread=key)
            continue
        found.append((path.parent.name, thread))
    return found
