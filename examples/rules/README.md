# A repository's own rulebook

Orobas reviews with the starter rulebook in `orobas/rules/` until a repository brings its own. This folder is an
example of one, for a shop that keeps prices in cents.

## How the override works

`orobas/sandbox/review.sh` builds one rulebook per review, file by file:

1. It starts from the starter files of this Legion copy (`orobas/rules/`).
2. Every file in `LEGION_RULES_DIR` (default `.legion/rules/`) on the repository's **base branch** takes the place of
   the starter file with the same name, as a whole.

So a file you add replaces the starter one completely, and a file you do not add stays the starter one. Here,
`STANDARDS.md` and `LANE_TESTS.md` are replaced, and every other lane, `TRIAGE.md`, `INTENT.md` and `VOICE.md` stay
the starter files. The rules come from the base branch, never from the PR under review: a PR cannot change the rules
it is reviewed by, so a new rule takes effect once it is merged.

## Rules to keep

- Keep the headings `## STD-01` to `## STD-07` in `STANDARDS.md`. Gate 0 checks them on the host with plain code,
  whatever the file says; the models read the headings to know not to report them again. Tune them in `legion.env`
  (`OROBAS_REQUIRE_ISSUE`, `OROBAS_BRANCH_TYPES`, `OROBAS_TITLE_NEEDS_SCOPE`, `OROBAS_EXTRA_BASES`,
  `OROBAS_FORBIDDEN_MERGES`, `OROBAS_FORBID_ATTRIBUTION`).
- Give your own rules ids from 90 on (`STD-90`, `TEST-90`), so a new starter rule never takes the same id.
- Give every rule a why, what a finding looks like, and a default severity: blocker, important, note or followup
  (`orobas/rules/README.md`).
- Write findings the way `VOICE.md` says, or replace `VOICE.md` too.

## Try it

1. Copy these files to `.legion/rules/` in your repository and merge them to the base branch.
2. On the host, dry-run a PR that breaks one of your rules (docs/OROBAS.md, "Try it without posting"):

   ```sh
   set -a; source /opt/legion/legion.env; set +a
   /opt/legion/venv/bin/python /opt/legion/repo/orobas/host/reviewer.py --dry-run 12
   ```

3. Read the printed comments: a finding on your rule carries its id, as `**Blocker** · \`STD-90\``.
