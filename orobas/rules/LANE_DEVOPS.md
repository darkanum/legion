# Lane: devops

CI, images, health checks, logs and monitors. Ask whether the change builds the same way every time, deploys
without a person, and tells someone when it breaks.

## OPS-01 CI still guards the trunk

A change to the pipeline keeps the required checks running on every PR. No step is skipped, made optional or allowed
to fail without a stated reason.
**Default:** blocker for a removed required check; important otherwise.

## OPS-02 Builds are reproducible

Base images, actions, tools and dependencies are pinned to a version (or a digest), and the lockfile is updated with
the manifest.
**Default:** important.

## OPS-03 Pipelines get only the access they need

Workflow permissions and tokens are scoped to the job. Secrets reach only the steps that use them and are never
echoed. Untrusted PR code does not run with write tokens or production secrets.
**Default:** blocker for secrets or write access exposed to untrusted code; important otherwise.

## OPS-04 Images are small and safe

A container image runs as a non-root user, holds no secrets or build-only tools, and uses a maintained base.
**Default:** important.

## OPS-05 Health checks mean healthy

A service has a health or readiness check that reflects whether it can serve, and a slow start does not get it
killed in a loop.
**Default:** important.

## OPS-06 Failures are logged with context

A new important path logs its failures with enough context (ids, not personal data) to debug them, at the right
level. A noisy log line on a hot path is avoided.
**Default:** important.

## OPS-07 Someone hears when it breaks

A new service, job or critical path has the metric or alert the team uses for its kind, or the PR says how a failure
will be noticed.
**Default:** followup; important for a new production service with no signal.

## OPS-08 Deploys and rollbacks stay safe

A change to deploy scripts keeps the rollback path working, does not deploy from a PR branch to production, and
keeps environments separate.
**Default:** blocker.

## OPS-09 Pipeline time stays reasonable

A new step does not add minutes to every run without need: it is cached, runs only on the paths it concerns, or runs
in parallel.
**Default:** note.
