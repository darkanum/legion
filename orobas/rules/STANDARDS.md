# Standards

The house rules every pull request follows. Gate 0 checks `STD-01` to `STD-07` on the host with plain code; Gate 1
reads every rule from `STD-08` on with judgement. See `README.md` for the severities.

Each rule says why it exists, what a finding looks like, and its default severity. Move a severity up or down only
when the case clearly calls for it, and say why in the comment.

---

## Rules Gate 0 checks on the host

Gate 0 checks `STD-01` to `STD-07` before any model runs. Gate 1 does not report them again: a finding with one of
these ids comes from the host.

## STD-01 The PR links an issue

The body links the issue it closes with `Closes #N` (or `Fixes #N`, `Resolves #N`), so GitHub registers the link. A
PR with no issue on purpose carries the `no-issue` label.

- **Why:** the issue says what was asked. Without it nobody can judge whether the change does the right thing, and
  the work leaves no trace in the plan.
- **What a finding looks like:** "The PR links no issue that GitHub registers. Could we add `Closes #N` to the body,
  or the `no-issue` label when it has none on purpose?"
- **Default severity:** blocker.

## STD-02 The title is conventional and matches the branch

The title reads `type(scope): message`, as `fix(api): reject an empty cart`. The type is one of the configured types
(by default `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build`) and matches the branch prefix:
branch `fix/empty-cart` takes a `fix` title. The scope is optional unless the repository requires it.

- **Why:** the title becomes the commit on the trunk. A consistent title makes the history searchable and lets tools
  build release notes.
- **What a finding looks like:** "The title does not follow `type(scope): message` with the type of the branch
  prefix."
- **Default severity:** important; a branch with no known prefix is a note.

## STD-03 The PR targets the right base and merges cleanly

The PR goes to the trunk (or to an allowed base, or to the head of the PR it is stacked on, said in the body as
`Stacked on #N`). It has no conflicts with its base, and no commit merges a long-lived branch into it.

- **Why:** a wrong base or a merged long-lived branch ships other people's unreviewed work under this review.
- **What a finding looks like:** "A commit merges a long-lived branch into this one, so this PR would ship other
  people's unreviewed work."
- **Default severity:** blocker.

## STD-04 The PR template is filled in

When the repository has a PR template, the body answers it: no template prompt is left, and no numbered step is left
empty.

- **Why:** the template asks for what a reviewer needs (how to test, what changes for the user). An unfilled
  template means the reviewer has to guess.
- **What a finding looks like:** "The body still holds a prompt or an empty step from the template."
- **Default severity:** important.

## STD-05 No AI attribution lines

Neither the body nor any commit message carries a line such as "Generated with ..." or "Co-authored-by:" for a tool.

- **Why:** the author owns the change, whatever tools helped. Attribution lines add noise to the history.
- **What a finding looks like:** "The body or a commit carries an AI attribution line. Could we drop it?"
- **Default severity:** important.

## STD-06 No committed env or secret files

The PR adds no `.env`, `.env.*` or `*.env` file. A template with placeholder values is fine when its name ends in
`.example`, `.sample` or `.template`.

- **Why:** a credential that enters git stays in its history forever, even after the file is deleted.
- **What a finding looks like:** "I think this commits an env file. A credential never enters git: could it live in
  the secret store instead?"
- **Default severity:** blocker.

## STD-07 No merge conflict markers

No added line starts with `<<<<<<<`, `=======` or `>>>>>>>`.

- **Why:** a marker left in breaks the build at best, and silently ships both sides at worst.
- **What a finding looks like:** "This looks like a merge conflict marker that was left in."
- **Default severity:** blocker.

---

## Rules Gate 1 reads with judgement

## STD-08 One concern per PR

A PR does one thing: one fix, one feature slice, one refactor. A refactor that the change needs is fine; an
unrelated clean-up, rename or reformat is a separate PR.

- **Why:** a mixed PR is harder to review, harder to revert, and hides the real change in noise.
- **What a finding looks like:** "I think the rename of `OrderRow` across `apps/web` is unrelated to the cart fix.
  Could it move to its own PR, so this one stays easy to revert?"
- **Default severity:** important.

## STD-09 The diff is small enough to review

A PR stays small enough that one reviewer can hold it in mind: as a rough guide, under about 400 changed lines, not
counting generated files, lockfiles, snapshots and fixtures.

- **Why:** review quality drops fast with size. Large PRs get skimmed and approved, not reviewed.
- **What a finding looks like:** "I think this PR could split in two: the new `PriceRules` module first, then the
  checkout change that uses it."
- **Default severity:** note; important when the size clearly hides risk.

## STD-10 Changed behaviour has tests

A change in behaviour comes with a test that fails without it: a new branch, a fixed bug, a new input. A pure
refactor keeps the existing tests green and needs none.

- **Why:** a test is the proof that the change works and the guard that it keeps working.
- **What a finding looks like:** "I think the new empty-cart branch in `checkout.ts:42` has no test. Could we add a
  case to `checkout.test.ts` that posts an empty cart and expects the 400?"
- **Default severity:** important; blocker for a bug fix that ships no regression test when one is easy to write.

## STD-11 No dead or commented-out code

The PR adds no code that never runs, no commented-out code, no unused import, variable or function, and no debug
leftovers (`print`, `console.log`, `debugger`) outside the logger.

- **Why:** dead code costs every future reader time and lies about what the system does. Git keeps the old version.
- **What a finding looks like:** "This block is commented out. Could we delete it? Git keeps it if we need it back."
- **Default severity:** important; a stray debug line is a note.

## STD-12 Errors are handled or surfaced, never swallowed

Every error is either handled (with a deliberate recovery) or passed up with context. No empty `catch`, no `except:
pass`, no ignored error return, no fallback that hides a failure from the caller and from the logs.

- **Why:** a swallowed error turns a loud, easy bug into a silent, expensive one.
- **What a finding looks like:** "I think this `catch` drops the error, so a failed charge looks like a success.
  Could it log the error and return the failure to the caller?"
- **Default severity:** important; blocker when the swallowed error can lose or corrupt data or money.

## STD-13 No secrets or personal data in code, fixtures or logs

No key, token, password or connection string in code, config, tests or fixtures. No personal data (names, emails,
addresses, identifiers of real people) in fixtures, examples or log lines. Logs never print a whole request, user
or token object.

- **Why:** code and logs are copied widely and kept long. A leaked secret must be rotated; leaked personal data may
  have to be reported.
- **What a finding looks like:** "I think this logs the whole `user` object, email included. Could it log only
  `user.id`?"
- **Default severity:** blocker for a real secret or real personal data; important for a risky log line.

## STD-14 Logging goes through the project logger

Code logs through the logger the project already uses, at a fitting level, with structured fields where the project
uses them. No raw prints to stdout in application code.

- **Why:** only the project logger reaches the place where people look, with the right level and context.
- **What a finding looks like:** "Nit: this uses `print`. Could it use the module logger at `info`, so the line
  reaches the logs?"
- **Default severity:** note.

## STD-15 Input is validated at the boundary

Data that enters from outside (a request, a form, a file, a queue message, a webhook, an environment variable) is
checked for shape, type and range where it enters, before the code relies on it.

- **Why:** a check at the edge keeps every function behind it simple and safe. A missing one becomes a crash, or a
  hole.
- **What a finding looks like:** "I think `quantity` reaches the price calculation unchecked, so `-3` gives a
  refund. Could the handler reject values below 1?"
- **Default severity:** important; blocker when the gap is exploitable (see the security lane).

## STD-16 Names say what a thing is

Names of variables, functions, files and types say what they hold or do, in the words of the domain. No `data2`,
`tmp`, `handleStuff`, or a name that says the opposite of what the code does.

- **Why:** a good name is the cheapest documentation, and a misleading one is a bug waiting for its next reader.
- **What a finding looks like:** "Nit: `items` holds the order ids, not the items. Could it be `orderIds`?"
- **Default severity:** note; important when the name is actively misleading.

## STD-17 No new dependency without a reason

A new package, library or service comes with a short reason in the PR body: what it does that the existing code or
the standard library cannot, and that it is maintained and licensed for the project.

- **Why:** each dependency adds attack surface, upgrade work and build time for as long as it stays.
- **What a finding looks like:** "I think `left-pad-plus` adds a dependency for one line the standard library
  covers. Could we use `padStart` instead, or say in the body why we need it?"
- **Default severity:** important.

## STD-18 Public interfaces stay compatible, or the change is documented

A change to an API, an event, a CLI flag, a config key or an exported function that others use is either backwards
compatible, or comes with its docs, a changelog line where the project keeps one, and the updated callers. The
consumers listed in the inputs show who uses a new or changed name.

- **Why:** a silent breaking change fails somewhere far from the PR that caused it.
- **What a finding looks like:** "I think renaming `total` to `amount` in the `/orders` response breaks the mobile
  client, which still reads `total`. Could we keep both for one release?"
- **Default severity:** important; blocker when it breaks a known consumer with no migration path.

## STD-19 Migrations are safe and reversible

A schema or data migration can run on a live system and can be rolled back: it has a down step or a stated reason it
cannot, it does not lock a large table for long, and code and schema can deploy in either order.

- **Why:** a migration that cannot roll back turns a bad deploy into an outage.
- **What a finding looks like:** "I think this migration drops `legacy_price` with no down step, while the running
  version still reads it. Could the drop move to a later PR, after the code stops reading it?"
- **Default severity:** important; blocker for an irreversible loss of data.

## STD-20 No TODO without an issue

A `TODO`, `FIXME` or `HACK` the PR adds names an issue, as `TODO(#123): ...`.

- **Why:** a TODO with no issue is a promise nobody tracks.
- **What a finding looks like:** "Nit: could this TODO name an issue, as `TODO(#N)`, so it is tracked?"
- **Default severity:** note.

## STD-21 Comments explain why, not what

A comment says why the code is the way it is: the constraint, the trade-off, the bug it avoids. It does not repeat
what the next line plainly does, and it stays true after the change.

- **Why:** the code already says what. Only a comment can say why, and a stale comment misleads.
- **What a finding looks like:** "Nit: this comment repeats the line below. Could it say why the retry stops at
  three instead?"
- **Default severity:** note; important when a comment the PR touches is now false.

## STD-22 Configuration is not hard-coded

Values that differ by environment or change over time (URLs, hosts, limits, feature switches, ids of external
resources) come from the project's configuration, not from literals in code.

- **Why:** a hard-coded value forces a code change and a deploy for what should be a setting, and often points
  production at the wrong place.
- **What a finding looks like:** "I think `https://staging.example.com` is hard-coded here, so production would call
  staging. Could it come from the config?"
- **Default severity:** important.

## STD-23 The house guide is followed

The PR follows the repository's own guide files (the root `AGENTS.md` or `CLAUDE.md` on the base branch, and those
on the paths it touches). A finding under this rule quotes the guide line it relies on.

- **Why:** the guide records decisions the team already made. Following it saves the same discussion every time.
- **What a finding looks like:** "The guide in `apps/web/AGENTS.md` says that every fetch goes through `apiClient`.
  I think this one calls `fetch` directly: could it use `apiClient`?"
- **Default severity:** important, unless the guide marks the rule otherwise.

## STD-24 No duplicated logic where a helper exists

The PR does not write again what the codebase already has: a helper, a type, a constant, a component.

- **Why:** two copies drift apart, and the next fix lands in only one of them.
- **What a finding looks like:** "I think `formatPrice` in `apps/web/lib/money.ts` already does this. Could we
  reuse it?"
- **Default severity:** note; important when the copy already differs from the original.

## STD-25 Generated and vendored files are not edited by hand

Files produced by a tool (generated clients, compiled output, lockfiles) change only by running the tool, and the
PR changes their source too. Vendored code is not patched in place without a note.

- **Why:** a hand edit to a generated file is lost the next time the tool runs.
- **What a finding looks like:** "I think `api/generated/client.ts` was edited by hand: the schema it comes from did
  not change. Could the change go in the schema, then regenerate?"
- **Default severity:** important.
