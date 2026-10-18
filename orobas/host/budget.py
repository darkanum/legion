"""Orobas's share of the Claude seat it shares with Marbas and Valac: a cap per 5-hour window, and a pause on a 429.

A seat reports no window size, so OROBAS_SEAT_WINDOW_USD is a measure: the equivalent cost one window held before every
call answered 429 (about 160 USD on a Max seat when this was written). OROBAS_SEAT_SHARE is the part Orobas may spend,
so the fixers keep room. Change both in legion.env.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402
import jsonstore  # noqa: E402

SEAT_WINDOW_USD = config.number("OROBAS_SEAT_WINDOW_USD", 160.0)
SHARE = config.number("OROBAS_SEAT_SHARE", 0.35)
CAP_USD = SEAT_WINDOW_USD * SHARE
WINDOW_SECONDS = 5 * 3600
LIMIT_STATUS = 429
RESET = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(UTC\)", re.IGNORECASE)
NO_RESET_SECONDS = 3600  # a 429 that names no reset time waits one hour
# Why no new review may start: the `reason` of orobas.budget.reached and orobas.review.parked.
SEAT_LIMIT = "seat_limit"
CAP = "cap"
DAILY = "daily"
# Full reviews a UTC day, oldest PR first; the light check after a push does not count.
# Set OROBAS_DAILY_FULL_REVIEWS in legion.env to change it without a code change.
DAILY_FULL_REVIEWS = int(config.number("OROBAS_DAILY_FULL_REVIEWS", 50))


def _read(path: Path) -> dict:
    return jsonstore.read(path) or {"spent": [], "paused_until": None}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonstore.write(path, data)


def spent(path: Path, now: float) -> float:
    """USD that Orobas spent in the last 5 hours. A rolling window, so it never overstates the room left."""
    return sum(usd for at, usd in _read(path)["spent"] if now - at < WINDOW_SECONDS)


def record(path: Path, usd: float, now: float) -> None:
    data = _read(path)
    data["spent"] = [[at, cost] for at, cost in data["spent"] if now - at < WINDOW_SECONDS] + [[now, usd]]
    _write(path, data)


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


def full_reviews(path: Path, now: float) -> int:
    data = _read(path)
    return data.get("full", 0) if data.get("day") == _day(now) else 0


def take_full_review(path: Path, now: float) -> bool:
    """Counts one full review for today. False when today's DAILY_FULL_REVIEWS are spent."""
    data = _read(path)
    used = full_reviews(path, now)
    if used >= DAILY_FULL_REVIEWS:
        return False
    data.update(day=_day(now), full=used + 1)
    _write(path, data)
    return True


def give_back_full_review(path: Path, now: float) -> None:
    """A counted review that did not run (skipped or out of scope) frees its place."""
    data = _read(path)
    if data.get("day") == _day(now) and data.get("full", 0) > 0:
        data["full"] -= 1
        _write(path, data)


def pause(path: Path, until: float) -> None:
    data = _read(path)
    data["paused_until"] = max(until, data.get("paused_until") or 0)
    _write(path, data)


def blocked(path: Path, now: float) -> str | None:
    """Why no new review may start now, or None."""
    data = _read(path)
    if (data.get("paused_until") or 0) > now:
        return SEAT_LIMIT
    if spent(path, now) >= CAP_USD:
        return CAP
    return None


def window_pct(usd: float) -> float:
    """The share of the whole seat window that `usd` is, in percent."""
    return round(100 * usd / SEAT_WINDOW_USD, 1)


def reset_at(text: str, now: float) -> float:
    """The epoch of "resets 3:30pm (UTC)": the next time of day after `now`."""
    match = RESET.search(text or "")
    if not match:
        return now + NO_RESET_SECONDS
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
    current = datetime.fromtimestamp(now, timezone.utc)
    reset = current.replace(hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0)
    if reset <= current:
        reset += timedelta(days=1)
    return reset.timestamp()


def limit_hit(run_dir: Path) -> str | None:
    """The message of the first stage that the seat refused with a 429, or None."""
    for path in sorted(run_dir.glob("claude-*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("api_error_status") == LIMIT_STATUS:
            return str(data.get("result") or "")
    return None


def run_usage(run_dir: Path) -> dict:
    """Tokens and cost of one run, summed over its `claude -p` calls. Input counts the cached tokens too."""
    tokens_in = tokens_out = 0
    cost = 0.0
    for path in run_dir.glob("claude-*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        usage = data.get("usage") or {}
        tokens_in += sum(int(usage.get(key) or 0) for key in
                         ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        tokens_out += int(usage.get("output_tokens") or 0)
        cost += float(data.get("total_cost_usd") or 0)
    return {"tokensIn": tokens_in, "tokensOut": tokens_out, "costUsd": round(cost, 4), "windowPct": window_pct(cost)}


def run_cost(run_dir: Path) -> float:
    return run_usage(run_dir)["costUsd"]
