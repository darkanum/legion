"""Every setting that ties Legion to one team, read once from the environment.

On the host the values come from legion.env (systemd loads it with EnvironmentFile=, and shared/paths.sh sources it), so
nothing here names a company, a repository or a person. legion.env.example lists every key with its default.
"""

import os
import re


def text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def items(name: str, default: str = "") -> tuple[str, ...]:
    """A comma list: `a,b , c` is ("a", "b", "c"). Empty entries are dropped."""
    return tuple(part.strip() for part in text(name, default).split(",") if part.strip())


def flag(name: str, default: bool) -> bool:
    return text(name, "true" if default else "false").lower() == "true"


def number(name: str, default: float) -> float:
    try:
        return float(text(name, str(default)))
    except ValueError:
        return default


# The repository every agent works on, and the branch that is production.
REPO = text("LEGION_REPO", "owner/repo")
BASE_BRANCH = text("LEGION_BASE_BRANCH", "main")
# The people whose words count: an org team (`org/slug`), or, when empty, every collaborator with push access.
TEAM = text("LEGION_TEAM")
# Who reviews a PR an agent opens. Logins and team slugs of the repository's org.
PR_REVIEWERS = list(items("LEGION_PR_REVIEWERS"))
PR_TEAM_REVIEWERS = list(items("LEGION_PR_TEAM_REVIEWERS"))
# No agent change may touch these prefixes: Marbas and Valac refuse the patch, Orobas never approves the PR.
GUARDED = items("LEGION_GUARDED_PATHS", ".github/,infra/,.claude/,.legion/")
# Package folders that get a `<root>/<package>` label when it exists, as in `apps/web`. Empty: no package labels.
PACKAGE_ROOTS = items("LEGION_PACKAGE_ROOTS")
LOCKFILES = items("LEGION_LOCKFILES", "pnpm-lock.yaml,package-lock.json,yarn.lock,go.sum,poetry.lock,uv.lock,Cargo.lock")
# The GitHub deployment environments that count as production; empty turns the deploy watch off.
DEPLOY_ENVIRONMENT = text("LEGION_DEPLOY_ENVIRONMENT", "production")
# The Slack mention that a hand-off pings, such as <!subteam^S0123> or <@U0123>. Empty pings nobody.
ONCALL = text("LEGION_ONCALL_MENTION")
# The file on the base branch that maps a GitHub login to a Slack user id.
SLACK_USER_MAP = text("LEGION_SLACK_USER_MAP", ".github/slack-user-map.json")
# The run log: one Axiom dataset for every agent, one `service` per agent. No token turns it off.
LOG_DATASET = text("LEGION_LOG_DATASET", "legion")
ENVIRONMENT = text("LEGION_ENVIRONMENT", "production")
# Where the hooks service listens behind the reverse proxy: 172.17.0.1 when the proxy runs in a container.
HOOKS_HOST = text("LEGION_HOOKS_HOST", "127.0.0.1")
HOOKS_PORT = int(number("LEGION_HOOKS_PORT", 8645))


def agents() -> set[str] | None:
    """The agents this host runs (LEGION_AGENTS, a comma list), or None for every agent folder. Read at each call."""
    return set(items("LEGION_AGENTS")) or None


# Sent with every GitHub call.
USER_AGENT = text("LEGION_USER_AGENT", "legion-agents")

# Where Orobas reads the review rulebook in the repository, on the base branch. A repository without it gets the starter
# rulebook in orobas/rules/.
RULES_DIR = text("LEGION_RULES_DIR", ".legion/rules").strip("/")

# The models. The deep one plans, fixes and reviews; the fast one triages and writes.
MODEL = text("LEGION_MODEL", "claude-opus-5-5")
FAST_MODEL = text("LEGION_FAST_MODEL", "claude-sonnet-5-5")

# What the issue form puts before a bug title, such as "🐞 [BUG] " or "bug: ". A PR title drops it.
ISSUE_TITLE_PREFIX = os.environ.get("LEGION_ISSUE_TITLE_PREFIX", "[bug] ")  # not stripped: the space is part of it
TITLE_PREFIX = re.compile(r"^\W*\[[A-Za-z]+\]\s*|^bug:\s*", re.IGNORECASE)


def repo_parts() -> tuple[str, str]:
    owner, _, name = REPO.partition("/")
    return owner, name
