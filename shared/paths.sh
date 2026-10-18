# Every path on the Legion host, defined once for the shell scripts. Source it; shared/lib/paths.py holds the same values.
# shellcheck shell=bash
# shellcheck disable=SC2034 # the scripts that source this file use these names
LEGION_ROOT=${LEGION_ROOT:-/opt/legion}
LEGION_REPO_DIR=$LEGION_ROOT/repo
LEGION_SECRETS=$LEGION_ROOT/secrets
LEGION_MEMORY=$LEGION_ROOT/memory
LEGION_WORKSPACE=$LEGION_ROOT/workspace
LEGION_VENV=$LEGION_ROOT/venv
# The settings of this host: the repository, the switches and the limits. legion.env.example lists every key.
LEGION_ENV_FILE=$LEGION_ROOT/legion.env
# shellcheck source=/dev/null
[ -f "$LEGION_ENV_FILE" ] && { set -a; source "$LEGION_ENV_FILE"; set +a; }
LEGION_REPO=${LEGION_REPO:-}
LEGION_BASE_BRANCH=${LEGION_BASE_BRANCH:-main}
LEGION_MODEL=${LEGION_MODEL:-claude-opus-5-5}
LEGION_FAST_MODEL=${LEGION_FAST_MODEL:-claude-sonnet-5-5}

# True when this VM runs the agent: always when LEGION_AGENTS is empty.
on_host() {
  [ -z "${LEGION_AGENTS:-}" ] || [[ ",$LEGION_AGENTS," == *",$1,"* ]]
}

# Each agent's setup.sh calls it first: the venv and the sandbox image belong to shared/sandbox/setup.sh.
require_shared_sandbox() {
  [ -x "$LEGION_VENV/bin/python" ] && docker image inspect legion-sandbox:local >/dev/null 2>&1 \
    || { echo "run shared/sandbox/setup.sh first" >&2; exit 1; }
}
