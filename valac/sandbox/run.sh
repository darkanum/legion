#!/usr/bin/env bash
# Runs one Valac step in a fresh sandbox and prints its result JSON.
# Usage: run.sh --patrol | run.sh <issue> --plan | run.sh <issue> --from-plan <plan.json>
# The sandbox gets only the read-only GitHub token, the query-only Axiom token and the Claude token, through an env
# file deleted at start, plus the PostHog key when the host has one. The host opens issues and PRs; the sandbox never writes to GitHub.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../../shared/paths.sh
source "$HERE/../../shared/paths.sh"
# shellcheck source=../../shared/sandbox/run-sandbox.sh
source "$HERE/../../shared/sandbox/run-sandbox.sh"
RUNS=$LEGION_WORKSPACE/valac/runs

usage="usage: run.sh --patrol | run.sh <issue> --plan | run.sh <issue> --from-plan <plan.json>"
ISSUE=0 MODE="" PLAN=""
if [ "${1:-}" = --patrol ]; then
  MODE=patrol
else
  ISSUE=${1:?$usage}
  [[ "$ISSUE" =~ ^[0-9]+$ ]] || { echo "issue must be a number" >&2; exit 2; }
  case "${2:-}" in
    --plan) MODE=plan ;;
    --from-plan) MODE=from-plan; PLAN=${3:?--from-plan needs a file} ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
fi
[ -z "$PLAN" ] || jq -e '.plan.files' "$PLAN" >/dev/null 2>&1 || { echo "$PLAN has no plan.files" >&2; exit 2; }

# The patrol reads and greps; a fix installs and runs the tests, so it gets more memory.
# Docker refuses --cpus above the host count, so a small VM lowers VALAC_CPUS.
WALL_SECONDS=${VALAC_WALL_SECONDS:-2700} MAX_TURNS=${VALAC_MAX_TURNS:-200}
if [ "$MODE" = patrol ]; then MEMORY=${VALAC_PATROL_MEMORY:-2g}; else MEMORY=${VALAC_FIX_MEMORY:-6g}; fi

docker image inspect legion-sandbox:local >/dev/null 2>&1 || {
  jq -n '{outcome: "blocked", reason: "the sandbox image is missing: run shared/sandbox/setup.sh on the VM"}'
  exit 0
}

sandbox_lock valac

run_dir="$RUNS/${MODE}-${ISSUE}-$(date +%Y%m%d%H%M%S)"
[ "$MODE" = patrol ] || run_dir="$RUNS/${ISSUE}-$(date +%Y%m%d%H%M%S)"
mkdir -p "$run_dir/in" && chown 1001:1001 "$run_dir"
[ -z "$PLAN" ] || cp "$PLAN" "$run_dir/in/plan.json"
name="valac-run-${MODE}-${ISSUE}"

sandbox_env_file
{
  echo "ISSUE=$ISSUE"
  echo "MODE=$MODE"
  sandbox_github_env
  echo "ANTHROPIC_MODEL=$LEGION_MODEL"
  echo "MAX_TURNS=$MAX_TURNS"
  echo "VALAC_HISTORY_DAYS=${VALAC_HISTORY_DAYS:-45}"
  echo "VALAC_WINDOW_HOURS=${VALAC_WINDOW_HOURS:-24}"
  sandbox_legion_env
  sandbox_telemetry_env
  sandbox_proxy_env
} > "$SANDBOX_ENV_FILE"

mapfile -t mounts < <(sandbox_skill_mounts valac)
on_timeout() {
  jq -n --arg mode "$MODE" '{outcome: "blocked", reason: ("the " + $mode + " run hit its time limit")}' > "$run_dir/result.json"
}
sandbox_run "$name" "$run_dir" "$WALL_SECONDS" on_timeout \
  --memory "$MEMORY" --cpus "${VALAC_CPUS:-2}" --pids-limit 4096 \
  -v "$run_dir:/out" -v "$run_dir/in:/in:ro" -v legion-pnpm-store:/home/agent/.local/share/pnpm/store \
  -v "$HERE/entrypoint.sh:/usr/local/bin/valac-run:ro" \
  "${mounts[@]}" --entrypoint valac-run legion-sandbox:local

cat "$run_dir/result.json" 2>/dev/null || echo '{"outcome": "blocked", "reason": "no result.json"}'
