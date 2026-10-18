#!/usr/bin/env bash
# Runs inside the sandbox: boot check, clone, then one skill run. The result goes to /out.
# MODE=patrol runs /valac-patrol. MODE=plan or from-plan runs /triage-bug on the issue that the host opened.
set -euo pipefail
# shellcheck source=../../shared/sandbox/lib/boot.sh
source /usr/local/lib/legion/boot.sh

fail() {
  mkdir -p /out
  jq -n --arg reason "$1" '{outcome: "blocked", reason: $reason}' > /out/result.json
  echo "blocked: $1" >&2
  exit 1
}

boot_check
[[ "${ISSUE:-}" =~ ^[0-9]+$ ]] || fail "ISSUE is not a number"
[ -n "${GITHUB_TOKEN:-}" ] || fail "no GitHub token"
[ -n "${AXIOM_TOKEN:-}" ] || fail "no Axiom token"
[ -n "${ANTHROPIC_MODEL:-}" ] || fail "no model"
[[ "${MAX_TURNS:-}" =~ ^[0-9]+$ ]] || fail "MAX_TURNS is not a number"
case "${MODE:-}" in
  patrol | plan) ;;
  from-plan) [ -f /in/plan.json ] || fail "MODE is from-plan but /in/plan.json is missing" ;;
  *) fail "MODE is not patrol, plan or from-plan" ;;
esac

# The patrol runs `git log -S` to tell new logging from a new defect, so it needs history, not one commit.
days=${VALAC_HISTORY_DAYS:-45}
clone_repo --shallow-since="$(date -u -d "$days days ago" +%Y-%m-%d)" --branch "$LEGION_BASE_BRANCH" \
  || fail "could not clone $LEGION_BASE_BRANCH"
install_skills
cd repo
echo "tmp/valac/" >> .git/info/exclude
echo "tmp/marbas/" >> .git/info/exclude

if [ "$MODE" = patrol ]; then
  PROMPT="/valac-patrol. Use the current directory ($(pwd)) as the repo: ${LEGION_BASE_BRANCH} today, with ${days} days of history."
  PROMPT="${PROMPT} Read the last ${VALAC_WINDOW_HOURS:-24} hours. The log datasets are: ${LEGION_LOG_DATASETS:-every dataset the token can read}."
  # The skill says how to query and where to write; this line names only what the skill cannot know.
  PROMPT="${PROMPT} You run in the sandbox. There is no python3 here; use jq."
else
  install_deps
  BASE=$(git rev-parse HEAD)
  PROMPT="/triage-bug ${ISSUE}"
  [ "$MODE" = plan ] && PROMPT="${PROMPT} --plan" || PROMPT="${PROMPT} --from-plan /in/plan.json"
  PROMPT="${PROMPT}. Use the current directory ($(pwd)) as the worktree: a fresh clone of ${LEGION_BASE_BRANCH}, already installed."
  # Valac's issue has no reporter and no affected user: its evidence is the APL in the body.
  PROMPT="${PROMPT} Valac opened this issue from the production logs. It has no affected user and no Slack thread:"
  PROMPT="${PROMPT} the evidence starts from the APL in the issue body. $(telemetry_hint)"
fi

# Headless mode: a tool outside /etc/claude-code/managed-settings.json is denied, never prompted.
claude -p "$PROMPT" --model "$ANTHROPIC_MODEL" --max-turns "$MAX_TURNS" --output-format json > /out/claude.json || true

if [ "$MODE" = patrol ]; then
  [ -f tmp/valac/patrol.json ] || fail "the patrol wrote no tmp/valac/patrol.json"
  jq -e '.findings | type == "array"' tmp/valac/patrol.json >/dev/null || fail "the patrol output has no findings array"
  cp tmp/valac/patrol.json /out/result.json
  exit 0
fi

result=$(find . -path "*/tmp/marbas/${ISSUE}/result.json" -print -quit)
[ -n "$result" ] || fail "the run wrote no result.json"
cp "$result" /out/result.json
# The clone dies with the container, so the fix leaves as a patch. The host checks it and pushes it.
git add -A || fail "could not stage the fix"
if ! git diff --cached --quiet "$BASE"; then
  git diff --cached --binary "$BASE" > /out/fix.patch
  echo "$BASE" > /out/base.sha
fi
