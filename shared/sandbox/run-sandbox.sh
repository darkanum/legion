# The docker run that every agent's sandbox/run.sh shares. Source it after shared/paths.sh; it runs nothing itself.
# An agent's run.sh keeps its own inputs, limits, mounts, env lines and what to write when the wall clock ends a run.
# shellcheck shell=bash

# One run at a time per agent. Exit 3 tells the host that the agent is busy.
sandbox_lock() { # <agent>
  exec 9>"/run/$1.lock"
  flock -n 9 || { echo "another $1 run is in progress" >&2; exit 3; }
}

# Sets SANDBOX_ENV_FILE: mode 600, deleted when the container starts or when the script exits.
sandbox_env_file() {
  SANDBOX_ENV_FILE=$(mktemp) && chmod 600 "$SANDBOX_ENV_FILE"
  trap 'rm -f "$SANDBOX_ENV_FILE"' EXIT
}

# The only way out of the sandbox network.
sandbox_proxy_env() {
  echo "HTTPS_PROXY=http://legion-proxy:8888"
  echo "HTTP_PROXY=http://legion-proxy:8888"
  echo "NODE_USE_ENV_PROXY=1"
}

# What every run needs to know about the repository, and the Claude token. Values go in as they are: an env file
# takes no quotes, so LEGION_INSTALL_CMD may hold spaces.
sandbox_legion_env() {
  echo "LEGION_REPO=$LEGION_REPO"
  echo "LEGION_BASE_BRANCH=$LEGION_BASE_BRANCH"
  echo "LEGION_INSTALL_CMD=${LEGION_INSTALL_CMD-pnpm install --frozen-lockfile --reporter=silent}"
  # The boot check: a run refuses to start when any variable name or value matches. Name your production keys here.
  echo "LEGION_BOOT_DENY=${LEGION_BOOT_DENY:-sk_live_|STRIPE_SECRET|AWS_SECRET_ACCESS_KEY|DATABASE_URL}"
  echo "CLAUDE_CODE_OAUTH_TOKEN=$(cat "$LEGION_SECRETS/claude_oauth_token")"
}

# The sandbox's read-only GitHub token, as GITHUB_TOKEN and GH_TOKEN: minted per run with read-only permissions when
# Legion runs as a GitHub App, else the github_sandbox_token file.
sandbox_github_env() {
  local token
  if [ -n "${LEGION_GITHUB_APP_ID:-}" ] && [ -n "${LEGION_GITHUB_APP_INSTALLATION_ID:-}" ]; then
    token=$("$LEGION_VENV/bin/python" "$LEGION_REPO_DIR/shared/lib/github_app.py" sandbox-token) || return 1
  else
    token=$(cat "$LEGION_SECRETS/github_sandbox_token")
  fi
  echo "GITHUB_TOKEN=$token"
  echo "GH_TOKEN=$token"
}

# The read-only telemetry tokens, only when the host has them: a run with none of them reads code and CI alone.
sandbox_telemetry_env() {
  if [ -s "$LEGION_SECRETS/axiom_token" ]; then
    echo "AXIOM_TOKEN=$(cat "$LEGION_SECRETS/axiom_token")"
    echo "LEGION_LOG_DATASETS=${LEGION_LOG_DATASETS:-}"
  fi
  if [ -s "$LEGION_SECRETS/posthog_key" ] && [ -n "${LEGION_POSTHOG_PROJECT_ID:-}" ]; then
    echo "POSTHOG_API_KEY=$(cat "$LEGION_SECRETS/posthog_key")"
    echo "POSTHOG_PROJECT_ID=$LEGION_POSTHOG_PROJECT_ID"
    echo "POSTHOG_HOST=${LEGION_POSTHOG_HOST:-us.posthog.com}"
  fi
}

# The skills come from this repository on the host, read-only, never from the repository under work: a PR must not
# change the rules it is reviewed or fixed by. The entrypoint copies them into ~/.claude/skills.
sandbox_skill_mounts() { # <agent>
  printf '%s\n' -v "$LEGION_REPO_DIR/shared/skills:/legion/skills/shared:ro"
  [ ! -d "$LEGION_REPO_DIR/$1/skills" ] || printf '%s\n' -v "$LEGION_REPO_DIR/$1/skills:/legion/skills/$1:ro"
}

# Starts the container, waits at most <wall-seconds>, kills it and calls <on-timeout> when time runs out, then keeps
# the log in <run-dir>/container.log and removes the container. The rest of the arguments go to `docker run`.
sandbox_run() { # <container> <run-dir> <wall-seconds> <on-timeout> <docker run args...>
  local name=$1 run_dir=$2 wall=$3 on_timeout=$4
  shift 4
  docker run -d --name "$name" --network legion-sandbox --cap-drop ALL --security-opt no-new-privileges \
    --env-file "$SANDBOX_ENV_FILE" "$@" >/dev/null
  rm -f "$SANDBOX_ENV_FILE"
  if ! timeout "$wall" docker wait "$name" >/dev/null; then
    docker kill "$name" >/dev/null 2>&1 || true
    "$on_timeout"
  fi
  docker logs "$name" > "$run_dir/container.log" 2>&1 || true
  docker rm -f "$name" >/dev/null 2>&1 || true
}
