#!/usr/bin/env python3
"""Creates or updates the repository webhook that feeds the hooks service, and stores its secret and id on the host.

Usage: webhook.py https://<host>/webhooks/github [--rotate]
The events are every event some agent's subscriptions.json takes, so a new agent needs only a new run. The secret is
generated here and goes to secrets/github_webhook_secret; it never prints. --rotate writes a new secret on an existing
hook. The host token needs the repository Webhooks permission (read and write).
"""

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import github as gh  # noqa: E402
import paths  # noqa: E402

import hooks  # noqa: E402

SECRET_FILE = paths.SECRETS / hooks.SECRET_FILE
ID_FILE = paths.SECRETS / hooks.HOOK_ID_FILE


def wanted_events(agents_dir: Path = paths.REPO_DIR) -> list[str]:
    return hooks.events(hooks.load_subscriptions(agents_dir, paths.agents_on_host()))


def find(url: str) -> dict | None:
    for page in gh.pages(f"repos/{gh.REPO}/hooks?per_page=100"):
        for hook in page:
            if (hook.get("config") or {}).get("url") == url:
                return hook
    return None


def write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(value)


def ensure(url: str, rotate: bool = False) -> str:
    """Returns what it did: created, updated or rotated. The secret file and the id file match the hook after it."""
    events = wanted_events()
    hook = find(url)
    secret = None if (hook and not rotate and SECRET_FILE.exists()) else secrets.token_hex(32)
    config = {"url": url, "content_type": "json", "insecure_ssl": "0", **({"secret": secret} if secret else {})}
    if hook is None:
        hook = gh.github(f"repos/{gh.REPO}/hooks", method="POST",
                         body={"name": "web", "active": True, "events": events, "config": config})
        done = "created"
    else:
        hook = gh.github(f"repos/{gh.REPO}/hooks/{hook['id']}", method="PATCH",
                         body={"active": True, "events": events, "config": config})
        done = "rotated" if secret else "updated"
    if secret:
        write_secret(SECRET_FILE, secret)
    write_secret(ID_FILE, str(hook["id"]))
    return f"{done} hook {hook['id']} for {', '.join(events)}"


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: webhook.py https://<host>/webhooks/github [--rotate]")
    print(ensure(sys.argv[1], rotate="--rotate" in sys.argv[2:]))
