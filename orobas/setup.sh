#!/usr/bin/env bash
# Installs or updates Orobas on the Legion host. Run it again after a change to orobas/ or to legion.env.
# The shared sandbox, the venv and the webhook service come first: run ./setup.sh, or shared/sandbox/setup.sh once.
set -euo pipefail
cd "$(dirname "$0")"
source ../shared/paths.sh

require_shared_sandbox
chmod 0755 sandbox/run.sh
mkdir -p "$LEGION_WORKSPACE/orobas/runs" "$LEGION_WORKSPACE/orobas/prs" "$LEGION_WORKSPACE/orobas/inbox"

# The bare clone each review fetches into; the first one takes a few minutes, so it happens here.
"$LEGION_VENV/bin/python" host/context.py

install -m 644 host/orobas.service /etc/systemd/system/orobas.service
systemctl daemon-reload
systemctl enable -q orobas
systemctl restart orobas
echo "orobas $(systemctl is-active orobas)"
