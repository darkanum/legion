#!/usr/bin/env python3
"""The Legion GitHub webhook service: every GitHub event that moves an agent arrives here, and no agent polls GitHub.

Caddy (shared/host/Caddyfile), or any reverse proxy, sends `/webhooks/github` to this server. It checks the signature, answers 202, and writes one small event
file into the inbox of each agent whose subscriptions.json takes the event. See docs/HOOKS.md.
"""

import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axiom  # noqa: E402
import github as gh  # noqa: E402
import inbox  # noqa: E402
import paths  # noqa: E402

SERVICE = "hooks"  # the Axiom `service` and the systemd unit suffix
HOST = os.environ.get("LEGION_HOOKS_HOST", "127.0.0.1")  # 172.17.0.1 when the reverse proxy runs in a container
PORT = int(os.environ.get("LEGION_HOOKS_PORT", "8645"))
MAX_BODY = 1_000_000
HEARTBEAT_SECONDS = 300
STATE = paths.workspace("hooks") / "state.json"
SECRET_FILE = "github_webhook_secret"
HOOK_ID_FILE = "github_webhook_id"

log = logging.getLogger(SERVICE)

Subscriptions = dict[str, dict[str, frozenset[str] | None]]  # agent -> event -> actions; None takes every action


def load_subscriptions(agents_dir: Path, only: set[str] | None = None) -> Subscriptions:
    """Reads <agent>/subscriptions.json for every agent folder, or only for `only`. The service names no agent itself."""
    found: Subscriptions = {}
    for path in sorted(agents_dir.glob("*/subscriptions.json")):
        spec = json.loads(path.read_text())
        if only is not None and spec["agent"] not in only:
            continue
        found[spec["agent"]] = {event: None if actions is None else frozenset(actions)
                                for event, actions in spec["events"].items()}
    return found


def subscribers_for(subs: Subscriptions, event: str, action: str | None) -> list[str]:
    return sorted(agent for agent, events in subs.items()
                  if event in events and (events[event] is None or action in events[event]))


def events(subs: Subscriptions) -> list[str]:
    return sorted({event for agent_events in subs.values() for event in agent_events})


SUBSCRIPTIONS: Subscriptions = {}


def verify(secret: bytes, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def first_pr(items: list | None) -> int | None:
    number = ((items or [{}])[0] or {}).get("number")
    return number if isinstance(number, int) else None


def summarize(event: str, delivery: str, payload: dict) -> dict:
    """The event file: who and what, never the payload text."""
    number, sha = None, None
    if event in ("pull_request", "pull_request_review"):
        pull = payload.get("pull_request") or {}
        number, sha = pull.get("number"), (pull.get("head") or {}).get("sha")
    elif event == "check_suite":
        suite = payload.get("check_suite") or {}
        number, sha = first_pr(suite.get("pull_requests")), suite.get("head_sha")
    elif event == "workflow_run":
        run = payload.get("workflow_run") or {}
        number, sha = first_pr(run.get("pull_requests")), run.get("head_sha")
    elif event == "deployment_status":
        sha = (payload.get("deployment") or {}).get("sha")
    elif event == "issues":
        number = (payload.get("issue") or {}).get("number")
    return {"delivery": delivery, "event": event, "action": payload.get("action"),
            "number": number if isinstance(number, int) else None, "sha": sha if isinstance(sha, str) else None,
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def dispatch(event: str, delivery: str, payload: dict) -> list[str]:
    summary = summarize(event, delivery, payload)
    agents = subscribers_for(SUBSCRIPTIONS, event, summary["action"])
    for agent in agents:
        inbox.write(agent, summary, f"{int(time.time() * 1000)}-{delivery}.json")
    return agents


class Handler(BaseHTTPRequestHandler):
    secret = b""

    def answer(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.answer(413)
            return
        body = self.rfile.read(length)
        delivery = self.headers.get("X-GitHub-Delivery", "")
        event = self.headers.get("X-GitHub-Event", "")
        if not verify(self.secret, body, self.headers.get("X-Hub-Signature-256")):
            axiom.emit(SERVICE, "hooks.rejected", "bad webhook signature", delivery or "unknown", level="warn")
            self.answer(401)
            return
        try:
            payload = json.loads(body)
        except ValueError:
            self.answer(400)
            return
        agents = [] if event == "ping" else dispatch(event, delivery, payload)
        self.answer(202)
        axiom.emit(SERVICE, "hooks.received", f"{event} to {len(agents)} agent(s)", delivery, event=event,
                   action=payload.get("action"), agents=",".join(agents) or None, subscribers=len(agents))

    def log_message(self, *args) -> None:
        pass  # axiom.emit records each delivery; the default access log only adds noise to journald


def redeliver(hook_id: str, state_file: Path = STATE) -> int:
    """At boot: asks GitHub to send again each failed delivery since the last run. The first run only records a mark.

    GitHub lists the newest delivery first, so this pages back to the mark, then sends the oldest first: an older push
    that lands last would move a PR back to an old head.
    """
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    last = state.get("last_delivery_id")
    newest, missed = last or 0, []
    for page in gh.pages(f"repos/{gh.REPO}/hooks/{hook_id}/deliveries?per_page=100"):
        newest = max([newest] + [d["id"] for d in page])
        if last is None:
            break
        missed += [d for d in page if d["id"] > last and not d.get("redelivery")
                   and not 200 <= (d.get("status_code") or 0) < 300]
        if not page or min(d["id"] for d in page) <= last:
            break
    for d in sorted(missed, key=lambda d: d["id"]):
        gh.github(f"repos/{gh.REPO}/hooks/{hook_id}/deliveries/{d['id']}/attempts", method="POST")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_delivery_id": newest}))
    return len(missed)


def redeliver_at_boot(hook_id: str) -> None:
    try:
        sent = redeliver(hook_id)
        axiom.emit(SERVICE, "hooks.redelivered", f"asked GitHub to redeliver {sent} event(s)", "boot", count=sent)
    except Exception as error:
        log.exception("redelivery failed")
        axiom.emit(SERVICE, "hooks.redeliver.failed", "redelivery failed", "boot", level="warn",
                   errorType=type(error).__name__, errorMessage=gh.describe(error))


def heartbeat() -> None:
    while True:
        axiom.emit(SERVICE, "hooks.heartbeat", "hooks service alive", "heartbeat")
        time.sleep(HEARTBEAT_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    SUBSCRIPTIONS.update(load_subscriptions(paths.REPO_DIR, paths.agents_on_host()))
    log.info("subscriptions: %s", {agent: sorted(e) for agent, e in SUBSCRIPTIONS.items()})
    Handler.secret = (gh.SECRETS / SECRET_FILE).read_text().strip().encode()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=heartbeat, daemon=True).start()
    hook_id = gh.SECRETS / HOOK_ID_FILE
    if hook_id.exists():
        # After the server is up: GitHub sends the redeliveries at once and gives each one 10 s for an answer.
        threading.Thread(target=redeliver_at_boot, args=(hook_id.read_text().strip(),), daemon=True).start()
    log.info("listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
