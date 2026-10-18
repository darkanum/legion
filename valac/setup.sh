#!/usr/bin/env bash
# Installs or updates Valac on the VM. Run it again after a change to valac/ or to legion.env.
# The shared sandbox, the venv and the webhook service come first: run ./setup.sh, or shared/sandbox/setup.sh once.
set -euo pipefail
cd "$(dirname "$0")"
source ../shared/paths.sh

require_shared_sandbox
[ -s "$LEGION_SECRETS/axiom_token" ] || { echo "Valac reads the logs: store the query token in $LEGION_SECRETS/axiom_token" >&2; exit 1; }
schedule=${VALAC_SCHEDULE:-Mon..Fri *-*-* 07:00:00 UTC}
systemd-analyze calendar "$schedule" >/dev/null || { echo "VALAC_SCHEDULE is not a systemd calendar: $schedule" >&2; exit 1; }
chmod 0755 sandbox/run.sh sandbox/entrypoint.sh
mkdir -p "$LEGION_WORKSPACE/valac/runs" "$LEGION_WORKSPACE/valac/findings" "$LEGION_WORKSPACE/valac/inbox"

install -m 644 host/valac-patrol.service /etc/systemd/system/
sed "s|^OnCalendar=VALAC_SCHEDULE$|OnCalendar=$schedule|" host/valac-patrol.timer > /etc/systemd/system/valac-patrol.timer
systemctl daemon-reload
systemctl enable -q --now valac-patrol.timer
echo "valac-patrol timer $(systemctl is-active valac-patrol.timer), next run $(systemctl show -p NextElapseUSecRealtime --value valac-patrol.timer)"
