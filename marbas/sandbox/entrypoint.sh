#!/usr/bin/env bash
# Runs inside the sandbox: boot check, clone, install, then one /triage-bug run. The result goes to /out.
# Inputs (env): ISSUE, AT_REF (optional replay commit), MODE (empty, plan, from-plan, prove, fix-ci or address-review;
# files in /in), GITHUB_TOKEN (read-only), ANTHROPIC_MODEL, MAX_TURNS and the LEGION_* lines of run-sandbox.sh.
set -euo pipefail
# shellcheck source=../../shared/sandbox/lib/boot.sh
source /usr/local/lib/legion/boot.sh

fail() {
  mkdir -p /out
  jq -n --arg reason "$1" --argjson issue "${ISSUE:-0}" \
    '{issue: $issue, outcome: "blocked", reason: $reason}' > /out/result.json
  echo "blocked: $1" >&2
  exit 1
}

boot_check
[[ "${ISSUE:-}" =~ ^[0-9]+$ ]] || fail "ISSUE is not a number"
[ -n "${GITHUB_TOKEN:-}" ] || fail "no GitHub token"
# run.sh owns the model and the turn limit; the container has no defaults of its own.
[ -n "${ANTHROPIC_MODEL:-}" ] || fail "no model"
[[ "${MAX_TURNS:-}" =~ ^[0-9]+$ ]] || fail "MAX_TURNS is not a number"
BRANCH_RE='^fix/issue-[0-9]+-[a-z0-9-]+$'
case "${MODE:-}" in
  "") ;;
  plan) ;;
  from-plan) [ -f /in/plan.json ] || fail "MODE is from-plan but /in/plan.json is missing" ;;
  prove) [[ "${PROVE_PR:-}" =~ ^[0-9]+$ && "${SINCE:-}" =~ ^[0-9T:Z-]{20}$ ]] || fail "MODE is prove without a PR or a time" ;;
  fix-ci) [[ "${BRANCH:-}" =~ $BRANCH_RE && -f /in/ci.json && -f /in/plan.json ]] || fail "MODE is fix-ci without a branch, a CI file or a plan" ;;
  address-review) [[ "${BRANCH:-}" =~ $BRANCH_RE && -f /in/review.json && -f /in/plan.json ]] || fail "MODE is address-review without a branch, a review file or a plan" ;;
  *) fail "MODE is not plan, from-plan, prove, fix-ci or address-review" ;;
esac

if [ -n "${AT_REF:-}" ]; then
  [[ "$AT_REF" =~ ^[0-9a-f]{7,40}$ ]] || fail "AT_REF is not a commit sha"
  fetch_commit "$AT_REF" || fail "could not fetch ${AT_REF}"
else
  # A CI or review fix works on the PR branch, so its patch applies on top of the PR head.
  clone_repo --depth 1 --branch "${BRANCH:-$LEGION_BASE_BRANCH}" || fail "could not clone ${BRANCH:-$LEGION_BASE_BRANCH}"
fi
install_skills
cd repo
# The run notes live under tmp/marbas/; the repository may not ignore tmp/, so this clone does.
echo "tmp/marbas/" >> .git/info/exclude
install_deps
BASE=$(git rev-parse HEAD)

PROMPT="/triage-bug ${ISSUE}"
[ -n "${AT_REF:-}" ] && PROMPT="${PROMPT} --at ${AT_REF}"
if [ "${MODE:-}" = plan ]; then
  PROMPT="${PROMPT} --plan"
  [ -f /in/feedback.md ] && PROMPT="${PROMPT} --feedback /in/feedback.md"
elif [ "${MODE:-}" = from-plan ]; then
  PROMPT="${PROMPT} --from-plan /in/plan.json"
elif [ "${MODE:-}" = fix-ci ]; then
  PROMPT="${PROMPT} --fix-ci /in/ci.json --from-plan /in/plan.json"
elif [ "${MODE:-}" = address-review ]; then
  PROMPT="${PROMPT} --address-review /in/review.json --from-plan /in/plan.json"
elif [ "${MODE:-}" = prove ]; then
  PROMPT="${PROMPT} --prove ${PROVE_PR} --since ${SINCE}"
fi
PROMPT="${PROMPT}. Use the current directory ($(pwd)) as the worktree: a fresh clone at the right commit, already installed."
PROMPT="${PROMPT} The base branch is ${LEGION_BASE_BRANCH}. $(telemetry_hint)"
# A run that is not told the memory folder exists does not look.
if [ -d /memory ]; then
  PROMPT="${PROMPT} Engineer lessons from past plans are in /memory (read-only, $(find /memory -name '*.md' | wc -l) notes): read them before you plan, as the Plan step says."
fi
# Headless mode: a tool outside /etc/claude-code/managed-settings.json is denied, never prompted.
# GITHUB_TOKEN is read-only; push, PR and merge belong to the host.
claude -p "$PROMPT" \
  --model "$ANTHROPIC_MODEL" \
  --max-turns "$MAX_TURNS" \
  --output-format json > /out/claude.json || true

result=$(find . -path "*/tmp/marbas/${ISSUE}/result.json" -print -quit)
[ -n "$result" ] || fail "the run wrote no result.json"
cp "$result" /out/result.json
# The clone dies with the container, so the fix leaves as a patch. The host checks it and pushes it.
# tmp/marbas/ is excluded above, so the run notes stay out of the patch.
git add -A || fail "could not stage the fix"
if ! git diff --cached --quiet "$BASE"; then
  git diff --cached --binary "$BASE" > /out/fix.patch
  echo "$BASE" > /out/base.sha
fi
