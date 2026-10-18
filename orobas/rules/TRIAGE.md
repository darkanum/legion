# Triage

Gate 2 reads the diff and picks the review lanes that fit the change. Each lane is a reviewer with one checklist. The
standards (Gate 1) and the intent (Gate 3) run on every PR, so a lane is only for a risk they do not cover in depth.

## How to pick

1. Read the list of touched files in pr.json, then skim diff.patch.
2. For each lane below, check its signals. A lane applies when the change itself touches its risk, not when a file
   merely sits near it.
3. Keep **at most 4 lanes**. When more apply, keep the riskiest, in this order: `security`, `data`, `ai`, `product`,
   `performance`, `devops`, `infra`, `design`, `tests`.
4. No lane is a fine answer: a docs-only, comment-only or formatting-only PR needs none.

Prefer fewer lanes. A lane that finds nothing costs a full review for no gain.

## The lanes

### security

Who can reach what, and what leaves the system.

- Paths: `auth`, `login`, `session`, `permission`, `policy`, `middleware`, `crypto`, `token`, `webhook`, `upload`.
- Diff: a new or changed endpoint or route; a check on roles or ownership; raw SQL or a shell call built from input;
  HTML rendered from input; a redirect to a URL from input; file paths from input; secrets, keys or cookies;
  CORS, CSP or security headers; a new third-party call that sends user data.

### data

Schemas, migrations, queries and indexes.

- Paths: `migrations`, `schema`, `models`, `entities`, `repositories`, `*.sql`, ORM model files.
- Diff: a new or changed table, column, index or constraint; a migration; a backfill or data fix script; a change in
  how rows are written, deleted or soft-deleted; a transaction; a new query on a large table.

### ai

Model calls, prompts, evaluations and their cost.

- Paths: `prompts`, `llm`, `agents`, `ai`, `evals`, files that hold prompt templates.
- Diff: a call to a model API; a prompt text; model name, temperature or token limits; parsing of model output;
  tool definitions for a model; retrieval or embeddings; an eval set or its thresholds.

### product

What was asked, and what the user gets.

- Diff: a change a user sees or feels: copy, a flow, a default, an email, a notification, an error message, a price,
  a limit, a permission a user notices, a feature flag that gates user-facing behaviour.
- Skip it for internal refactors, tooling and infrastructure with no visible effect.

### performance

Hot paths, memory, reads and queues.

- Diff: a loop that calls the database, the network or a model; a query inside a loop (N+1); a new list endpoint
  with no pagination; loading a whole table or file into memory; a cache, or its removal; a change on a request path
  the code marks as hot; work moved into or out of a queue or a background job; a large bundle import on the client.

### devops

CI, images, health checks, logs and monitors.

- Paths: `.github/workflows`, CI config files, `Dockerfile`, `docker-compose*`, `Makefile`, deploy scripts,
  health-check routes, logging or metrics setup, alert definitions.
- Diff: a build or test step; an image base or version; a start command; a health check; a new log line on an
  important path; a metric or alert.

### infra

Infrastructure as code, names and cost.

- Paths: `terraform`, `*.tf`, `pulumi`, `cdk`, `helm`, `k8s`, `charts`, cloud template files.
- Diff: a new or changed resource; a size, a replica count, a region; network rules; IAM roles and policies; a
  bucket or a queue; anything that changes the monthly bill.

### design

The build matches the design and the design system.

- Paths: components, pages, views, styles, `*.css`, `*.scss`, theme or token files, templates.
- Diff: new or changed UI: layout, spacing, colours, type, icons, states (loading, empty, error), responsive
  behaviour, keyboard and screen reader access.

### tests

The behaviour is proven.

- Diff: new behaviour with no test change; tests that change together with the code they check; tests deleted,
  skipped or weakened; snapshots updated in bulk; new mocks of the thing under test; flaky patterns such as sleeps
  or real time and network in a unit test.
- Pick it when the tests are the risk. Gate 1 already asks for tests on changed behaviour (`STD-10`).

## Output

The lane ids, riskiest first, as `{"lanes": ["security", "tests"]}`. The host drops any id not in the list above and
keeps the first four in risk order.
