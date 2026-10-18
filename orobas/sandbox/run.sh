#!/usr/bin/env bash
# Runs the review ladder for one PR in a fresh sandbox and prints the run folder. host/reviewer.py fills <in-dir> first.
# Usage: run.sh <pr> <head-sha> <in-dir>
# The sandbox gets only the Claude token: the host fetches the clone and writes the inputs, so the run never calls GitHub.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../../shared/paths.sh"
source "$HERE/../../shared/sandbox/run-sandbox.sh"
RUNS=$LEGION_WORKSPACE/orobas/runs
CACHE=$LEGION_WORKSPACE/orobas/repo.git

PR=${1:?usage: run.sh <pr> <head-sha> <in-dir>}
SHA=${2:?usage: run.sh <pr> <head-sha> <in-dir>}
IN=${3:?usage: run.sh <pr> <head-sha> <in-dir>}
[[ "$PR" =~ ^[0-9]+$ && "$SHA" =~ ^[0-9a-f]{40}$ && -f "$IN/stages.json" ]] || { echo "bad arguments" >&2; exit 2; }
# The host owns the wall clock: it lives in stages.py and reaches the run through stages.json.
WALL_SECONDS=$(jq -er '.wallSeconds' "$IN/stages.json")

# Its own lock, not Marbas's: a review must not wait behind a bug run.
sandbox_lock orobas

run_dir="$RUNS/${PR}-$(date +%Y%m%d%H%M%S)"
mkdir -p "$run_dir/in" && cp -r "$IN"/. "$run_dir/in/" && chown -R 1001:1001 "$run_dir"
name="orobas-run-${PR}"

sandbox_env_file
{
  echo "PR=$PR"
  echo "HEAD_SHA=$SHA"
  echo "LEGION_RULES_DIR=${LEGION_RULES_DIR:-.legion/rules}"
  sandbox_legion_env
  sandbox_proxy_env
} > "$SANDBOX_ENV_FILE"

# The starter rulebook and the review skills, read-only from this repository: a PR cannot change either.
mapfile -t mounts < <(sandbox_skill_mounts orobas)
mounts+=(-v "$LEGION_REPO_DIR/orobas/rules:/legion/rules:ro")

# No install: a review reads code, and CI runs the checks. Up to five claude processes run at once, and each
# one mostly waits for the API, so 2 CPUs and 3 GB fit beside a Marbas run.
on_timeout() {
  local reason="the review hit the $((WALL_SECONDS / 60))-minute limit"
  if [ -f "$run_dir/status.json" ]; then
    jq --arg r "$reason" '.error = $r' "$run_dir/status.json" > "$run_dir/status.tmp" && mv "$run_dir/status.tmp" "$run_dir/status.json"
  else
    jq -n --arg r "$reason" '{stages: [], error: $r}' > "$run_dir/status.json"
  fi
}
sandbox_run "$name" "$run_dir" "$WALL_SECONDS" on_timeout \
  --memory "${OROBAS_MEMORY:-3g}" --cpus "${OROBAS_CPUS:-2}" --pids-limit 2048 \
  -v "$run_dir:/out" -v "$run_dir/in:/in:ro" -v "$CACHE:/cache/repo.git:ro" -v "$HERE/review.sh:/opt/review.sh:ro" \
  "${mounts[@]}" --entrypoint bash legion-sandbox:local /opt/review.sh

echo "$run_dir"
