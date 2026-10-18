# The Orobas rulebook

Orobas reviews a pull request against the rules in this folder. Every finding it posts names one rule id, so a
person can always see which rule a comment comes from, and fix the rule when the rule is wrong.

This folder is the starter rulebook. It is generic on purpose: it holds for most repositories in most stacks. A team
replaces it, or parts of it, with its own.

## The files

| File | Read by | What it holds |
| --- | --- | --- |
| `STANDARDS.md` | Gate 0 (host) and Gate 1 | The house rules every PR follows: `STD-01` onward |
| `TRIAGE.md` | Gate 2 | When each review lane applies |
| `INTENT.md` | Gate 3 | How to judge whether the change does what its issue asks |
| `VOICE.md` | every stage | How a comment is written |
| `LANE_<LANE>.md` | one lane each | A short checklist for one kind of risk |

The lanes are `security`, `data`, `ai`, `product`, `performance`, `devops`, `infra`, `design` and `tests`. Their
files are `LANE_SECURITY.md`, `LANE_DATA.md` and so on.

## How a review runs

1. **Gate 0** runs on the host, with no model, in about a second. It checks the rules that pr.json and the diff text
   decide alone (see below). A Gate 0 Blocker stops the review before any model runs.
2. **Gate 1, Standards** reads `STANDARDS.md` with judgement. A Gate 1 Blocker stops the rest of the ladder.
3. **Gate 2, Triage** picks the lanes that fit the change, at most four, by `TRIAGE.md`.
4. **The lanes and Gate 3, Intent** run in parallel. Each lane reads its own `LANE_<LANE>.md`; Gate 3 reads
   `INTENT.md`.
5. **The report** writes a short summary. The host, not a model, renders the table and picks the review state.

## Overriding the rulebook in a repository

A repository uses its own rules by adding a `.legion/rules/` folder on its base branch, with the same file names as
here. The folder can be moved with `LEGION_RULES_DIR`.

- The rules are always read from the **base branch**, never from the PR. A PR cannot change the rules it is reviewed
  by; a change to the rules takes effect once it is merged.
- The repository folder is used when it holds a `STANDARDS.md`. A file it does not hold (a lane it did not write,
  say) is read from this starter set instead, so a team can start with `STANDARDS.md` alone and add files over time.
- Keep the rule ids stable. A finding cites its id, and a renamed id breaks the link between old comments and rules.
- Keep the `STD-01` to `STD-07` headings. Gate 0 reports findings with those ids, whatever the text says.

## Rule ids

Each rule is a heading with an id and a short title, as `## STD-12 Errors are handled or surfaced`. The prefix says
which file owns it:

| Prefix | File |
| --- | --- |
| `STD-` | `STANDARDS.md` |
| `INT-` | `INTENT.md` |
| `SEC-`, `DATA-`, `AI-`, `PROD-`, `PERF-`, `OPS-`, `INFRA-`, `DES-`, `TEST-` | the lane files |

A new rule takes the next free number. A removed rule leaves its number unused.

## Severities

Every finding has exactly one severity. Each rule states its default; a reviewer moves it up or down when the case
clearly calls for it, and says why.

| Severity | Meaning | Effect |
| --- | --- | --- |
| `blocker` | The PR must not merge as it is: it breaks behaviour, loses or leaks data, opens a hole, or breaks a rule the team made hard. | Requests changes. A Blocker in Gate 0 or Gate 1 stops the ladder. |
| `important` | A reviewer would ask for this change before merging, but the PR is not broken without it. | Counted and shown; does not block on its own. |
| `note` | A small improvement the author may take or leave. Starts with "Nit:" when it is about style. | Shown; never blocks. |
| `followup` | Real, but outside this PR. Worth an issue later. | Shown; never blocks. |

When in doubt between two severities, pick the lower one. A false Blocker costs the team more than a missed note.

## What Gate 0 checks on the host

Gate 0 checks these rules with plain code, before any model runs. Gate 1 does not repeat them.

| Rule | Check | Severity |
| --- | --- | --- |
| `STD-01` | The PR links an issue GitHub registers (`Closes #N`), or carries the `no-issue` label | blocker |
| `STD-02` | The title is a conventional title whose type matches the branch prefix | important (a branch with no known prefix: note) |
| `STD-03` | The base is the trunk (or an allowed base, or the head of a stacked PR), there are no conflicts, and no commit merges a long-lived branch | blocker |
| `STD-04` | The body holds no unfilled prompt or empty step of the PR template | important |
| `STD-05` | No AI attribution line in the body or the commits | important |
| `STD-06` | No added env file (`.env`, `.env.*`, `*.env`), except templates ending in `.example`, `.sample` or `.template` | blocker |
| `STD-07` | No merge conflict marker in an added line | blocker |

The host settings that tune Gate 0: `OROBAS_BRANCH_TYPES`, `OROBAS_TITLE_NEEDS_SCOPE`, `OROBAS_EXTRA_BASES`,
`OROBAS_FORBIDDEN_MERGES`, `OROBAS_REQUIRE_ISSUE` and `OROBAS_FORBID_ATTRIBUTION`. `OROBAS_ALWAYS_BLOCK` lists the
rules that request changes even when Orobas is not allowed to block otherwise (by default `STD-01`).

## Writing a good rule

- One rule, one idea. If a rule needs "and also", it is two rules.
- Say **why** the rule exists. A reviewer applies a rule well only when it knows what the rule protects.
- Say **what a finding looks like**: one concrete example comment.
- Give it a default severity, and keep Blockers for what truly must not merge.
- Write it for any stack. Name a pattern, not a product.
