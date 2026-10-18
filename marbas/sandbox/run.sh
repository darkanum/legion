#!/usr/bin/env bash
# Runs /triage-bug for one issue in a fresh sandbox and prints result.json.
# Usage: run.sh <issue> [--at <sha>] [--plan [--feedback <file>] | --from-plan <plan.json> | --prove <pr> --since <time>
#   | --fix-ci <ci.json> | --address-review <review.json>] (the last two with --plan-file and --branch)
# The sandbox gets only the read-only GitHub token and the Claude token, through an env file deleted at start.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../../shared/paths.sh
source "$HERE/../../shared/paths.sh"
# shellcheck source=../../shared/sandbox/run-sandbox.sh
source "$HERE/../../shared/sandbox/run-sandbox.sh"
RUNS=$LEGION_WORKSPACE/marbas/runs
MEMORY=$LEGION_MEMORY/marbas # engineer feedback notes, written by the Slack listener
WALL_SECONDS=${MARBAS_WALL_SECONDS:-2700} # 45 minutes
MAX_TURNS=${MARBAS_MAX_TURNS:-200}

usage="usage: run.sh <issue> [--at <sha>] [--plan [--feedback <file>] | --from-plan <plan.json> | --prove <pr> --since <time> | --fix-ci <ci.json> --plan-file <plan.json> --branch <fix/issue-N-*> | --address-review <review.json> --plan-file <plan.json> --branch <fix/issue-N-*>]"
ISSUE=${1:?$usage}
shift
AT_REF="" MODE="" FEEDBACK="" PLAN="" PROVE_PR="" SINCE="" CI="" REVIEW="" BRANCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --at) AT_REF=${2:?--at needs a sha}; shift 2 ;;
    --plan) MODE=plan; shift ;;
    --feedback) FEEDBACK=${2:?--feedback needs a file}; shift 2 ;;
    --from-plan) MODE=from-plan; PLAN=${2:?--from-plan needs a file}; shift 2 ;;
    --prove) MODE=prove; PROVE_PR=${2:?--prove needs a PR number}; shift 2 ;;
    --since) SINCE=${2:?--since needs a time}; shift 2 ;;
    --fix-ci) MODE=fix-ci; CI=${2:?--fix-ci needs a file}; shift 2 ;;
    --address-review) MODE=address-review; REVIEW=${2:?--address-review needs a file}; shift 2 ;;
    --plan-file) PLAN=${2:?--plan-file needs a file}; shift 2 ;;
    --branch) BRANCH=${2:?--branch needs a name}; shift 2 ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done
[[ "$ISSUE" =~ ^[0-9]+$ ]] || { echo "issue must be a number" >&2; exit 2; }
[ -z "$FEEDBACK" ] || [ "$MODE" = plan ] || { echo "--feedback needs --plan" >&2; exit 2; }
[ "$MODE" != prove ] || { [[ "$PROVE_PR" =~ ^[0-9]+$ ]] && [[ "$SINCE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; } \
  || { echo "--prove needs a PR number and --since <YYYY-MM-DDTHH:MM:SSZ>" >&2; exit 2; }
[[ "$MODE" != fix-ci && "$MODE" != address-review ]] || [[ "$BRANCH" =~ ^fix/issue-[0-9]+-[a-z0-9-]+$ && -n "$PLAN" ]] \
  || { echo "--$MODE needs --plan-file and a --branch fix/issue-N-*" >&2; exit 2; }
for f in "$FEEDBACK" "$PLAN" "$CI" "$REVIEW"; do [ -z "$f" ] || [ -f "$f" ] || { echo "no such file: $f" >&2; exit 2; }; done
[ -z "$PLAN" ] || jq -e '.plan.files' "$PLAN" >/dev/null 2>&1 || { echo "$PLAN has no plan.files" >&2; exit 2; }

docker image inspect legion-sandbox:local >/dev/null 2>&1 || {
  jq -n --argjson issue "$ISSUE" '{issue: $issue, outcome: "blocked", reason: "the sandbox image is missing: run shared/sandbox/setup.sh on the VM"}'
  exit 0
}

sandbox_lock marbas

run_dir="$RUNS/${ISSUE}-$(date +%Y%m%d%H%M%S)"
mkdir -p "$run_dir/in" && chown 1001:1001 "$run_dir"
# The inputs go in read-only, so the agent cannot rewrite the plan that an engineer approved.
[ -z "$FEEDBACK" ] || cp "$FEEDBACK" "$run_dir/in/feedback.md"
[ -z "$PLAN" ] || cp "$PLAN" "$run_dir/in/plan.json"
[ -z "$CI" ] || cp "$CI" "$run_dir/in/ci.json"
[ -z "$REVIEW" ] || cp "$REVIEW" "$run_dir/in/review.json"
name="marbas-run-${ISSUE}"

sandbox_env_file
{
  echo "ISSUE=$ISSUE"
  echo "AT_REF=$AT_REF"
  echo "MODE=$MODE"
  echo "PROVE_PR=$PROVE_PR"
  echo "SINCE=$SINCE"
  echo "BRANCH=$BRANCH"
  sandbox_github_env
  echo "ANTHROPIC_MODEL=$LEGION_MODEL"
  echo "MAX_TURNS=$MAX_TURNS"
  sandbox_legion_env
  sandbox_telemetry_env
  sandbox_proxy_env
} > "$SANDBOX_ENV_FILE"

# Only a plan reads the lessons, and read-only: the agent can never write its own memory.
mounts=()
if [ "$MODE" = plan ]; then mkdir -p "$MEMORY" && mounts+=(-v "$MEMORY:/memory:ro"); fi
mapfile -t -O "${#mounts[@]}" mounts < <(sandbox_skill_mounts marbas)

on_timeout() {
  jq -n --argjson issue "$ISSUE" --arg limit "$((WALL_SECONDS / 60))" \
    '{issue: $issue, outcome: "blocked", reason: ("the run hit the " + $limit + "-minute limit")}' > "$run_dir/result.json"
}
sandbox_run "$name" "$run_dir" "$WALL_SECONDS" on_timeout \
  --memory "${MARBAS_MEMORY:-6g}" --cpus "${MARBAS_CPUS:-3}" --pids-limit 4096 \
  -v "$run_dir:/out" -v "$run_dir/in:/in:ro" -v legion-pnpm-store:/home/agent/.local/share/pnpm/store \
  -v "$HERE/entrypoint.sh:/usr/local/bin/marbas-run:ro" \
  "${mounts[@]}" --entrypoint marbas-run legion-sandbox:local

cat "$run_dir/result.json" 2>/dev/null || echo '{"outcome": "blocked", "reason": "no result.json"}'
