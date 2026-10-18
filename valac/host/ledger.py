"""Valac's memory of every finding: one JSON file per fingerprint, so a known defect never opens a second issue.

Pure except for the store folder, so the tests run on a temporary one. The states are in docs/VALAC.md.
"""

import hashlib
import re
import time
from pathlib import Path

import config
import jsonstore

ACTIVE = ("issue_open", "pr_open")  # a finding that already has an issue or a PR waiting for people
# At most this many Valac issues and PRs wait for people at once, so the patrol never floods the team.
MAX_ACTIVE = int(config.number("VALAC_MAX_ACTIVE", 3))
MIN_SCORE = int(config.number("VALAC_MIN_SCORE", 40))
SNOOZE_SECONDS = int(config.number("VALAC_SNOOZE_DAYS", 30)) * 24 * 3600
FINGERPRINT = re.compile(r"^[0-9a-f]{8,40}$")


def fingerprint(finding: dict) -> str:
    """The host computes it: the sandbox has no python3 or sha1sum, and a model's hash is not stable.

    No `class`: the model picks it on each run, so one defect could change key and open a second issue."""
    location = finding.get("location") or {}
    key = "|".join(str(finding.get(k) or "") for k in ("dataset", "eventType")) + "|" + str(location.get("file") or "")
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def path(store: Path, fingerprint: str) -> Path:
    if not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("a fingerprint is a hex digest")  # it becomes a file name
    return store / f"{fingerprint}.json"


def load(store: Path, fingerprint: str) -> dict | None:
    return jsonstore.read(path(store, fingerprint))


def save(store: Path, record: dict) -> None:
    jsonstore.write(path(store, record["fingerprint"]), record, indent=2)


def every(store: Path) -> list[dict]:
    return [record for file in sorted(store.glob("*.json")) if (record := jsonstore.read(file))]


def active_count(store: Path) -> int:
    return sum(1 for record in every(store) if record.get("state") in ACTIVE)


def snoozed(record: dict | None, now: float) -> bool:
    return bool(record) and record.get("state") == "snoozed" and now < record.get("snoozedUntil", 0)


def select(findings: list[dict], store: Path, now: float | None = None) -> list[dict]:
    """The findings to act on now: new or back from a snooze, over the score floor, best first, within the cap."""
    now = time.time() if now is None else now
    room = MAX_ACTIVE - active_count(store)
    chosen = []
    for finding in sorted(findings, key=lambda f: -(f.get("score") or 0)):
        if room <= 0:
            break
        fingerprint = str(finding.get("fingerprint", ""))
        if (finding.get("score") or 0) < MIN_SCORE or not FINGERPRINT.fullmatch(fingerprint):
            continue
        known = load(store, fingerprint)
        if known and (known.get("state") in ACTIVE + ("deployed", "verified") or snoozed(known, now)):
            continue
        chosen.append(finding)
        room -= 1
    return chosen


def opened(store: Path, finding: dict, issue: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    known = load(store, finding["fingerprint"]) or {}
    record = {**known, **{k: finding.get(k) for k in ("fingerprint", "class", "dataset", "eventType", "score",
                                                         "evidence", "location", "humanOnly")},
              "state": "issue_open", "issue": issue, "firstSeen": known.get("firstSeen", now), "lastSeen": now}
    save(store, record)
    return record


def by_number(store: Path, number: int) -> dict | None:
    """The finding whose issue or PR has this number."""
    return next((r for r in every(store) if number in (r.get("issue"), r.get("pr"))), None)


def closed(store: Path, record: dict, merged: bool, reason: str, now: float | None = None) -> dict:
    """A merge moves the finding on. Any other close is a reject: the fingerprint sleeps for 30 days."""
    now = time.time() if now is None else now
    if merged:
        record.update(state="deployed", mergedAt=now)
    else:
        record.update(state="snoozed", snoozedUntil=now + SNOOZE_SECONDS, rejectReason=reason)
    save(store, record)
    return record
