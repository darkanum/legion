# Standards

An example of a repository's own standards for Orobas. Copied to `.legion/rules/STANDARDS.md` on the base branch, it
takes the place of the starter `orobas/rules/STANDARDS.md` as a whole: a rule that is not here is not read.

So it keeps the headings of `STD-01` to `STD-07`, which Gate 0 checks on the host whatever this file says (the settings
in `legion.env` tune them), keeps the starter rules this team wants, and adds its own from `STD-90` on, so a new
starter rule never takes the same id.

---

## Rules Gate 0 checks on the host

## STD-01 The PR links an issue
Checked on the host. `Closes #N`, or the `no-issue` label. **Default severity:** blocker.

## STD-02 The title is conventional and matches the branch
Checked on the host. **Default severity:** important.

## STD-03 The PR targets the right base and merges cleanly
Checked on the host. **Default severity:** blocker.

## STD-04 The PR template is filled in
Checked on the host. **Default severity:** important.

## STD-05 No AI attribution lines
Checked on the host. **Default severity:** important.

## STD-06 No committed env or secret files
Checked on the host. **Default severity:** blocker.

## STD-07 No merge conflict markers
Checked on the host. **Default severity:** blocker.

---

## Rules Gate 1 reads with judgement

## STD-10 Changed behaviour has a test

Kept from the starter rulebook, as written there. A team copies each starter rule it wants to keep; here, one.

- **Why:** a change with no test is a claim nobody checked.
- **What a finding looks like:** "I think this changes what `applyDiscount` returns for an empty cart, and no test
  covers it. Could we add one?"
- **Default severity:** important.

## STD-90 Money is an integer number of cents

Prices, totals and discounts are integers in the smallest unit of the currency, never floats. Formatting to a string
happens only at the edge, for display.

- **Why:** `0.1 + 0.2` is not `0.3` in floating point; a rounding drift in a total is a bug a customer sees.
- **What a finding looks like:** "I think `total` holds a float here. Could it be an integer number of cents, as the
  rest of `cart/` does?"
- **Default severity:** blocker.

## STD-91 Every public endpoint has an entry in the API changelog

A PR that adds, removes or changes a public endpoint adds a line to `docs/api-changelog.md`.

- **Why:** the mobile app ships on its own schedule and reads that file to know what changed.
- **What a finding looks like:** "This adds `POST /carts/{id}/coupons`, and `docs/api-changelog.md` has no line for
  it."
- **Default severity:** important.
