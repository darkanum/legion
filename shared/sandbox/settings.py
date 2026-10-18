#!/usr/bin/env python3
"""Writes the two files the sandbox image takes from the settings: Claude Code's managed settings and the proxy allowlist.

Usage: settings.py <out-dir>. shared/sandbox/setup.sh runs it before each image build, so a change to legion.env reaches
the image on the next setup. The base lists below hold what every repository needs; legion.env adds the rest.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402

# Read-only git, search and the two telemetry commands. The repository's own tools come from LEGION_SANDBOX_ALLOW.
ALLOW = ["Read", "Glob", "Grep", "Edit", "Write",
         "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)", "Bash(git add:*)",
         "Bash(git commit:*)", "Bash(git restore:*)", "Bash(git grep:*)", "Bash(gh issue view:*)",
         "Bash(ls:*)", "Bash(cat:*)", "Bash(find:*)", "Bash(grep:*)", "Bash(rg:*)", "Bash(mkdir:*)", "Bash(jq:*)",
         "Bash(legion-axiom:*)", "Bash(legion-posthog:*)"]
# Nothing in the sandbox may push, reach the network on its own, or read the environment.
DENY = ["Bash(git push:*)", "Bash(git remote:*)", "Bash(git config:*)", "Bash(gh pr:*)", "Bash(gh api:*)",
        "Bash(gh auth:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(ssh:*)", "Bash(env:*)", "Bash(printenv:*)",
        "WebFetch", "WebSearch", "Edit(**/.env*)", "Write(**/.env*)", "Read(**/.env*)", "Edit(//out/**)", "Write(//out/**)"]
HOSTS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
         "release-assets.githubusercontent.com", "api.anthropic.com", "api.axiom.co"]


def guarded_rules(guarded: tuple[str, ...]) -> list[str]:
    """Edit and Write denied on each guarded prefix: a folder gets `/**`, a file stays as it is."""
    rules = []
    for prefix in guarded:
        pattern = f"{prefix}**" if prefix.endswith("/") else prefix
        rules += [f"Edit({pattern})", f"Write({pattern})"]
    return rules


def managed_settings() -> dict:
    allow = ALLOW + [rule for rule in config.items("LEGION_SANDBOX_ALLOW", "Bash(pnpm:*),Bash(npm:*),Bash(npx:*)")
                     if rule not in ALLOW]
    return {"permissions": {"allow": allow, "deny": DENY + guarded_rules(config.GUARDED)},
            "enableAllProjectMcpServers": False, "allowedMcpServers": [],
            "env": {"DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}}


def allowlist() -> str:
    """One anchored regex per host, for tinyproxy's filter."""
    hosts = HOSTS + list(config.items("LEGION_PROXY_HOSTS", "registry.npmjs.org"))
    posthog = config.text("LEGION_POSTHOG_HOST")
    if posthog:
        hosts.append(posthog)
    seen = []
    for host in hosts:
        if host and host not in seen:
            seen.append(host)
    return "".join(f"^{re.escape(host)}$\n" for host in seen)


def write(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "managed-settings.json").write_text(json.dumps(managed_settings(), indent=2) + "\n")
    (out / "allowlist").write_text(allowlist())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: settings.py <out-dir>")
    write(Path(sys.argv[1]))
    print(f"wrote managed-settings.json and allowlist to {os.path.abspath(sys.argv[1])}")
