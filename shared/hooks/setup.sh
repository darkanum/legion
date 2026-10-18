#!/usr/bin/env bash
# Installs or updates the GitHub webhook service on the VM. The root setup.sh runs it after the shared sandbox.
# It needs the secret file github_webhook_secret, which host/webhook.py writes when it creates the GitHub hook.
set -euo pipefail
cd "$(dirname "$0")"
source ../paths.sh

[ -x "$LEGION_VENV/bin/python" ] || { echo "run shared/sandbox/setup.sh first" >&2; exit 1; }
if [ ! -s "$LEGION_SECRETS/github_webhook_secret" ]; then
  # A new or migrated host: the service cannot check a signature yet, so it waits for the secret.
  echo "legion-hooks skipped: store github_webhook_secret, then run ./setup.sh hooks (docs/HOST.md)" >&2
  exit 0
fi
mkdir -p "$LEGION_WORKSPACE/hooks"
# One inbox per agent that subscribes to GitHub events.
for spec in ../../*/subscriptions.json; do
  [ -f "$spec" ] && mkdir -p "$LEGION_WORKSPACE/$(basename "$(dirname "$spec")")/inbox"
done

install -m 644 host/legion-hooks.service /etc/systemd/system/legion-hooks.service
systemctl daemon-reload
systemctl enable -q legion-hooks
systemctl restart legion-hooks
echo "legion-hooks $(systemctl is-active legion-hooks)"
