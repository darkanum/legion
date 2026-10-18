# Operating a Legion host

A Legion host is one Linux VM that runs the hooks service and one or more agents. This page covers its layout, the
secrets each agent needs, setup, updates, logs and the switches that stop the agents.

## The layout

Everything lives under `/opt/legion`. `LEGION_ROOT` moves the whole tree, for a host whose backup covers another
folder. The paths are defined once, in `shared/lib/paths.py` and `shared/paths.sh`.

| Path | What it holds |
| --- | --- |
| `/opt/legion/repo` | The clone of this repository that every service runs from. `setup.sh` refuses to run from anywhere else. |
| `/opt/legion/legion.env` | The settings. Mode 600, owner root. systemd loads it with `EnvironmentFile=`, and the shell scripts source it. |
| `/opt/legion/secrets/` | One file per secret, mode 600, owner root, folder mode 700. |
| `/opt/legion/memory/<agent>/` | Notes an agent reads. Marbas keeps one Markdown note per engineer reject or change request here. A sandbox mounts it read-only, so an agent never writes its own memory. |
| `/opt/legion/workspace/<agent>/` | State and runs: `inbox/` (GitHub events), `runs/` (one folder per sandbox run), and each agent's own state (`threads/`, `findings/`, `prs/`, `repo.git`, `budget.json`). |
| `/opt/legion/workspace/hooks/state.json` | The last webhook delivery the hooks service saw, for redelivery at boot. |
| `/opt/legion/venv` | One Python venv for every host process. Each agent lists its packages in `host/requirements.txt`. |

Docker holds the rest: the images `legion-sandbox:local` and `legion-proxy:local`, the `legion-proxy` container, the
internal network `legion-sandbox`, and the volume `legion-pnpm-store`, a package store shared by every run.

The systemd units are `legion-hooks.service`, `marbas-slack.service`, `valac-patrol.service` with
`valac-patrol.timer`, and `orobas.service`. All run as root, because they start containers and write the workspace.

## Secrets

Each agent's `secrets.txt` names the files it reads. A VM that runs several agents needs the union.

| File | Marbas | Valac | Orobas | What it is |
| --- | :---: | :---: | :---: | --- |
| `github_host_token` | yes | yes | yes | The bot account's write token. Host only. |
| `github_sandbox_token` | yes | yes | | The bot account's read-only token, for the sandbox. |
| `claude_oauth_token` | yes | yes | yes | The Claude Code subscription token. |
| `slack_bot_token` | yes | | | The Slack bot token (`xoxb-`). |
| `slack_app_token` | yes | | | The Slack app-level token for Socket Mode (`xapp-`). |
| `slack_webhook_url` | | yes | | An incoming webhook URL for Valac's channel. No file: no posts. |
| `axiom_ingest_token` | yes | yes | yes | Writes the agents' run log. No file: the log stays in journald. |
| `axiom_token` | yes | yes | | Queries the production datasets, read-only. Required for Valac. |
| `posthog_key` | yes | yes | | A PostHog personal API key with query access, read-only. |
| `github_webhook_secret` | | | | Written by `webhook.py`. The hooks service needs it. |
| `github_webhook_id` | | | | Written by `webhook.py`. Lets the hooks service ask for redeliveries at boot. |

Write a file by hand with no echo to the shell history:

```sh
( umask 077; cat > /opt/legion/secrets/github_host_token )   # paste the value, then Ctrl-D
```

`setup.sh` sets the owner and the modes again on every full run.

### The GitHub tokens

Legion acts as one GitHub bot account (a machine user). The host calls `GET /user` to learn its own login and commits
as that account, so both tokens are fine-grained personal access tokens of that account, limited to the one
repository.

**`github_host_token`**, the write token. Repository permissions:

| Permission | Access | Why |
| --- | --- | --- |
| Contents | Read and write | Push the fix branches; fetch the code for Orobas's clone; merge when `MARBAS_AUTO_MERGE` is on |
| Pull requests | Read and write | Open PRs, request reviewers, post reviews, dismiss the bot's own reviews, resolve threads |
| Issues | Read and write | Open Valac's issues, assign, label, comment; create Orobas's labels |
| Commit statuses | Read | Read the CI state of a commit (GraphQL `statusCheckRollup`) |
| Actions | Read and write | Read failed job logs, and re-run failed jobs that a PR did not cause (Marbas) |
| Deployments | Read | Watch the production deploy of a merge (Marbas) |
| Webhooks | Read and write | Create the repository webhook and ask for redeliveries (`webhook.py`, the hooks service) |
| Metadata | Read | Required by GitHub |

When `LEGION_TEAM` names an org team, the token also needs the organization permission **Members: read**. When
`LEGION_TEAM` is empty, the engineers are the collaborators with push access, read with the same token.

A fine-grained token has no Checks permission, which is why the CI state is read through GraphQL.

If the bot account may bypass the base branch protection, nothing on GitHub checks its pushes. The host checks the
scope of every patch for that reason; see [MARBAS.md](MARBAS.md).

**`github_sandbox_token`**, the read-only token: Contents, Issues, Pull requests and Metadata, all **read**. The
sandbox uses it to clone and to run `gh issue view`. The host also uses it for a few reads that need no write access,
such as the Slack user map.

### Or a GitHub App

Instead of a bot account, Legion can run as a GitHub App, which needs no seat and no long-lived token:

1. Create an App with the repository permissions in the table above (Commit statuses replaces Checks: an App may
   also have **Checks: read**), no webhook of its own, and install it on the repository.
2. Download its private key to `/opt/legion/secrets/github_app_key.pem` (mode 600).
3. Set `LEGION_GITHUB_APP_ID` and `LEGION_GITHUB_APP_INSTALLATION_ID` in `legion.env` (the installation id is the
   number at the end of the App's installation URL).

The hosts then mint an installation token from the key (`shared/lib/github_app.py`, which needs `PyJWT[crypto]` from
`shared/host/requirements.txt`) and mint it again five minutes before it ends. Each sandbox run gets its own token
minted with read-only permissions (Contents, Issues, Pull requests, Metadata), whatever the App may do. Commits,
PRs and reviews come from `<app-slug>[bot]`. `github_host_token` and `github_sandbox_token` are not needed.

### The Claude token

Run `claude setup-token` on any machine where you are logged in to Claude Code, and store the token it prints in
`claude_oauth_token`. Every agent on the host shares this one seat. Orobas keeps to its share of the seat window
(`OROBAS_SEAT_WINDOW_USD`, `OROBAS_SEAT_SHARE`) so the fixers keep room; see [OROBAS.md](OROBAS.md).

### Slack

**Marbas** uses a Slack app in Socket Mode, so the host needs no public Slack endpoint.

- `slack_app_token`: an app-level token with the `connections:write` scope.
- `slack_bot_token`: the bot token, with at least `chat:write` (post, update and ephemeral messages),
  `reactions:write` (the eyes and check reactions), `channels:history` (read the thread parent and the replies;
  add `groups:history` for a private channel) and `channels:read`.
- Event subscriptions: `message.channels` (and `message.groups` for a private channel). Interactivity on, for the
  Approve and Reject buttons.
- Invite the bot to every channel in `MARBAS_CHANNELS` and `MARBAS_SHADOW_CHANNEL`.

An engineer's Slack id is linked to their GitHub login by the file `LEGION_SLACK_USER_MAP` on the base branch
(`{"octocat": "U0123"}`). The default `.github/slack-user-map.json` is under a guarded path, so no agent can add a
name to it.

**Valac** posts through an incoming webhook: store its URL in `slack_webhook_url`. No file turns the posts off.

### Axiom and PostHog

- `axiom_ingest_token`: an Axiom API token that can ingest into `LEGION_LOG_DATASET` (default `legion`).
- `axiom_token`: an Axiom token that can query the production datasets in `LEGION_LOG_DATASETS`. The sandbox uses it
  through `legion-axiom`, which runs one APL query and prints at most 60 KB.
- `posthog_key`: a PostHog personal API key with query access to project `LEGION_POSTHOG_PROJECT_ID`. With no
  project id, no run gets the key. The sandbox uses it through `legion-posthog`.

A run with no telemetry token is told so, and proves from code, tests and CI alone.

## Setup

1. **A VM.** Debian or Ubuntu, with a public DNS name for the webhook. Two CPUs and 8 GB of memory fit one agent;
   Marbas's runs take up to `MARBAS_MEMORY` (6 GB) and `MARBAS_CPUS` (3), so size the VM for the agents it runs.
2. **The clone.** `git clone <this repository> /opt/legion/repo`.
3. **The settings.** `cp /opt/legion/repo/legion.env.example /opt/legion/legion.env` and edit it. Set at least
   `LEGION_REPO`. Set `LEGION_HOSTNAME` to let `shared/host/setup.sh` install Docker, Caddy and `python3-venv` and
   write the Caddyfile. Set `LEGION_AGENTS` when the VM runs only some agents. Set `MARBAS_CHANNELS` for Marbas and
   `LEGION_LOG_DATASETS` for Valac.
4. **The secrets.** Write the files above, or set `LEGION_KEY_VAULT` (see below).
5. **Run `./setup.sh`** as root from `/opt/legion/repo`. In order, it runs:
   - `shared/host/setup.sh`, only when `LEGION_HOSTNAME` is set: Docker, Caddy, the Caddyfile, and the secrets from
     Key Vault when `LEGION_KEY_VAULT` is set.
   - `shared/sandbox/setup.sh`: writes the managed settings and the proxy allowlist from `legion.env`, builds both
     images, creates the internal network, starts the proxy, creates the package volume and the venv.
   - `shared/hooks/setup.sh`: installs `legion-hooks`. On a new host it skips the service until the webhook secret
     exists.
   - `<agent>/setup.sh` for every agent this VM runs. Valac's refuses to install without `axiom_token`; Marbas's
     refuses without `MARBAS_CHANNELS`. Orobas's primes its bare clone, which can take a few minutes.
6. **The webhook.**

   ```sh
   /opt/legion/venv/bin/python /opt/legion/repo/shared/hooks/host/webhook.py https://<host>/webhooks/github
   ./setup.sh hooks
   ```

   `webhook.py` creates the repository webhook (or updates the one with that URL) for every event some agent on this
   VM subscribes to, generates its secret, and writes `github_webhook_secret` and `github_webhook_id`. It never
   prints the secret. `--rotate` writes a new secret on the existing hook; run `./setup.sh hooks` after it.

Check it:

```sh
systemctl status legion-hooks orobas marbas-slack
systemctl list-timers valac-patrol.timer
curl -s https://<host>/healthz          # "ok"
```

GitHub's webhook page shows the `ping` delivery with a 202.

### Caddy

`shared/host/Caddyfile` is the host's one public door. Caddy gets the TLS certificate itself. `/webhooks/github`
goes to the hooks service on `127.0.0.1:8645` with a 1 MB body limit, `/healthz` answers `ok`, and every other path
is a 404. A host with its own reverse proxy leaves `LEGION_HOSTNAME` empty, sends `/webhooks/github` to
`LEGION_HOOKS_HOST:LEGION_HOOKS_PORT`, and sets `LEGION_HOOKS_HOST=172.17.0.1` when the proxy runs in a container.

### Azure Key Vault

On an Azure VM, the secrets can come from Key Vault with the VM's managed identity, so no value is ever typed on the
host.

1. Give the VM a system-assigned managed identity with permission to get secrets from one vault.
2. Store each secret file under its name with dashes instead of underscores (`github_host_token` becomes
   `github-host-token`), since Key Vault allows no underscore.
3. The webhook pair is stored per agent: `github-webhook-secret-<agent>` and `github-webhook-id-<agent>`. Store them
   after `webhook.py` writes the files on the first run.
4. Set `LEGION_KEY_VAULT=<vault name>` and `LEGION_AGENTS` in `legion.env`, and `LEGION_HOSTNAME`, since the vault
   step runs inside `shared/host/setup.sh`.

`shared/lib/keyvault.py` reads the names from each agent's `secrets.txt` and writes the files mode 600. A missing
webhook pair is skipped with a note; any other missing secret stops the setup.

## Updating

```sh
cd /opt/legion/repo && git pull && ./setup.sh
```

The full run rebuilds the images (a change to `legion.env` reaches the managed settings and the allowlist this way),
reinstalls the units and restarts every service. After a change to `legion.env` only, `./setup.sh <agent>` restarts
that agent. A running review or fix is cut by the restart; each agent puts its work back on the queue at boot (see
each agent's doc).

The skills and the starter rulebook are mounted from `/opt/legion/repo`, so a `git pull` changes them for the next
run, with no restart.

## Logs

Each service logs to journald:

```sh
journalctl -u legion-hooks -f
journalctl -u marbas-slack -f
journalctl -u orobas -f
journalctl -u valac-patrol --since today
```

Each sandbox run keeps its files in `/opt/legion/workspace/<agent>/runs/<id>/`: the inputs in `in/`, the Claude Code
output (`claude.json`, or `claude-<stage>.json` per stage for Orobas), the result, the patch, and `container.log`.

### The run log in Axiom

With `axiom_ingest_token`, every agent sends its events to one Axiom dataset, `LEGION_LOG_DATASET`. Every event has
the same fields, so one APL query reads every agent:

| Field | What it holds |
| --- | --- |
| `_time` | UTC time of the event |
| `level` | `info`, `warn` or `error` |
| `type` | The event, as `marbas.thread.stage` or `orobas.review.posted` |
| `message` | One line for a person |
| `environment` | `LEGION_ENVIRONMENT` |
| `service` | `hooks`, `marbas`, `valac` or `orobas` |
| `module` | The part of the agent that sent it |
| `traceId` | The Slack thread key, the PR and head (`pr-<n>-<sha9>`), the finding fingerprint, or `heartbeat` |
| `host` | The VM's host name |
| `durationMs`, `turns`, `costUsd` | For a run: totals from the Claude Code output |
| `errorType`, `errorMessage` | For a failure: the exception type and a one-line cause with no command output |

Events add their own fields: `issue`, `pr`, `sha`, `stageFrom`, `stageTo`, `outcome`, `mode`, `event`, `blockers`,
`lanes`, `persona`, `model`, `sinceEventMs` (latency from the webhook delivery) and more. The event types of each
agent are listed in its doc.

Every long-lived service sends a heartbeat every five minutes (`hooks.heartbeat`, `marbas.heartbeat`,
`orobas.heartbeat`). Alert on its absence: no heartbeat means nobody answers.

```kusto
['legion'] | where level == "error" | summarize count() by service, type, bin(_time, 1h)
```

## Kill switches

| Switch | Effect |
| --- | --- |
| `MARBAS_ENABLED=false` | No new bug thread starts; Marbas says so in the thread. Open PRs keep their deploy watch and proof. |
| `VALAC_ENABLED=false` | The patrol starts and exits at once. Off by default. |
| `OROBAS_ENABLED=false` | No new review. The events wait in the inbox, and turning it back on reviews them. |
| `legion:skip` label on a PR | Orobas does not review it. |
| `systemctl stop <unit>` | Stops the service. `systemctl stop valac-patrol.timer` stops the schedule. |

After a change to `legion.env`, run `./setup.sh <agent>` to restart that agent. To stop a run in flight:
`docker rm -f marbas-run-<issue>`, `valac-run-<mode>-<issue>` or `orobas-run-<pr>`.

The softer switches keep a person in charge without stopping the agent: `MARBAS_AUTO_MERGE`,
`MARBAS_ADDRESS_REVIEW`, `MARBAS_REPLAN_ON_REJECT`, `MARBAS_SHADOW_CHANNEL`, `VALAC_CAN_OPEN_PR`,
`OROBAS_CAN_APPROVE` and `OROBAS_CAN_BLOCK`.

## Running a step by hand

Each `run.sh` can be run by hand as root; it takes the agent's lock, so it waits for or refuses a concurrent run.

```sh
/opt/legion/repo/marbas/sandbox/run.sh 1234 --plan            # prints result.json
/opt/legion/repo/valac/sandbox/run.sh --patrol
/opt/legion/venv/bin/python /opt/legion/repo/orobas/host/reviewer.py --dry-run 1234   # prints, never posts
```

`--dry-run <pr> --from-run <run-dir>` replays an Orobas run's files through the host's decision with no model.
