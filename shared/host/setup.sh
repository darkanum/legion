#!/usr/bin/env bash
# Prepares a fresh Debian or Ubuntu VM: Docker, Caddy and python3-venv, the Caddyfile, and the secrets from Azure Key
# Vault when legion.env names one. The root setup.sh runs it first, only when legion.env sets LEGION_HOSTNAME; a host
# with its own reverse proxy skips it. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"
source ../paths.sh
: "${LEGION_HOSTNAME:?set LEGION_HOSTNAME in $LEGION_ENV_FILE}"

# Caddy comes from its signed upstream repo: the distribution package is older and gets fewer security fixes.
if [ ! -f /etc/apt/sources.list.d/caddy-stable.list ]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
fi
missing=()
command -v docker >/dev/null || missing+=(docker.io)
command -v caddy >/dev/null || missing+=(caddy)
command -v jq >/dev/null || missing+=(jq)
python3 -c "import ensurepip" 2>/dev/null || missing+=(python3-venv)
if [ ${#missing[@]} -gt 0 ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi
systemctl enable -q --now docker

sed "s/^LEGION_HOSTNAME {/$LEGION_HOSTNAME {/" Caddyfile > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable -q caddy
systemctl reload-or-restart caddy

mkdir -p "$LEGION_SECRETS"
chmod 700 "$LEGION_SECRETS"
if [ -n "${LEGION_KEY_VAULT:-}" ]; then
  # The VM identity reads only this host's vault; each agent's secrets.txt picks the files.
  IFS=, read -r -a agents <<< "${LEGION_AGENTS:?set LEGION_AGENTS in $LEGION_ENV_FILE when it names a vault}"
  python3 ../lib/keyvault.py "$LEGION_KEY_VAULT" "${agents[@]}"
fi

echo "host ready: $LEGION_HOSTNAME, caddy $(systemctl is-active caddy), docker $(systemctl is-active docker)"
