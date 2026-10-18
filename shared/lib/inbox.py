"""The local event inbox of an agent: the hooks service writes one JSON file per GitHub event, the agent drains it.

A file holds only `delivery`, `event`, `action`, `number`, `sha` and `received_at`. The payload stays untrusted and is
never stored: an agent reads the facts it needs from the API. Draining reads a local folder and never calls GitHub.
"""

import calendar
import os
import time
from pathlib import Path
from typing import Callable

import jsonstore
from paths import WORKSPACE
KEYS = ("delivery", "event", "action", "number", "sha", "received_at")


def folder(agent: str, root: Path | None = None) -> Path:
    return (root or WORKSPACE) / agent / "inbox"


def write(agent: str, event: dict, name: str, root: Path | None = None) -> Path:
    """One file per event, written atomically, holding only the contract keys."""
    return jsonstore.write(folder(agent, root) / name, {key: event.get(key) for key in KEYS})


def valid(event: object) -> bool:
    return (isinstance(event, dict) and isinstance(event.get("delivery"), str) and isinstance(event.get("event"), str)
            and (event.get("number") is None or isinstance(event.get("number"), int)))


def received_epoch(event: dict | None) -> float | None:
    """The epoch second the hooks service received the event, or None when the event carries no time."""
    try:
        return float(calendar.timegm(time.strptime((event or {})["received_at"], "%Y-%m-%dT%H:%M:%SZ")))
    except (KeyError, TypeError, ValueError):
        return None


def since_ms(start: float | None, now: float | None = None) -> int | None:
    """Milliseconds from a received epoch to now: the end-to-end latency an agent reports."""
    return None if start is None else max(0, int(((now if now is not None else time.time()) - start) * 1000))


def drain(agent: str, root: Path | None = None) -> list[dict]:
    """Every event in file-name order, each file deleted after it is read. A bad file moves to inbox/bad/."""
    source = folder(agent, root)
    if not source.exists():
        return []
    events = []
    for path in sorted(source.glob("*.json")):
        event = jsonstore.read(path)
        if valid(event):
            events.append(event)
            path.unlink(missing_ok=True)
        else:
            (source / "bad").mkdir(exist_ok=True)
            os.replace(path, source / "bad" / path.name)
    return events


def consume(agent: str, handle: Callable[[dict], None], failed: Callable[[dict, Exception], None],
            root: Path | None = None) -> None:
    """Drains the inbox into `handle`. One failing event goes to `failed` and never stops the rest of the batch."""
    for event in drain(agent, root):
        try:
            handle(event)
        except Exception as error:
            failed(event, error)
