"""Run with: python3 -m unittest test_verdict (no dependencies)."""

import unittest
from unittest import mock

import verdict

PATCH = "@@ -10,3 +10,4 @@ def f():\n a\n-b\n+c\n+d\n e"


class LinesTest(unittest.TestCase):
    def test_commentable_lines_are_the_right_side_of_the_hunk(self):
        self.assertEqual(verdict.commentable_lines([{"filename": "x.py", "patch": PATCH}]), {"x.py": {10, 11, 12, 13}})

    def test_a_file_with_no_patch_takes_no_comment(self):
        self.assertEqual(verdict.commentable_lines([{"filename": "img.png"}]), {"img.png": set()})


class CleanTest(unittest.TestCase):
    def test_clean_drops_attribution_dashes_and_mentions(self):
        text = "Nice work \u2014 thanks @ana\nGenerated with Claude Code"
        self.assertEqual(verdict.clean(text), "Nice work, thanks @\u200bana")


class SplitTest(unittest.TestCase):
    def test_a_comment_off_the_diff_moves_to_the_body(self):
        review = {"comments": [{"path": "x.py", "line": 11, "severity": "note", "body": "Nit: ok", "rule": "STD-12"},
                               {"path": "x.py", "line": 99, "severity": "blocker", "body": "I think this blocks."}],
                  "process": [{"severity": "blocker", "text": "No `Closes #N` in the body.", "rule": "STD-01"}]}
        inline, extra, blockers = verdict.split_comments(review, {"x.py": {10, 11}})
        self.assertEqual(inline, [{"path": "x.py", "line": 11, "side": "RIGHT", "body": "**Note** · `STD-12`: Nit: ok"}])
        self.assertEqual(extra, ["- `x.py:99`: **Blocker**: I think this blocks.",
                                 "- **Blocker** · `STD-01`: No `Closes #N` in the body."])
        self.assertEqual(blockers, 2)

    def test_a_rule_id_that_is_not_one_is_dropped(self):
        review = {"comments": [{"path": "x.py", "line": 10, "severity": "note", "body": "b", "rule": "<!-- x -->"}]}
        inline, _, _ = verdict.split_comments(review, {"x.py": {10}})
        self.assertEqual(inline[0]["body"], "**Note**: b")

    def test_comments_are_capped(self):
        review = {"comments": [{"path": "x.py", "line": 10, "body": "n"}] * 50}
        inline, _, _ = verdict.split_comments(review, {"x.py": {10}})
        self.assertEqual(len(inline), verdict.MAX_COMMENTS)


class DecideTest(unittest.TestCase):
    def test_approve_needs_every_gate(self):
        self.assertEqual(verdict.decide(0, "green", [], False, True, can_block=True), ("APPROVE", ""))

    def test_a_blocker_requests_changes(self):
        self.assertEqual(verdict.decide(1, "green", [], False, True, can_block=True)[0], "REQUEST_CHANGES")

    def test_with_blocking_off_a_blocker_only_comments(self):
        event, note = verdict.decide(2, "green", [], False, True, can_block=False)
        self.assertEqual(event, "COMMENT")
        self.assertIn("2 Blocker(s)", note)

    def test_a_missing_issue_blocks_even_with_blocking_off(self):
        review = {"process": [{"rule": "STD-01", "severity": "blocker"}]}
        self.assertTrue(verdict.always_blocks(review))
        self.assertFalse(verdict.always_blocks({"process": [{"rule": "STD-04", "severity": "blocker"}]}))
        event, note = verdict.decide(1, "green", [], False, True, can_block=False, must_block=["STD-01"])
        self.assertEqual(event, "REQUEST_CHANGES")
        self.assertIn("Closes #N", note)
        self.assertEqual(verdict.decide(1, "green", [], True, True, can_block=False, must_block=["STD-01"])[0], "COMMENT")

    def test_a_team_can_add_its_own_always_blocking_rule(self):
        review = {"process": [{"rule": "STD-42", "severity": "blocker"}, {"rule": "STD-01", "severity": "blocker"}]}
        with mock.patch.object(verdict, "ALWAYS_BLOCK", {"STD-01", "STD-42"}):
            self.assertEqual(verdict.always_blocks(review), ["STD-01", "STD-42"])
        event, note = verdict.decide(2, "green", [], False, True, can_block=False, must_block=["STD-42"])
        self.assertEqual(event, "REQUEST_CHANGES")
        self.assertIn("`STD-42` always blocks", note)
        self.assertNotIn("Closes #N", note)

    def test_a_stacked_pr_waits_for_the_base_branch(self):
        with mock.patch.object(verdict.config, "BASE_BRANCH", "main"):
            event, note = verdict.decide(0, "green", [], False, True, can_block=True, stacked=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("`main`", note)

    def test_red_ci_never_approves(self):
        self.assertEqual(verdict.decide(0, "red", [], False, True, can_block=True)[0], "COMMENT")

    def test_a_guarded_path_never_approves(self):
        event, note = verdict.decide(0, "green", [".github/workflows/ci.yml"], False, True, can_block=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn(verdict.ENGINEERING, note)

    def test_a_bot_pr_only_gets_comments(self):
        self.assertEqual(verdict.decide(2, "green", [], True, True, can_block=True)[0], "COMMENT")
        self.assertEqual(verdict.decide(0, "green", [], True, True, can_block=True)[0], "COMMENT")

    def test_with_approval_off_the_bot_asks_engineering(self):
        event, note = verdict.decide(0, "green", [], False, False, can_block=True)
        self.assertEqual(event, "COMMENT")
        self.assertTrue(note.endswith("could one of you approve?"))

    def test_the_body_is_signed_keeps_the_team_mention_and_links_the_report(self):
        text = verdict.body_text("Nice.", [], "@acme/engineering, could one of you approve?", "https://r")
        self.assertTrue(text.startswith("Nice."))
        self.assertIn("@acme/engineering", text)
        self.assertIn("Full report: https://r", text)
        self.assertTrue(text.endswith(verdict.SIGNATURE))

    def test_an_unfinished_ladder_never_approves(self):
        event, note = verdict.decide(0, "green", [], False, True, can_block=True, incomplete="lane-tests")
        self.assertEqual(event, "COMMENT")
        self.assertIn("lane-tests", note)

    def test_pending_ci_comments_and_says_it_looks_again(self):
        event, note = verdict.decide(0, "pending", [], False, True, can_block=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("look again", note)

    def test_a_stacked_pr_is_not_approved(self):
        self.assertEqual(verdict.decide(0, "green", [], False, True, can_block=True, stacked=True)[0], "COMMENT")


def ladder():
    return {"gate0": {"comments": [{"rule": "STD-26", "severity": "blocker", "path": "a.ts", "line": 3, "body": "x"}],
                      "process": []},
            "gate1": {"verdict": "pass", "comments": [{"rule": "STD-25", "severity": "note", "path": "a.ts", "line": 3,
                                                        "body": "y"}], "process": [], "resolved": [11]},
            "lanes": {"security": {"comments": [{"rule": "SEC-01", "severity": "important", "path": "b.ts", "line": 1,
                                                  "body": "z"}], "resolved": ["12", "bad"]}},
            "gate3": {"verdict": "fail", "comments": [], "process": [{"rule": "INT-01", "severity": "important", "text": "t"}]}}


class OpenThreadsTest(unittest.TestCase):
    def thread(self, comment_id, login="legion-bot", resolved=False):
        return {"isResolved": resolved, "path": "a.ts", "line": 1,
                "comments": {"nodes": [{"databaseId": comment_id, "author": {"login": login}}]}}

    def test_an_open_bot_thread_counts_until_a_gate_lists_it_as_fixed(self):
        threads = [self.thread(1), self.thread(2), self.thread(3, resolved=True), self.thread(4, login="eng")]
        self.assertEqual(verdict.open_threads(threads, "legion-bot", []), 2)
        self.assertEqual(verdict.open_threads(threads, "legion-bot", [1]), 1)

    def test_an_open_thread_never_approves(self):
        event, note = verdict.decide(0, "green", [], False, True, can_block=True, still_open=1)
        self.assertEqual(event, "COMMENT")
        self.assertIn("still open", note)


class CollectTest(unittest.TestCase):
    def test_collect_tags_every_source_and_unions_resolved(self):
        review = verdict.collect(ladder())
        self.assertEqual([c["source"] for c in review["comments"]], ["gate0", "gate1", "lane-security"])
        self.assertEqual(review["process"][0]["source"], "gate3")
        self.assertEqual(review["resolved"], [11, 12])

    def test_dedupe_keeps_the_most_severe_and_skips_open_bot_threads(self):
        review = verdict.collect(ladder())
        threads = [{"isResolved": False, "path": "b.ts", "line": 1,
                    "comments": {"nodes": [{"author": {"login": "legion-bot"}}]}}]
        kept = verdict.dedupe(review["comments"], threads, "legion-bot")
        self.assertEqual([(c["path"], c["severity"]) for c in kept], [("a.ts", "blocker")])

    def test_a_failed_gate_counts_as_a_blocker(self):
        review = {"comments": [], "process": []}
        self.assertEqual(verdict.blocker_count(review, ["pass", "fail"]), 1)
        self.assertEqual(verdict.blocker_count(verdict.collect(ladder()), ["pass"]), 1)

    def test_blockers_survive_the_cap(self):
        notes = [{"path": "x.py", "line": 10, "severity": "note", "body": "n"}] * 40
        review = {"comments": notes + [{"path": "x.py", "line": 10, "severity": "blocker", "body": "b"}]}
        _, _, blockers = verdict.split_comments(review, {"x.py": {10}})
        self.assertEqual(blockers, 1)

    def test_clean_removes_html_comments(self):
        self.assertEqual(verdict.clean("a <!-- orobas-report:v1 x --> b"), "a  orobas-report:v1 x  b")


if __name__ == "__main__":
    unittest.main()
