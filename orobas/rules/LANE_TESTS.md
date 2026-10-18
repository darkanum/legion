# Lane: tests

The behaviour is proven. Ask: if this change were wrong, would a test fail? Gate 1 already asks that changed
behaviour has a test (`STD-10`); this lane checks that the tests are good.

## TEST-01 Each new behaviour has a test that would catch its bug

A test exercises the new or fixed behaviour and would fail if the change were reverted. A bug fix has a regression
test that reproduces the bug.
**Default:** important; blocker for a bug fix with no regression test when one is easy to write.

## TEST-02 Tests assert outcomes, not just that code ran

Each test checks a result, a state or a call that matters. No test that only checks "no exception" or that a mock
was called when the result is what matters.
**Default:** important.

## TEST-03 Edge cases are covered

The cases most likely to break are tested: empty, boundary values, invalid input, missing permission, the error
path of each external call.
**Default:** important.

## TEST-04 Tests are not weakened to pass

No test is deleted, skipped, loosened (a wider tolerance, a removed assertion) or has its expected value changed to
match new output, unless the PR body says why the old expectation was wrong.
**Default:** blocker for a silent weakening that hides a regression; important otherwise.

## TEST-05 Mocks stop at the boundary

Mocks replace external systems (network, clock, payment, model APIs), not the code under test. A test that mocks the
function it claims to test proves nothing.
**Default:** important.

## TEST-06 Tests are deterministic

No real sleeps, wall-clock time, random values without a seed, shared global state, order dependence or real network
in unit tests.
**Default:** important.

## TEST-07 Snapshots are reviewed, not rubber-stamped

A large snapshot update comes with a reason, and the snapshot covers a small, meaningful piece of output.
**Default:** note; important for a bulk update with no reason.

## TEST-08 Tests read as specifications

Each test name says the behaviour it checks, and the arrange, act and assert steps are easy to see. One behaviour per
test.
**Default:** note.

## TEST-09 Tests sit at the right level

Logic is tested with fast unit tests; wiring between parts with an integration test; a main user flow with an end to
end test when the project has them. A slow test does not stand in for a missing fast one.
**Default:** note.
