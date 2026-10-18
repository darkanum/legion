"""Run with: python3 -m unittest test_gate0 (no dependencies)."""

import unittest
from unittest import mock

import gate0

BODY = "## Summary\nx\n## How to verify\n1. run it\nCloses #1"
PROMPT = "<!-- Write this first. -->"


def meta(**extra):
    base = {"title": "feat(api): add x", "body": BODY, "base": "main", "head": "feat/x", "linkedIssues": [1],
            "mergeable": True, "commits": []}
    return {**base, **extra}


def patch(*lines):
    return "@@ -1,0 +1,%d @@\n" % len(lines) + "\n".join("+" + line for line in lines)


def rules(found):
    return sorted({f["rule"] for f in found})


def meta_rules(**extra):
    return rules(gate0.check_meta(meta(**extra), set(), {PROMPT}))


class MetaTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(gate0.config, "BASE_BRANCH", "main")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_clean_pr_has_no_finding(self):
        self.assertEqual(gate0.check_meta(meta(), set(), {PROMPT}), [])

    def test_no_linked_issue_blocks(self):
        found = gate0.check_meta(meta(linkedIssues=[]), set(), {PROMPT})
        self.assertEqual([(f["rule"], f["severity"]) for f in found], [("STD-01", "blocker")])

    def test_the_no_issue_label_skips_the_issue_rule(self):
        self.assertEqual(meta_rules(linkedIssues=[], labels=["docs", "no-issue"]), [])

    def test_the_issue_rule_can_be_turned_off(self):
        with mock.patch.object(gate0, "REQUIRE_ISSUE", False):
            self.assertEqual(meta_rules(linkedIssues=[]), [])

    def test_the_title_takes_the_branch_type(self):
        self.assertEqual(meta_rules(title="feat: add x"), [])
        self.assertEqual(meta_rules(title="fix(api): add x"), ["STD-02"])
        self.assertEqual(meta_rules(title="add x"), ["STD-02"])
        found = gate0.check_meta(meta(head="ana/x"), set(), {PROMPT})
        self.assertEqual([(f["rule"], f["severity"]) for f in found], [("STD-02", "note")])

    def test_a_scope_can_be_required(self):
        with mock.patch.object(gate0, "NEEDS_SCOPE", True):
            self.assertEqual(meta_rules(title="feat: add x"), ["STD-02"])
            self.assertEqual(meta_rules(title="feat(api): add x"), [])

    def test_the_base(self):
        self.assertEqual(meta_rules(base="dev"), ["STD-03"])
        with mock.patch.object(gate0, "EXTRA_BASES", ("prototypes",)):
            self.assertEqual(meta_rules(base="prototypes"), [])
        stacked = meta(base="feat/a", body=BODY + "\nStacked on #2")
        self.assertEqual(gate0.check_meta(stacked, {"feat/a"}, {PROMPT}), [])
        self.assertEqual(rules(gate0.check_meta(stacked, set(), {PROMPT})), ["STD-03"])

    def test_conflicts_block_and_an_unknown_mergeable_does_not(self):
        self.assertEqual(meta_rules(mergeable=False), ["STD-03"])
        self.assertEqual(meta_rules(mergeable=None), [])

    def test_a_merge_of_a_long_lived_branch_blocks_only_when_named(self):
        commits = [{"message": "Merge remote-tracking branch 'origin/dev' into feat/x"}]
        self.assertEqual(meta_rules(commits=commits), [])
        with mock.patch.object(gate0, "FORBIDDEN_MERGES", ("dev",)):
            self.assertEqual(meta_rules(commits=commits), ["STD-03"])
            self.assertEqual(meta_rules(commits=[{"message": "Merge branch 'main' into feat/x"}]), [])

    def test_an_unfilled_template(self):
        self.assertEqual(meta_rules(body=BODY + "\n" + PROMPT), ["STD-04"])
        self.assertEqual(meta_rules(body=BODY + "\n2. "), ["STD-04"])
        self.assertEqual(rules(gate0.check_meta(meta(body=BODY + "\n2. "), set(), set())), [])

    def test_attribution_in_the_body_or_a_commit(self):
        self.assertEqual(meta_rules(body=BODY + "\n🤖 Generated with Claude Code"), ["STD-05"])
        self.assertEqual(meta_rules(commits=[{"message": "x\n\nCo-Authored-By: Claude <a@b>"}]), ["STD-05"])
        with mock.patch.dict("os.environ", {"OROBAS_FORBID_ATTRIBUTION": "false"}):
            self.assertEqual(meta_rules(body=BODY + "\nCo-Authored-By: Claude <a@b>"), [])

    def test_the_template_prompts_are_its_one_line_comments(self):
        template = "## Summary\n<!-- Write this first. -->\n<!--\nlong\n-->\n"
        self.assertEqual(gate0.template_prompts(template), {PROMPT})


class FileTest(unittest.TestCase):
    def test_a_committed_env_file_blocks_but_a_template_does_not(self):
        for path in (".env", "api/.env.production", "deploy/prod.env"):
            found = gate0.check_file({"filename": path, "status": "added", "patch": patch("A=1")})
            self.assertEqual(rules(found), ["STD-06"], path)
        for path in (".env.example", "api/.env.sample", "api/.env.template"):
            self.assertEqual(gate0.check_file({"filename": path, "status": "added", "patch": patch("A=")}), [], path)

    def test_a_conflict_marker_blocks_on_its_line(self):
        found = gate0.check_file({"filename": "a.go", "status": "modified",
                                  "patch": patch("x := 1", "<<<<<<< HEAD", "y", "=======", "z", ">>>>>>> main")})
        self.assertEqual([(f["rule"], f["line"]) for f in found], [("STD-07", 2), ("STD-07", 4), ("STD-07", 6)])

    def test_a_markdown_underline_is_not_a_conflict_marker(self):
        for lines in (("Title", "======="), ("========", "=== x")):
            self.assertEqual(gate0.check_file({"filename": "a.md", "status": "modified", "patch": patch(*lines)}), [], lines)


class CheckTest(unittest.TestCase):
    def test_the_result_splits_lines_from_process_and_counts_blockers(self):
        with mock.patch.object(gate0.config, "BASE_BRANCH", "main"):
            result = gate0.check(meta(linkedIssues=[]), [{"filename": ".env", "status": "added", "patch": patch("A=1")}],
                                 set(), {PROMPT})
        self.assertEqual((len(result["comments"]), len(result["process"]), result["blockers"]), (1, 1, 2))


if __name__ == "__main__":
    unittest.main()
