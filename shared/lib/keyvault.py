"""Writes an agent VM's secrets from Key Vault into secrets/, mode 600, with the VM's managed identity.

Run by shared/host/setup.sh on an Azure VM whose legion.env sets LEGION_KEY_VAULT. Any other host writes the files
by hand (docs/HOST.md) and never runs it.
Usage: keyvault.py <vault> <agent>...
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from paths import REPO_DIR, SECRETS

IMDS = "http://169.254.169.254/metadata/identity/oauth2/token"
API_VERSION = "7.4"

AGENTS_DIR = REPO_DIR  # each agent is a top-level folder of this repository
# Each VM has its own repo webhook: the vault holds one per agent, the VM keeps the file name legion-hooks reads.
WEBHOOK_FILES = ["github_webhook_secret", "github_webhook_id"]


def agent_secrets(agent: str, agents_dir: Path = AGENTS_DIR) -> list[str]:
    """The secret files that <agent>/secrets.txt lists, one per line."""
    lines = (agents_dir / agent / "secrets.txt").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def vault_names(agent: str, agents_dir: Path = AGENTS_DIR) -> dict[str, str]:
    """Secret file name => Key Vault secret name, for one agent. Key Vault allows no underscore, so names use dashes."""
    names = {file: file.replace("_", "-") for file in agent_secrets(agent, agents_dir)}
    names.update({file: f"{file.replace('_', '-')}-{agent}" for file in WEBHOOK_FILES})
    return names


def get_json(url: str, headers: dict) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.loads(response.read())


def token() -> str:
    query = urllib.parse.urlencode({"api-version": "2018-02-01", "resource": "https://vault.azure.net"})
    return get_json(f"{IMDS}?{query}", {"Metadata": "true"})["access_token"]


def write_secrets(vault: str, agents: list[str], folder: Path = SECRETS, agents_dir: Path = AGENTS_DIR) -> list[str]:
    """Writes every secret the agents need and returns the file names. Never prints a value."""
    bearer = {"Authorization": f"Bearer {token()}"}
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = []
    for agent in agents:
        for file, name in vault_names(agent, agents_dir).items():
            try:
                value = get_json(f"https://{vault}.vault.azure.net/secrets/{name}?api-version={API_VERSION}", bearer)["value"]
            except urllib.error.HTTPError as error:
                # The webhook pair lands in the vault when the GitHub hook is created (docs/HOST.md).
                # Without the file, hooks/setup.sh skips legion-hooks instead of starting it.
                if error.code == 404 and file in WEBHOOK_FILES:
                    print(f"not in the vault yet: {name}", file=sys.stderr)
                    continue
                raise
            path = folder / file
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(value)
            os.chmod(path, 0o600)
            written.append(file)
    return sorted(set(written))


if __name__ == "__main__":
    if len(sys.argv) < 3 or any(not (AGENTS_DIR / agent / "secrets.txt").is_file() for agent in sys.argv[2:]):
        sys.exit("usage: keyvault.py <vault> <agent>... (each agent needs <agent>/secrets.txt)")
    print("wrote", ", ".join(write_secrets(sys.argv[1], sys.argv[2:])))
