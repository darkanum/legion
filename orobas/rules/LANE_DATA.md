# Lane: data

Schemas, migrations, queries and indexes. Think about the data that already exists, the code that is still running
during a deploy, and the table at ten times its size.

## DATA-01 Migrations run safely on a live system

A migration does not hold a long lock on a large table: it adds columns as nullable or with a cheap default, builds
indexes without blocking writes where the database allows, and backfills in batches.
**Default:** blocker for a lock that would stall production; important otherwise.

## DATA-02 Code and schema deploy in either order

The running version keeps working after the migration, and the new version works before it finishes. A rename or a
drop is split: add, move readers and writers, then remove in a later PR.
**Default:** blocker.

## DATA-03 Migrations can be rolled back

Each migration has a down step, or the PR says why it cannot. A drop of a column or table that holds data is
backed up or confirmed unused first.
**Default:** important; blocker for an irreversible loss of data.

## DATA-04 Constraints match the rules

A field the code treats as required, unique or linked is enforced by the schema (not null, unique, foreign key,
check), or the PR says why not.
**Default:** important.

## DATA-05 Queries are indexed for how they are used

A new filter, join or sort on a table that grows has an index that serves it, and a new index is not a duplicate of
an existing one.
**Default:** important.

## DATA-06 Writes that belong together are atomic

Several writes that must all happen or none run in one transaction, or in an idempotent sequence that can be
retried. No read-modify-write race on shared counters or balances.
**Default:** blocker when a partial write corrupts data; important otherwise.

## DATA-07 Deletes are deliberate

A delete is scoped (no unbounded `DELETE` or `UPDATE` without a filter), follows the project's soft-delete rule, and
handles the rows that refer to it.
**Default:** blocker.

## DATA-08 Data fixes are reviewed like code

A backfill or one-off script is idempotent, logs what it changed, can run in batches, and was tried on a copy or with
a dry run.
**Default:** important.

## DATA-09 Types, time and money are exact

Money uses an exact type or integer minor units. Timestamps carry a time zone or are UTC by rule. Text lengths and
number ranges fit the real data.
**Default:** important.

## DATA-10 Personal data is placed and kept on purpose

A new column or table that holds personal data says why it is needed, follows the project's retention and deletion
rules, and is not copied to places that outlive it (logs, analytics, caches).
**Default:** important.
