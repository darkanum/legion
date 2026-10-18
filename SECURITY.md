# Security policy

Legion runs models that read untrusted text and act on a repository, so we take security reports seriously and
answer them first.

## Reporting a vulnerability

Please report a vulnerability privately, through GitHub's security advisories:

1. Open the **Security** tab of this repository.
2. Choose **Report a vulnerability**.
3. Describe the problem, the version or commit, how to reproduce it, and what an attacker gains.

Do not open a public issue, pull request or discussion for a vulnerability.

We aim to acknowledge a report within three working days, and to agree on a fix and a disclosure date with you. We
credit reporters in the advisory unless they ask us not to.

Fixes land on `main`. There are no maintained release branches; a host updates with `git pull && ./setup.sh`.

## What is in scope

- A way for untrusted input (an issue, a PR diff or body, a review comment, CI output, a log line, telemetry) to make
  an agent push, merge, approve, post, or reach a host outside the allowlist, beyond what its settings allow.
- A way for a sandbox to read or use the host's write token, the webhook secret, or another secret it was not given.
- A way around the guarded paths, the patch scope check, the boot check or the managed settings.
- A way to forge or replay a webhook delivery into an agent's inbox.
- A mention, link or marker that model text can inject into GitHub or Slack despite the host's cleaning.

Out of scope: weaknesses of GitHub, Slack, Anthropic, Axiom or PostHog themselves; a host where the operator gave a
sandbox more than the documented tokens; denial of service by a person who already has write access to the
repository.

## The threat model in short

**Assets.** The bot account's write token (`github_host_token`), which may bypass branch protection; the Claude
token; the telemetry tokens; the webhook secret; and the integrity of the repository's base branch.

**Adversary.** Anyone who can write text an agent reads: a bug reporter, a PR author, a commenter, a user whose
actions reach the production logs. We assume such text can steer a model completely.

**Boundaries and controls.**

- **Host and sandbox.** Every model runs in a container, as a non-root user, with all capabilities dropped,
  `no-new-privileges`, and memory, CPU, process and wall-clock limits. The host process runs no model.
- **Tokens.** The write token never enters a container. The sandbox gets a read-only GitHub token (none at all for
  Orobas), query-only telemetry tokens, and the Claude token, through an env file deleted at start. The clone's
  remote carries no token after the clone.
- **Network.** The sandbox network has no route out. A proxy lets through only an allowlist of hosts.
- **Tools.** Claude Code's managed settings deny push, remote and config changes, `gh pr`, `gh api`, `curl`, `wget`,
  `ssh`, `env`, `printenv`, web access, and edits of env files and guarded paths. Anything not allowed is denied.
- **Boot check.** A run refuses to start when its environment matches `LEGION_BOOT_DENY`.
- **Output checks on the host.** A patch may change only the files of the plan an engineer approved, and never a
  guarded path or an env file; it is applied with git hooks off. A review state is computed by code. Model text has
  its mentions, attribution lines and HTML comments removed before it is posted.
- **Rules from outside the change.** Skills come from the Legion copy on the host; Orobas's rules come from the base
  branch; the Slack user map comes from the base branch under a guarded path.
- **Webhooks.** Every delivery is checked against an HMAC secret, capped at 1 MB, and reduced to six typed keys. The
  payload text is never stored.
- **People.** An engineer approves every plan and every wider scope. By default a person merges every PR, Valac opens
  issues only, and Orobas only comments.

**Known limits.** A model can still write a wrong fix inside an approved plan; CI and the human review are the
control for that. A sandbox can reach every host on the allowlist, including the package registries. The shared
Claude seat can be spent by any agent; Orobas's budget limits only Orobas.
