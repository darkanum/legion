"""Every path on the Legion host, defined once. shared/paths.sh holds the same values for the shell scripts.

LEGION_ROOT moves the whole tree, for a host whose backup covers another folder.
"""

import os
from pathlib import Path

ROOT = Path(os.environ.get("LEGION_ROOT", "/opt/legion"))
REPO_DIR = ROOT / "repo"  # the copy of this repository that the services run from
SECRETS = ROOT / "secrets"  # one file per secret, mode 600, owner root
MEMORY = ROOT / "memory"  # memory/<agent>/: notes an agent reads, never writes from a sandbox
WORKSPACE = ROOT / "workspace"  # workspace/<agent>/: state, runs and the event inbox
VENV = ROOT / "venv"
ENV_FILE = ROOT / "legion.env"  # the settings: config.py reads them from the environment that systemd loads from here


def agents_on_host() -> set[str] | None:
    """The agents this VM runs, from LEGION_AGENTS in legion.env (a comma list, no spaces). None runs every agent."""
    names = {name for name in os.environ.get("LEGION_AGENTS", "").split(",") if name}
    return names or None


def workspace(agent: str) -> Path:
    return WORKSPACE / agent


def memory(agent: str) -> Path:
    return MEMORY / agent
