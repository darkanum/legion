"""The Legion run log: one Axiom event per run, stage change or review, in one dataset, one `service` per agent.

Every event has the same fields (level, type, message, environment, service, module, traceId, durationMs,
errorMessage, errorType), so one APL query reads every agent the same way. traceId is the Slack thread key or the PR head.
With no ingest token the log stays local, so a host without Axiom still runs.
"""

import json
import logging
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config
from paths import SECRETS

TOKEN_FILE = SECRETS / "axiom_ingest_token"
HOSTNAME = socket.gethostname()  # splits the dashboards by VM
URL = f"https://api.axiom.co/v1/datasets/{urllib.parse.quote(config.LOG_DATASET)}/ingest"  # LEGION_LOG_DATASET

log = logging.getLogger("legion-axiom")


def emit(service: str, event_type: str, message: str, trace_id: str, level: str = "info", module: str = "listener",
         **fields) -> None:
    """Sends one event. Never raises: a lost log line must not stop the agent. With no token it only logs locally."""
    record = {"_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "level": level, "type": event_type,
              "message": message, "environment": config.ENVIRONMENT, "service": service, "module": module,
              "traceId": trace_id, "host": HOSTNAME, **{k: v for k, v in fields.items() if v is not None}}
    if not TOKEN_FILE.exists():
        log.info("axiom off (no token): %s", event_type)
        return
    try:
        request = urllib.request.Request(
            URL, method="POST", data=json.dumps([record]).encode(),
            headers={"Authorization": f"Bearer {TOKEN_FILE.read_text().strip()}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception as error:
        log.warning("axiom ingest failed for %s: %s", event_type, type(error).__name__)


def file_fields(path: Path) -> dict:
    """Duration, turns and cost of one `claude -p` call, from the JSON that Claude Code writes."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return {"durationMs": data.get("duration_ms"), "turns": data.get("num_turns"), "costUsd": data.get("total_cost_usd")}


def run_fields(run_dir: Path) -> dict:
    """Totals of one sandbox run: its claude.json, or the sum of its claude-<stage>.json files when it ran several calls."""
    single = run_dir / "claude.json"
    if single.exists():
        return file_fields(single)
    parts = [f for f in (file_fields(path) for path in sorted(run_dir.glob("claude-*.json"))) if f]
    if not parts:
        return {}
    return {key: sum(part.get(key) or 0 for part in parts) for key in ("durationMs", "turns", "costUsd")}
