# Lane: product

What was asked, and what the user gets. Read the change as the person who uses it: what do they see, what can they
do, and what happens when it goes wrong? Gate 3 checks the issue item by item; this lane checks the experience.

## PROD-01 The user can finish the flow

The changed flow works end to end for the people it is for, including the first time (no data yet) and the return
visit (data from before the change).
**Default:** blocker when a main flow breaks; important otherwise.

## PROD-02 Every state has something to show

Loading, empty, error, partial and success states each have a clear screen, message or response. No blank page, no
spinner that never ends.
**Default:** important.

## PROD-03 Errors tell the user what to do

A message a user sees says what happened and what to do next, in their words. No stack traces, codes alone or
internal names.
**Default:** important.

## PROD-04 Copy is clear and consistent

Text the user reads is short, correct and uses the product's existing terms for the same things. Text that the
project translates goes through its translation system.
**Default:** note; important when copy is wrong or misleading.

## PROD-05 Defaults are safe and deliberate

A new or changed default (a setting, a limit, a notification, an opt-in) is the one the issue asks for, and existing
users are not silently moved to it unless that is the point.
**Default:** important.

## PROD-06 Existing users are not surprised

A change in behaviour for people who already use the feature is announced, gated or migrated as the issue says. Saved
data, links and bookmarks keep working.
**Default:** important; blocker when existing user data or access is lost.

## PROD-07 Gated work is gated

A feature behind a flag is off for everyone outside the rollout, in every path that shows it (UI, API, emails,
jobs).
**Default:** blocker when an unfinished feature leaks to all users.

## PROD-08 The change can be measured

When the issue names a goal, the events or metrics that tell whether the change reached it exist, without personal
data.
**Default:** followup; important when the issue asks for the measurement.
