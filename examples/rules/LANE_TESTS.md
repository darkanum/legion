# Lane: tests

An example of a lane file a repository tightens. Copied to `.legion/rules/LANE_TESTS.md`, it replaces the starter
lane as a whole: this team keeps two starter rules, makes one stricter, and adds one of its own.

## TEST-01 Each new behaviour has a test that would catch its bug

A test exercises the new or fixed behaviour and would fail if the change were reverted. A bug fix has a regression
test that reproduces the bug.
**Default:** blocker. This team blocks on it, where the starter rule says important.

## TEST-06 Tests are deterministic

No test depends on the clock, the network, the order of a map or a random seed it does not set.
**Default:** important.

## TEST-90 Payment flows run against the provider's sandbox in CI

A change under `payments/` keeps the contract tests in `payments/contract/` green against the provider's sandbox.
**Default:** blocker.
