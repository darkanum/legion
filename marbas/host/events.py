"""Which open Marbas thread a GitHub inbox event moves. Pure: the listener reads the threads and calls pr.step()."""

from typing import Callable, Iterable

WATCHED = ("pr_open", "deploying")


def owns(thread: dict, event: dict) -> bool:
    """A PR event moves the thread of that PR; a deploy or workflow event on the merge commit moves a deploying thread."""
    if thread.get("stage") not in WATCHED:
        return False
    if event.get("number") is not None and event["number"] == thread.get("pr"):
        return True
    sha = event.get("sha")
    return bool(sha) and thread.get("stage") == "deploying" and sha == thread.get("merge_sha")


def timeouts_due(thread: dict, now: float, marks: tuple[int, ...]) -> list[int]:
    """The deploy timeouts that passed since the merge and were not checked yet. A timer, not a GitHub poll."""
    if thread.get("stage") != "deploying" or "merged_at" not in thread:
        return []
    checked = set(thread.get("timeouts_checked", []))
    return [mark for mark in marks if now - thread["merged_at"] > mark and mark not in checked]


def deliver(event: dict, threads: Iterable[tuple[str, dict]], advance: Callable[[str, dict], None],
            failed: Callable[[str | None, dict, Exception], None]) -> None:
    """Moves every thread the event owns. A failure on one thread is reported and never stops the others."""
    for key, thread in threads:
        if not owns(thread, event):
            continue
        try:
            advance(key, event)
        except Exception as error:
            failed(key, event, error)
