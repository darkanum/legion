#!/usr/bin/env bash
# Installs or updates Legion on the host, in order: the VM (only with LEGION_HOSTNAME), the shared sandbox, the GitHub
# webhook service, then each agent this host runs. Run as root from the repo copy at /opt/legion/repo. Safe to run again.
# Usage: setup.sh [host|sandbox|hooks|<agent>]
set -euo pipefail
cd "$(dirname "$0")"
source shared/paths.sh
part=${1:-all}

[ "$(pwd -P)" = "$(cd "$LEGION_REPO_DIR" 2>/dev/null && pwd -P)" ] \
  || { echo "run this from $LEGION_REPO_DIR: clone the repo there first (docs/HOST.md)" >&2; exit 1; }
[ -f "$LEGION_ENV_FILE" ] || { echo "write $LEGION_ENV_FILE first: start from legion.env.example (docs/HOST.md)" >&2; exit 1; }
[ -n "$LEGION_REPO" ] || { echo "set LEGION_REPO in $LEGION_ENV_FILE" >&2; exit 1; }

if [ "$part" = all ]; then
  mkdir -p "$LEGION_SECRETS" "$LEGION_MEMORY" "$LEGION_WORKSPACE"
  chown -R root:root "$LEGION_SECRETS" "$LEGION_ENV_FILE"
  chmod 700 "$LEGION_SECRETS"
  chmod 600 "$LEGION_ENV_FILE"
  find "$LEGION_SECRETS" -type f -exec chmod 600 {} +
fi

# The shared parts first, then every agent folder that has a setup.sh and runs on this VM (LEGION_AGENTS).
steps=()
[ -z "${LEGION_HOSTNAME:-}" ] || steps+=(host:shared/host)
steps+=(sandbox:shared/sandbox hooks:shared/hooks)
for dir in */setup.sh; do
  agent=${dir%/setup.sh}
  [ "$agent" = shared ] || ! on_host "$agent" || steps+=("$agent:$agent")
done
for step in "${steps[@]}"; do
  if [ "$part" = all ] || [ "$part" = "${step%%:*}" ]; then
    bash "${step#*:}/setup.sh"
  fi
done
