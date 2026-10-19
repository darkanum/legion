# Architecture

Legion is three agents that share one host, one webhook service and one sandbox image. This page draws them in the
[C4 model](https://c4model.com): the system in its context, the containers inside it, then one dynamic view per
daemon and one of how they meet. Each agent has its own page for the details: [MARBAS.md](MARBAS.md),
[VALAC.md](VALAC.md), [OROBAS.md](OROBAS.md), and [HOOKS.md](HOOKS.md) for the event path.

## 1. System context

Who and what Legion talks to. People stay in the loop at every decision that changes code.

```mermaid
flowchart TB
  engineer["👤 <b>Engineer</b><br/><i>Person</i><br/>Approves plans, reviews and merges<br/>PRs, closes wrong findings"]:::person
  reporter["👤 <b>Bug reporter</b><br/><i>Person or a form bot</i><br/>Posts a bug issue link in Slack"]:::person
  legion["<b>Legion</b><br/><i>Software system</i><br/>Marbas fixes bugs, Valac patrols the logs,<br/>Orobas reviews pull requests"]:::system
  github["<b>GitHub</b><br/><i>External system</i><br/>Issues, PRs, reviews, CI,<br/>deployments, webhooks"]:::external
  slack["<b>Slack</b><br/><i>External system</i><br/>Bug threads, plans with<br/>Approve and Reject"]:::external
  claude["<b>Anthropic API</b><br/><i>External system</i><br/>Claude Code, only inside the sandbox"]:::external
  telemetry["<b>Axiom and PostHog</b><br/><i>External system</i><br/>Production logs and events, read-only"]:::external

  reporter -- "posts a bug issue link" --> slack
  engineer -- "approves or rejects plans" --> slack
  engineer -- "reviews and merges" --> github
  slack -- "thread messages and clicks<br/>(Socket Mode)" --> legion
  github -- "signed webhook events" --> legion
  legion -- "branches, PRs, issues, reviews" --> github
  legion -- "plans, progress, hand-offs" --> slack
  legion -- "headless runs, via the egress proxy" --> claude
  legion -- "evidence and proof queries" --> telemetry

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

## 2. Containers

What runs on the host. No model ever runs here: the host processes only move events, start sandboxes and check what
comes back. Every model step runs in a fresh container on a network with no route out except the proxy.

```mermaid
flowchart TB
  github["<b>GitHub</b><br/><i>External system</i>"]:::external
  slack["<b>Slack</b><br/><i>External system</i>"]:::external
  outside["<b>Anthropic, Axiom, PostHog,<br/>package registries</b><br/><i>External systems</i>"]:::external

  subgraph host["Legion host: a Linux VM with Docker"]
    edge["<b>Caddy or a tunnel</b><br/><i>HTTPS</i><br/>The one public door:<br/>only /webhooks/github"]:::container
    hooks["<b>hooks service</b><br/><i>Python, systemd</i><br/>Checks the HMAC signature,<br/>one small file per agent"]:::container
    inboxes[("<b>Inboxes and state</b><br/><i>JSON files</i><br/>inboxes, threads, PR states,<br/>the findings ledger")]:::store
    marbas["<b>Marbas</b><br/><i>Python, Slack Socket Mode</i><br/>Thread to plan to PR,<br/>CI repair, deploy, proof"]:::container
    valac["<b>Valac</b><br/><i>Python, systemd timer</i><br/>Patrol to issue to PR,<br/>verified or regressed"]:::container
    orobas["<b>Orobas</b><br/><i>Python, systemd</i><br/>Gate 0, the ladder,<br/>the review state"]:::container
    sandbox["<b>Sandbox</b><br/><i>Docker, Claude Code</i><br/>Read-only token, managed<br/>settings, skills read-only"]:::sandbox
    egress["<b>Egress proxy</b><br/><i>tinyproxy</i><br/>Allowlisted hosts only"]:::container
  end

  github -- "webhook" --> edge --> hooks -- "one file per agent" --> inboxes
  inboxes --> marbas & valac & orobas
  slack -- "Socket Mode" --> marbas
  marbas & valac & orobas -- "run.sh" --> sandbox
  sandbox -- "every outbound request" --> egress -- "allowlist" --> outside
  marbas & valac & orobas -- "checked patch, PR, issue, review<br/>(write token, host only)" --> github

  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef store fill:#438dd5,stroke:#2e6295,color:#fff
  classDef sandbox fill:#0f766e,stroke:#0b544e,color:#fff
  style host fill:#ffffff00,stroke:#888,stroke-dasharray: 5 5
```

## 3. Marbas: a bug thread becomes a proven fix

```mermaid
sequenceDiagram
  autonumber
  actor R as Reporter
  actor E as Engineer
  participant S as Slack
  participant M as Marbas host
  participant X as Sandbox
  participant G as GitHub
  R->>S: Posts the link to a bug issue
  S->>M: Message event (Socket Mode)
  M->>G: Is the issue open, labelled bug, with no open PR?
  M->>X: /triage-bug --plan
  X-->>M: result.json: what was found, the cause, the fix, the proof, the files
  M->>S: The plan, with Approve and Reject
  E->>S: Approve
  M->>X: /triage-bug --from-plan (fix, then lint, tests, build)
  X-->>M: fix.patch and result.json
  M->>M: Applies the patch in a clean clone, checks every path git staged against the plan
  M->>G: Pushes fix/issue-N and opens the PR
  G-->>M: CI red (webhook)
  M->>X: --fix-ci with the failed job logs
  M->>G: Pushes a CI fix commit
  E->>G: Approves and merges
  G-->>M: Deployment success (webhook)
  M->>S: Live in production
  M->>X: An hour later: --prove against telemetry
  M->>S: Proved, or a person must check
```

## 4. Valac: a patrol finds a defect and follows it to the end

```mermaid
sequenceDiagram
  autonumber
  participant T as systemd timer
  participant V as Valac host
  participant X as Sandbox
  participant L as Axiom
  participant G as GitHub
  actor E as Engineer
  T->>V: Patrol (VALAC_SCHEDULE)
  V->>V: Applies the merges and closes from its inbox
  V->>X: /valac-patrol over the last window
  X->>L: APL queries (read-only token)
  X-->>V: patrol.json: findings with the query, the counts and the code location
  V->>V: Fingerprints, drops the known and the snoozed, keeps the best within the cap
  V->>G: Opens one issue per new finding, assigned to the file's recent owner
  opt VALAC_CAN_OPEN_PR
    V->>X: /triage-bug --plan, then --from-plan
    V->>G: Opens the PR that closes the issue
  end
  E->>G: Merges, or closes the issue as not planned (snoozed for 30 days)
  T->>V: A later patrol, a full window after the merge
  V->>V: Gone from the logs: verified. Still there: regressed, raised again
```

## 5. Orobas: a push becomes a review

```mermaid
sequenceDiagram
  autonumber
  actor A as Author
  participant G as GitHub
  participant O as Orobas host
  participant X as Sandbox
  A->>G: Opens or pushes a PR
  G-->>O: pull_request event (via the hooks service)
  O->>O: Waits 90 s for a quiet head, checks the seat budget
  O->>O: Gate 0 on the host: issue link, title, base, template, attribution, env files, conflict markers
  alt A Gate 0 Blocker
    O->>G: REQUEST_CHANGES or COMMENT, and the report
  else Clean
    O->>X: The ladder, with the rules from the base branch
    par
      X->>X: Furcas, Gate 1: the standards
    and
      X->>X: Bathin, Gate 2: picks the lanes
    end
    par
      X->>X: The lanes: Eligos, Zagan, Paimon, Amon, Seere, Halphas, Sabnock, Andrealphus, Foras
    and
      X->>X: Dantalion, Gate 3: the intent
    end
    X->>X: Orobas writes the summary
    X-->>O: The gate files
    O->>O: Decides the event from the files, never from a model
    O->>G: The review, inline comments with their rule ids, lane labels, the report comment
  end
  A->>G: Pushes a fix
  G-->>O: synchronize
  O->>X: The light recheck: which of my threads does this push fix?
  O->>G: Resolves the fixed threads, edits the report
```

## 6. How the daemons meet

The three never call each other. They meet on GitHub, through the same events every person sees.

```mermaid
flowchart LR
  subgraph People
    E[Engineer]
  end
  subgraph Legion
    V[Valac<br/>patrol]
    M[Marbas<br/>fixer]
    O[Orobas<br/>reviewer]
  end
  G[(GitHub)]
  S[(Slack)]
  V -- "opens a bug issue with evidence" --> G
  V -- "can open the PR for its own issue" --> G
  S -- "a bug thread links an issue" --> M
  M -- "opens the fix PR" --> G
  G -- "every PR, including the agents' PRs" --> O
  O -- "COMMENT only on an agent's PR:<br/>a bot never approves a bot" --> G
  E -- "approves, merges, requests changes" --> G
  G -- "an engineer's change request<br/>starts Marbas's next round" --> M
  G -- "merges and closes<br/>teach Valac's ledger" --> V
```

- **Valac to Marbas.** A Valac issue is a bug issue with the query as evidence. With `VALAC_CAN_OPEN_PR` on, Valac
  runs the same `/triage-bug` skill Marbas uses and opens the PR itself; without it, a person can post the issue in a
  Marbas channel and Marbas takes it from there.
- **Marbas and Valac to Orobas.** Orobas reviews every PR an engineer can merge, the agents' PRs included. It never
  approves or blocks a PR written by a bot account (GitHub forbids an author to approve its own PR, and a bot must not
  vouch for a bot), so it only comments and a person decides.
- **People to all three.** An engineer's change request on a Marbas PR starts Marbas's next round
  (`MARBAS_ADDRESS_REVIEW`). A merge or a close teaches Valac's ledger. A dismissal in an Orobas thread is how a rule
  gets fixed.
