#!/usr/bin/env python3
"""GitHub App authentication: the installation tokens the hosts and the sandboxes use when Legion runs as an App.

With LEGION_GITHUB_APP_ID and LEGION_GITHUB_APP_INSTALLATION_ID set and the App's private key in
secrets/github_app_key.pem, no static GitHub token is needed: github.token() asks this module for one.
- The host token has every permission the installation grants. It lives an hour, so it is kept in memory and minted
  again five minutes before it ends.
- The sandbox token is minted per run with read-only permissions, so a sandbox can never write, whatever the App may.
Usage from a shell (run.sh): github_app.py sandbox-token, which prints a fresh read-only token.
"""

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
from paths import SECRETS  # noqa: E402

API = "https://api.github.com/"
KEY_FILE = SECRETS / "github_app_key.pem"
REFRESH_SECONDS = 300  # mint a new token this long before the old one expires
JWT_SECONDS = 540  # GitHub allows ten minutes; a little less covers clock drift
# What a sandbox may do: read the code, the issues and the PRs. Nothing else.
SANDBOX_PERMISSIONS = {"contents": "read", "issues": "read", "pull_requests": "read", "metadata": "read"}

_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}  # kind -> (token, expires epoch)


def enabled() -> bool:
    return bool(config.text("LEGION_GITHUB_APP_ID") and config.text("LEGION_GITHUB_APP_INSTALLATION_ID"))


def app_jwt(now: float | None = None) -> str:
    """The App's own JWT, signed with its private key. PyJWT comes from shared/host/requirements.txt."""
    import jwt  # imported here, so a host that runs no App, and the tests, need no package

    now = time.time() if now is None else now
    payload = {"iat": int(now) - 60, "exp": int(now) + JWT_SECONDS, "iss": config.text("LEGION_GITHUB_APP_ID")}
    return jwt.encode(payload, KEY_FILE.read_text(), algorithm="RS256")


def post(path: str, bearer: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path, method="POST", data=json.dumps(body or {}).encode(),
        headers={"Authorization": f"Bearer {bearer}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": config.USER_AGENT,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def get(path: str, bearer: str) -> dict:
    request = urllib.request.Request(
        API + path, headers={"Authorization": f"Bearer {bearer}", "Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def expiry(stamp: str) -> float:
    return time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone


def mint(read_only: bool) -> tuple[str, float]:
    """A new installation token, read-only for a sandbox, and the epoch it expires."""
    installation = config.text("LEGION_GITHUB_APP_INSTALLATION_ID")
    body = {"permissions": SANDBOX_PERMISSIONS} if read_only else {}
    answer = post(f"app/installations/{installation}/access_tokens", app_jwt(), body)
    return answer["token"], expiry(answer["expires_at"])


def token(read_only: bool = False, now: float | None = None) -> str:
    """The host token (cached), or a read-only token for a sandbox (cached the same way)."""
    now = time.time() if now is None else now
    kind = "sandbox" if read_only else "host"
    with _lock:
        held = _cache.get(kind)
        if held is None or held[1] - now < REFRESH_SECONDS:
            held = _cache[kind] = mint(read_only)
        return held[0]


_identity: dict | None = None


def identity() -> dict:
    """{"login", "id"} of the App's bot user, as `legion[bot]`: the author of every commit, PR and review it makes."""
    global _identity
    if _identity is None:
        slug = get("app", app_jwt())["slug"]
        user = get(f"users/{slug}%5Bbot%5D", token())
        _identity = {"login": user["login"], "id": user["id"]}
    return _identity


if __name__ == "__main__":
    if sys.argv[1:] != ["sandbox-token"]:
        sys.exit("usage: github_app.py sandbox-token")
    print(token(read_only=True))
