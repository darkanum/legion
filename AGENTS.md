# Working in this repository

This is the guide for anyone who changes Legion: people and coding agents alike. It holds the rules that keep the
three agents apart, keep the host safe, and keep the project usable by any team. How the agents work is in
[docs/](docs/); how to run a host is in [docs/HOST.md](docs/HOST.md).

| Folder | What it holds | Doc |
| --- | --- | --- |
| `marbas/` | Marbas: a Slack bug thread to a PR, then the deploy watch and the production proof | [docs/MARBAS.md](docs/MARBAS.md) |
| `valac/` | Valac: the log patrol that opens one issue per new defect, and optionally the PR | [docs/VALAC.md](docs/VALAC.md) |
| `orobas/` | Orobas: the PR review ladder, and the starter rulebook in `orobas/rules/` | [docs/OROBAS.md](docs/OROBAS.md) |
| `shared/hooks/` | The webhook service that hands every GitHub event to the agents | [docs/HOOKS.md](docs/HOOKS.md) |
| `shared/` | Everything else two or more agents use | [docs/HOST.md](docs/HOST.md) |

## The folder rule

```
setup.sh               # installs or updates everything on the host, in order
legion.env.example     # every setting, with its default
shared/                # what two or more agents use; it names no agent
├── lib/               # host Python: paths.py, config.py, github.py, pull_request.py, inbox.py, axiom.py,
│                      # jsonstore.py, sandbox_run.py, keyvault.py, and their tests
├── paths.sh           # every host path for the shell scripts, the same values as lib/paths.py
├── sandbox/           # the one image, the egress proxy, settings.py, lib/boot.sh, bin/, run-sandbox.sh
├── hooks/             # the GitHub webhook service; it reads every agent's subscriptions.json
├── host/              # a fresh VM: Docker, Caddy and the secrets from Azure Key Vault
└── skills/            # the skills two or more agents run
<agent>/               # one agent, and only its own code
├── subscriptions.json # the GitHub events and actions the agent receives
├── secrets.txt        # the secret files the agent reads, one name per line, never a value
├── setup.sh           # installs or updates the agent's services
├── host/              # the process on the host: code, systemd units, tests. No model runs here
├── sandbox/           # run.sh (inputs, limits, mounts) and the entrypoint mounted into the shared image
└── skills/            # the skills only this agent runs
docs/                  # every document
```

## The rules

- **`shared/` holds only what two or more agents use, and names no agent.** Code that one agent needs stays in that
  agent's folder. A shared script finds the agents by their folders (`*/setup.sh`, `*/subscriptions.json`,
  `*/secrets.txt`, `*/host/requirements.txt`), never by a list of names.
- **An agent folder holds only its own code:** `subscriptions.json`, `secrets.txt`, `setup.sh`, `host/`, `sandbox/`
  and `skills/`. A new agent adds a folder, and nothing in `shared/` changes.
- **No agent imports from another agent.** A host process adds `shared/lib` to `sys.path`. A shell script sources
  `shared/paths.sh` and `shared/sandbox/run-sandbox.sh`. When two agents need the same code, it moves to `shared/`.
- **Every host path is defined once**: `shared/lib/paths.py` for Python, `shared/paths.sh` for shell. Never write
  `/opt/legion/...` anywhere else, except in a systemd unit, which cannot source either file.
- **Every setting is read in `shared/lib/config.py`**, from the environment that systemd loads from `legion.env`. A
  new setting gets a line, with its default and a comment, in `legion.env.example`. An agent-only switch takes the
  agent's prefix (`MARBAS_`, `VALAC_`, `OROBAS_`); a shared one takes `LEGION_`.
- **No agent polls GitHub.** A GitHub event reaches an agent through `shared/hooks/` and its inbox. A local timer is
  fine; a loop that lists PRs or checks is not.
- **The write token stays on the host.** A sandbox gets only read-only tokens, through an env file that is deleted
  when the container starts. Push, PR, review and merge belong to the host.
- **The host checks every model output.** A patch passes `pull_request.scope_error` before it is pushed. Any model
  text that reaches GitHub or Slack has its mentions escaped and its attribution lines dropped. A review state is
  computed by code, never read from a model.
- **One sandbox image for every agent.** An agent mounts its own entrypoint into `legion-sandbox:local` at run time;
  it never bakes it into the image.
- **Tests are plain `unittest`**, with no install: `cd <folder>/host && python3 -m unittest` (and `shared/lib`,
  `shared/hooks/host`, `shared/sandbox`). A module that imports a third-party package (as `slack_sdk`) keeps the
  logic the tests need in a module that does not.
- **Every long-lived process sends a heartbeat**, and every event goes to the run log through `axiom.emit(service,
  ...)`, with one `service` per agent.
- **Docs live in `docs/`.** A new agent gets `docs/<AGENT>.md` and a row in the table above. A code comment says why;
  the doc says how the parts fit.
- **English only**, in code, comments, docs, commits and the text the agents post.
- **Nothing names a specific company, person or account.** Legion runs for any team: names, ids, channels and
  repositories come from `legion.env`. Examples use neutral names, as `acme/shop`.

## Adding an agent

1. Pick a daemon of the Ars Goetia whose office matches the work, and create its folder.
2. Write `subscriptions.json` (the events it needs, or none), `secrets.txt` (the secret files it reads), `setup.sh`
   (it calls `require_shared_sandbox` first), `host/` with its process, units and tests, and `sandbox/run.sh` with
   its entrypoint. Use `sandbox_lock`, `sandbox_env_file` and `sandbox_run` from `run-sandbox.sh`.
3. Add its settings to `shared/lib/config.py` or read them with `config.text`, `config.flag`, `config.number` and
   `config.items`, and list them in `legion.env.example`.
4. Run `webhook.py` again on the host so the GitHub hook takes the new events.
5. Write `docs/<AGENT>.md`.

## Skills and rules

The skills each sandbox runs live in `shared/skills/`, `marbas/skills/`, `valac/skills/` and `orobas/skills/`, one
folder per skill with a `SKILL.md`. The starter review rulebook lives in `orobas/rules/`. Both are mounted read-only
from the host copy, so a change to them reaches a run only after it is merged here and pulled on the host.
