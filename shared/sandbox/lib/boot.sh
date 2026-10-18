# What every Legion entrypoint does first inside the sandbox. The image holds it at /usr/local/lib/legion/boot.sh;
# an entrypoint sources it and defines fail() before it calls anything here.
# shellcheck shell=bash

# Refuses any sign of production before the agent sees a single line of untrusted input.
boot_check() {
  [ -n "${LEGION_REPO:-}" ] || fail "no LEGION_REPO"
  [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || fail "no Claude token"
  local deny=${LEGION_BOOT_DENY:-}
  # The pattern itself is in the environment, so it is left out of the scan.
  if [ -n "$deny" ] && env | grep -v '^LEGION_BOOT_DENY=' | grep -E "$deny" >/dev/null; then
    fail "a production value is in the environment"
  fi
}

# Clones LEGION_REPO into ./repo with the read-only token, then drops the token from the remote.
# Usage: clone_repo <git clone args...>, as `clone_repo --depth 1 --branch main`.
clone_repo() {
  local url="https://x-access-token:${GITHUB_TOKEN:?no GitHub token}@github.com/${LEGION_REPO}.git"
  git clone -q "$@" "$url" repo || return 1
  # The token is in the remote URL only for the clone; the agent never sees it in git config.
  git -C repo remote set-url origin "https://github.com/${LEGION_REPO}.git"
}

# Fetches one commit into a fresh ./repo, for a replay at an older head.
fetch_commit() { # <sha>
  local url="https://x-access-token:${GITHUB_TOKEN:?no GitHub token}@github.com/${LEGION_REPO}.git"
  git init -q repo && git -C repo remote add origin "$url" \
    && git -C repo fetch -q --depth 1 origin "$1" && git -C repo checkout -q FETCH_HEAD \
    && git -C repo remote set-url origin "https://github.com/${LEGION_REPO}.git"
}

# The skills come read-only from the Legion copy on the host (/legion/skills/<group>/<SKILL>/). They go to the user's
# ~/.claude, outside the repo, so they never show up in a fix's diff.
install_skills() {
  mkdir -p ~/.claude/skills
  local group skill
  for group in /legion/skills/*/; do
    for skill in "$group"*/; do
      [ -f "${skill}SKILL.md" ] && cp -r "$skill" ~/.claude/skills/
    done
  done
  [ -n "$(ls -A ~/.claude/skills 2>/dev/null)" ] || fail "no skill reached the sandbox"
}

# Runs LEGION_INSTALL_CMD in ./repo. Empty: the repository needs no install.
install_deps() {
  [ -z "${LEGION_INSTALL_CMD:-}" ] && return 0
  (cd repo && bash -c "$LEGION_INSTALL_CMD") || fail "the install failed: $LEGION_INSTALL_CMD"
}

# The line a prompt carries about telemetry: only the tools this run has a token for.
telemetry_hint() {
  local tools=()
  [ -n "${AXIOM_TOKEN:-}" ] && tools+=("legion-axiom '<APL>' (datasets: ${LEGION_LOG_DATASETS:-list them with APL})")
  [ -n "${POSTHOG_API_KEY:-}" ] && tools+=("legion-posthog '<HogQL>'")
  if [ ${#tools[@]} -eq 0 ]; then
    echo "This run has no production telemetry: prove from code, tests and CI, and say so."
  else
    local joined
    joined=$(printf '%s and ' "${tools[@]}")
    echo "For production evidence, run ${joined% and } (read-only)."
  fi
}
