# The webhook service and the inbox

No Legion agent polls GitHub. One small service, `legion-hooks`, receives every GitHub event, and writes a file into
the inbox of each agent that wants it. The agent reads its inbox, a local folder, and calls the GitHub API only for
the facts it needs.

Code: `shared/hooks/host/hooks.py` (the service), `shared/hooks/host/webhook.py` (creates the GitHub hook),
`shared/lib/inbox.py` (the inbox, on both sides).

## The flow

```
GitHub ──POST /webhooks/github──> Caddy ──> legion-hooks (127.0.0.1:8645)
                                              │ 1. size ≤ 1 MB, else 413
                                              │ 2. X-Hub-Signature-256 is a valid HMAC, else 401
                                              │ 3. the body is JSON, else 400
                                              │ 4. one event file per subscriber
                                              │ 5. 202
                                              ▼
                       /opt/legion/workspace/<agent>/inbox/<ms>-<delivery>.json
```

A `ping` event gets a 202 and goes to nobody. The service answers before any agent work, so GitHub never times out
on Legion.

## Subscriptions

Each agent declares the events it takes in `<agent>/subscriptions.json`:

```json
{
  "agent": "valac",
  "events": {
    "pull_request": ["closed"],
    "issues": ["closed"]
  }
}
```

An event maps to a list of actions, or to `null` for every action. The service reads every agent folder's file at
start, and only the agents in `LEGION_AGENTS` when it is set, so a VM that runs one agent gets only that agent's
events. The service names no agent itself.

| Agent | Events |
| --- | --- |
| Marbas | `pull_request` closed, `pull_request_review` submitted, `check_suite` completed, `workflow_run` completed, `deployment_status` (all) |
| Valac | `pull_request` closed, `issues` closed |
| Orobas | `pull_request` opened, reopened, ready_for_review, synchronize, review_requested, edited, labeled, unlabeled, closed; `check_suite` completed |

`webhook.py` sets the GitHub hook's event list to the union of these, so a new agent needs only a new run of
`webhook.py`.

## The inbox contract

An event file holds exactly these keys, and nothing from the payload text:

| Key | Type | What it is |
| --- | --- | --- |
| `delivery` | string | GitHub's `X-GitHub-Delivery` id |
| `event` | string | GitHub's `X-GitHub-Event`, as `pull_request` |
| `action` | string or null | The payload's `action` |
| `number` | integer or null | The PR or issue number, when the event names one |
| `sha` | string or null | The commit the event is about |
| `received_at` | string | UTC time the service received it, `YYYY-MM-DDTHH:MM:SSZ` |

How `number` and `sha` are filled:

| Event | `number` | `sha` |
| --- | --- | --- |
| `pull_request`, `pull_request_review` | `pull_request.number` | `pull_request.head.sha` |
| `check_suite` | the first of `check_suite.pull_requests` | `check_suite.head_sha` |
| `workflow_run` | the first of `workflow_run.pull_requests` | `workflow_run.head_sha` |
| `deployment_status` | null | `deployment.sha` |
| `issues` | `issue.number` | null |
| any other | null | null |

A value of the wrong type becomes null. The payload is untrusted and never stored: the agent reads the state of the
PR, the issue or the commit from the API.

The file name is `<epoch milliseconds>-<delivery>.json`, so a sorted listing is arrival order. Each file is written
to a temporary name in the same folder and renamed, so a reader never sees half a file.

### Reading the inbox

An agent calls `inbox.drain(agent)` or `inbox.consume(agent, handle, failed)`:

- `drain` returns every valid event in file-name order and deletes each file once read. A file that does not parse,
  or has no string `delivery` and `event`, moves to `inbox/bad/`.
- `consume` drains into `handle`, one event at a time. An event whose handler raises goes to `failed` and never stops
  the rest of the batch. Since the file is gone by then, `failed` logs the whole event to the run log, so a person
  can replay it.

Marbas and Orobas read their inbox every two seconds. Valac reads it once per patrol.

`inbox.received_epoch(event)` and `inbox.since_ms(...)` give the latency from the webhook to the agent's action,
which the agents report as `sinceEventMs`.

## Missed deliveries

GitHub does not retry a failed delivery. When the service starts and `github_webhook_id` exists, it pages through the
hook's deliveries back to the last one it saw (`workspace/hooks/state.json`), and asks GitHub to redeliver every
failed delivery since then, oldest first, so an older push never lands after a newer one. The first start only records
the mark. The result is the `hooks.redelivered` event; a failure is `hooks.redeliver.failed`, and the service keeps
running.

## Settings and secrets

| Name | Where | Default | What it does |
| --- | --- | --- | --- |
| `LEGION_HOOKS_HOST` | `legion.env` | `127.0.0.1` | The listen address; `172.17.0.1` when the reverse proxy runs in a container |
| `LEGION_HOOKS_PORT` | `legion.env` | `8645` | The listen port |
| `LEGION_AGENTS` | `legion.env` | empty (all) | The agents whose subscriptions load |
| `github_webhook_secret` | `secrets/` | | The HMAC secret, written by `webhook.py` |
| `github_webhook_id` | `secrets/` | | The hook id, for redeliveries |

`webhook.py https://<host>/webhooks/github` creates the hook, or updates the one with that URL to the current events.
It keeps the existing secret unless `--rotate` is given or the secret file is missing. The host token needs the
repository Webhooks permission, read and write.

## Run log events

| Type | When |
| --- | --- |
| `hooks.received` | Every signed delivery: `event`, `action`, `agents`, `subscribers` |
| `hooks.rejected` | A bad or missing signature (warn) |
| `hooks.redelivered` | At boot: how many deliveries it asked GitHub to send again |
| `hooks.redeliver.failed` | The redelivery at boot failed (warn) |
| `hooks.heartbeat` | Every five minutes |

## Testing a delivery

GitHub's webhook page lists each delivery with its response and a Redeliver button. On the host:

```sh
journalctl -u legion-hooks -f
ls /opt/legion/workspace/*/inbox/
```
