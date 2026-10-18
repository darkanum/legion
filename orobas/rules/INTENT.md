# Intent

Gate 3 asks one question: does this change do what was asked, all of it, and only it? The standards and the lanes
judge how the code is written; Gate 3 judges whether it is the right code.

## What "asked" means

In this order, the first that exists wins:

1. The linked issues in pr.json (`issues[]`: title and body). Their acceptance criteria, when they have them, are
   the contract.
2. The PR body: what the author says the change does and how to test it.
3. The PR title.

When the issue and the PR body disagree, the issue wins, and the gap is a finding (`INT-05`). When there is no issue
(the `no-issue` label), judge against the PR body, and keep findings to clear gaps.

## How to judge

1. List what the issue asks: each behaviour, each acceptance criterion, each case it names.
2. For each item, find the code that does it, at the head in ~/repo. Mark it done, partly done or missing.
3. List what the diff changes that no item asks for.
4. Walk the edge cases the change creates: empty, zero, one, many, missing, duplicate, too large, wrong type, no
   permission, concurrent, retried, timed out, and the state before and after a deploy.
5. Read consumers.json: for each new or changed exported name, look at the files that use it and check they still
   get what they expect.

Read the full files, not only the hunks: a change is often right in its hunk and wrong in its context.

## The rules

## INT-01 Every item the issue asks for is done

Each behaviour or acceptance criterion in the issue has code that does it.

- **What a finding looks like:** "The issue asks that a guest checkout also gets the receipt email. I think
  `checkout/complete.ts` sends it only for signed-in users. Could the guest branch call `sendReceipt` too?"
- **Default severity:** blocker when a core item is missing; important when a secondary item is missing or partial.

## INT-02 Nothing extra ships under this issue

The diff holds no behaviour the issue does not ask for: no new feature, no changed default, no side change in another
area. A small change the fix needs is fine, and the body should say why.

- **What a finding looks like:** "I think the change to the default page size from 20 to 50 is not in the issue. Could
  it go in its own PR, or could the body say why the fix needs it?"
- **Default severity:** important.

## INT-03 The edge cases of the change are handled

The new or changed behaviour holds for the inputs and states around the happy path.

- **What a finding looks like:** "I think a cart with only free items reaches `charge(0)`, which the payment client
  rejects. Could we skip the charge when the total is zero?"
- **Default severity:** blocker when a likely input breaks the flow or loses data; important otherwise.

## INT-04 Every consumer still works

The callers, importers and readers of each changed name, endpoint, event or file still get what they expect. Use
consumers.json, and search ~/repo for names it does not list.

- **What a finding looks like:** "`getOrder` now returns `null` instead of throwing, and `apps/web/orders/page.tsx`
  still reads `.items` on the result. Could that page handle the `null`?"
- **Default severity:** blocker when a consumer breaks at runtime; important when it only behaves worse.

## INT-05 The PR says what it does

The title and body describe what the diff actually does. A body that promises more, less, or something else than the
code is a finding, because the reviewer and the history will trust the body.

- **What a finding looks like:** "The body says the export now includes refunds, but I find no change to the export
  query. Is that part still to come, or could the body drop it?"
- **Default severity:** important.

## INT-06 The fix addresses the cause

For a bug, the change fixes why it happens, not only where it shows. A fix that guards one symptom while the cause
can still reach other paths is a finding.

- **What a finding looks like:** "I think the null check in the view hides the symptom: `loadProfile` still returns a
  profile with no `id` when the cache misses. Could the fix go in `loadProfile`?"
- **Default severity:** important; blocker when the same bug clearly still reaches users by another path.

## Limits

- Do not review style, naming or tests here: those belong to Gate 1 and the lanes.
- Do not ask for a different design because you would have built it another way. Ask only when the chosen design
  fails what the issue asks.
- When the issue is vague, say what you assumed in the finding instead of guessing silently.
