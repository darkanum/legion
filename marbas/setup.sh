#!/usr/bin/env bash
# Installs or updates the Marbas host service on the VM. Run it again after a change to marbas/ or to legion.env.
# The shared sandbox, the venv and the webhook service come first: run ./setup.sh, or shared/sandbox/setup.sh once.
set -euo pipefail
cd "$(dirname "$0")"
source ../shared/paths.sh

require_shared_sandbox
: "${MARBAS_CHANNELS:?set MARBAS_CHANNELS in $LEGION_ENV_FILE: the Slack channel ids Marbas watches}"
chmod 0755 sandbox/run.sh sandbox/entrypoint.sh
mkdir -p "$LEGION_WORKSPACE/marbas/runs" "$LEGION_WORKSPACE/marbas/inbox" "$LEGION_MEMORY/marbas"

install -m 644 host/marbas-slack.service /etc/systemd/system/marbas-slack.service
systemctl daemon-reload
systemctl enable -q marbas-slack
systemctl restart marbas-slack
echo "marbas-slack $(systemctl is-active marbas-slack)"
