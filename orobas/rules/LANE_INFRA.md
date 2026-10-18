# Lane: infra

Infrastructure as code, names and cost. Ask what the plan would create, change or destroy, who can then reach it,
and what it costs each month.

## INFRA-01 Nothing is destroyed by surprise

A change that replaces or deletes a stateful resource (a database, a disk, a bucket, a queue) is intended, said in
the PR body, and protected where the tool allows (deletion protection, prevent-destroy).
**Default:** blocker.

## INFRA-02 Access is least privilege

Roles and policies grant only the actions and resources the workload needs. No wildcard actions or resources, no
admin roles for services, no long-lived keys where an identity-based login exists.
**Default:** blocker for broad admin or public access; important otherwise.

## INFRA-03 Nothing is public by accident

Buckets, databases, ports and endpoints are private unless public on purpose. Network rules allow only the sources
that need them, never the whole internet to an admin port.
**Default:** blocker.

## INFRA-04 State and secrets are handled by the tool

Infrastructure state lives in the shared remote backend with locking. Secrets come from the secret store, not from
variables in code or state outputs.
**Default:** blocker for a secret in code; important otherwise.

## INFRA-05 Names, tags and regions follow the convention

New resources follow the project's naming and tagging rules (owner, environment, cost centre) and live in the agreed
regions.
**Default:** note.

## INFRA-06 Cost is stated

A change that adds or sizes up resources says the rough monthly cost in the PR body. Autoscaling has a maximum.
**Default:** important for a large or unbounded cost; note otherwise.

## INFRA-07 Environments stay separate and alike

A change applies to each environment through its own variables, not by copying blocks, and production does not
depend on a staging resource.
**Default:** important.

## INFRA-08 Versions are pinned

Providers, modules and charts are pinned to a version range the project allows.
**Default:** important.

## INFRA-09 Data is protected

Stateful resources have encryption at rest, backups with a stated retention, and, for production, a tested restore
path.
**Default:** important.
