"""Run with: python3 -m unittest test_pr (no dependencies). The shared PR flow is tested in shared/lib/test_pull_request.py."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pr
import pull_request

PLAN = {"files": ["apps/web/src/a.tsx", "apps/web/src/a.test.tsx (new)"]}
PATCH = """diff --git a/apps/web/src/a.tsx b/apps/web/src/a.tsx
--- a/apps/web/src/a.tsx
diff --git a/apps/web/src/a.test.tsx b/apps/web/src/a.test.tsx
new file mode 100644
"""


class CiFixTest(unittest.TestCase):
    PR = {"number": 7, "merged": False, "state": "open", "mergeable": True, "head": {"sha": "head", "ref": "fix/issue-7-x"}}
    JOB = {"name": "Testing (backend/api)", "url": "u", "run": 1, "id": 2, "log": "✖ a test"}

    def step(self, thread, review=("waiting", "")):
        with mock.patch.object(pr, "github", return_value=dict(self.PR)), \
             mock.patch.object(pr, "checks_state", return_value="red"), \
             mock.patch.object(pr, "failed_jobs", return_value=[self.JOB]), \
             mock.patch.object(pr, "review_state", return_value=review):
            return pr.step(thread)

    def test_red_ci_starts_a_fix_even_before_an_approval(self):
        thread = {"stage": "pr_open", "pr": 7}
        moved = self.step(thread)
        self.assertEqual(moved[0], "ci_fixing")
        self.assertIn("`Testing (backend/api)`", moved[1])
        self.assertEqual((thread["ci_attempts"], thread["branch"], thread["ci_jobs"]), (1, "fix/issue-7-x", [self.JOB]))

    def test_after_two_fixes_a_human_takes_it(self):
        moved = self.step({"stage": "pr_open", "pr": 7, "ci_attempts": pr.CI_FIX_ATTEMPTS})
        self.assertEqual(moved[0], "needs_human")
        self.assertIn("after 2 fixes", moved[1])

    def test_a_ci_fix_outside_the_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "fix.patch").write_text("diff --git a/other/file.ts b/other/file.ts\n")
            self.assertIn("does not list", pr.push_fix("fix/issue-7-x", run_dir, PLAN, "fix: x")["error"])

    def test_a_ci_fix_never_pushes_to_another_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "fix.patch").write_text(PATCH)
            self.assertIn("not a Marbas branch", pr.push_fix("production", run_dir, PLAN, "fix: x")["error"])


class PrTextTest(unittest.TestCase):
    RESULT = {"score": 82, "label": "High", "prTitle": "fix: show every Add Submissions option at high zoom",
              "prBody": "## 📝 TL;DR\n\nThe menu was cut off. @team see .claude/skills/x.md\n\n🤖 Generated with [Claude Code](x)\nCo-Authored-By: Claude <a@b>"}

    def test_the_agent_text_is_used_with_the_bot_line_and_closes(self):
        title, body = pr.pr_text("4025", "🐞 [BUG] Dropdown", self.RESULT, {})
        self.assertEqual(title, "fix: show every Add Submissions option at high zoom")
        self.assertIn("The menu was cut off.", body)
        self.assertIn("Closes #4025", body)
        self.assertIn("Marbas opened this PR", body)

    def test_attribution_lines_go_but_a_claude_path_stays(self):
        _, body = pr.pr_text("1", "t", self.RESULT, {})
        self.assertNotIn("Generated with", body)
        self.assertNotIn("Co-Authored-By", body)
        self.assertIn(".claude/skills/x.md", body)

    def test_a_mention_in_the_agent_text_cannot_ping(self):
        _, body = pr.pr_text("1", "t", self.RESULT, {})
        self.assertNotIn("@team", body)

    def test_without_agent_text_the_evidence_body_is_used(self):
        title, body = pr.pr_text("7", "Grade wrong", {"score": 50, "evidence": ["a query"]}, {"fix": "round"})
        self.assertEqual(title, "fix: Grade wrong")
        self.assertIn("## Evidence", body)


class OpenPrTest(unittest.TestCase):
    def open_pr(self, refuse_team=False):
        """Opens PR 9 for issue 7 against a fake GitHub. Returns (result, every call, the axiom.emit mock)."""
        calls = []

        def github(path, *args, method="GET", body=None, **kwargs):
            calls.append((path, method, body))
            if path.startswith("repos/owner/repo/issues/") and method == "GET":
                return {"title": "🐞 [BUG] Grade wrong"}
            if path == "user":
                return {"login": "legion-bot", "id": 1}
            if path.endswith("/pulls") and method == "POST":
                return {"number": 9, "html_url": "u"}
            if refuse_team and path.endswith("/requested_reviewers") and "team_reviewers" in (body or {}):
                raise OSError("422")
            return []

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "fix.patch").write_text(PATCH)
            (run_dir / "base.sha").write_text("a" * 40)
            with mock.patch.object(pr, "github", side_effect=github), \
                 mock.patch.object(pull_request, "github", side_effect=github), \
                 mock.patch.object(pull_request, "git"), mock.patch.object(pull_request, "git_auth", return_value="h"), \
                 mock.patch.object(pull_request.axiom, "emit") as emit, \
                 mock.patch.object(pull_request, "staged_paths", return_value=pull_request.patch_paths(PATCH)), \
                 mock.patch.object(pull_request, "me", return_value={"login": "legion-bot", "id": 1}), \
                 mock.patch.object(pull_request, "PR_TEAM_REVIEWERS", ["engineering"]):
                return pr.open_pr("7", run_dir, {"score": 80}, PLAN), calls, emit

    def test_the_pr_uses_the_marbas_branch_and_title(self):
        result, calls, _ = self.open_pr()
        self.assertEqual(result, {"pr": 9, "url": "u"})
        opened = next(body for path, method, body in calls if path.endswith("/pulls") and method == "POST")
        self.assertEqual((opened["head"], opened["title"]), ("fix/issue-7-grade-wrong", "fix: Grade wrong"))
        self.assertIn("Closes #7.", opened["body"])

    def test_a_refused_team_review_logs_under_marbas(self):
        result, _, emit = self.open_pr(refuse_team=True)
        self.assertEqual(result, {"pr": 9, "url": "u"})
        self.assertEqual(emit.call_args.args[1], "marbas.pr.reviewers_failed")

    def test_a_patch_outside_the_plan_opens_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "fix.patch").write_text("diff --git a/other/file.ts b/other/file.ts\n")
            (run_dir / "base.sha").write_text("a" * 40)
            self.assertIn("does not list", pr.open_pr("7", run_dir, {}, PLAN)["error"])


class MergeSwitchTest(unittest.TestCase):
    PR = {"number": 7, "merged": False, "state": "open", "mergeable": True, "head": {"sha": "head"}}

    def step(self, auto_merge, thread=None):
        thread = thread or {"stage": "pr_open", "pr": 7}
        calls = []

        def github(path, *args, **kwargs):
            calls.append((path, kwargs.get("method", "GET")))
            return dict(self.PR) if path.endswith("pulls/7") else {"sha": "merged"}

        with mock.patch.object(pr, "AUTO_MERGE", auto_merge), mock.patch.object(pr, "github", side_effect=github), \
             mock.patch.object(pr, "review_state", return_value=("approved", "eng")), \
             mock.patch.object(pr, "checks_state", return_value="green"):
            return pr.step(thread), calls, thread

    def test_with_merge_off_an_approved_green_pr_is_not_merged(self):
        moved, calls, _ = self.step(False)
        self.assertEqual(moved[0], "pr_open")
        self.assertIn("An engineer merges it", moved[1])
        self.assertNotIn("PUT", [method for _, method in calls])

    def test_an_approval_asks_for_the_approved_reaction_on_the_pr_message(self):
        _, _, thread = self.step(False)
        self.assertEqual(thread["react"], "heavy_check_mark")

    def test_changes_requested_asks_for_the_rejected_reaction(self):
        reviews = [{"id": 5, "state": "CHANGES_REQUESTED", "user": {"login": "eng"}, "body": "no"}]

        def github(path, *args, **kwargs):
            return reviews if path.endswith("/reviews?per_page=100") else dict(self.PR)

        thread = {"stage": "pr_open", "pr": 7}
        with mock.patch.object(pr, "ADDRESS_REVIEW", False), mock.patch.object(pr, "github", side_effect=github), \
             mock.patch.object(pr, "checks_state", return_value="green"), \
             mock.patch.object(pr, "review_state", return_value=("changes", "eng")):
            moved = pr.step(thread)
        self.assertEqual((moved[0], thread["react"]), ("needs_human", "x"))

    def test_with_merge_off_the_ready_message_is_posted_once(self):
        _, _, thread = self.step(False)
        moved, _, _ = self.step(False, thread)
        self.assertIsNone(moved)

    def test_with_merge_on_an_approved_green_pr_is_merged(self):
        moved, calls, thread = self.step(True)
        self.assertEqual(moved[0], "deploying")
        self.assertIn(("repos/owner/repo/pulls/7/merge", "PUT"), calls)
        self.assertEqual(thread["merge_sha"], "merged")

    def test_a_merge_by_a_person_starts_the_deploy_watch(self):
        merged = dict(self.PR, merged=True, merge_commit_sha="m1", merged_by={"login": "max"})
        with mock.patch.object(pr, "github", return_value=merged):
            thread = {"stage": "pr_open", "pr": 7}
            moved = pr.step(thread)
        self.assertEqual(moved[0], "deploying")
        self.assertIn("`max` merged", moved[1])
        self.assertEqual(thread["merge_sha"], "m1")


class DeployWatchTest(unittest.TestCase):
    def step(self, environment, runs, deployments=(), status="success"):
        def github(path, *args, **kwargs):
            if "/actions/runs?" in path:
                return {"workflow_runs": list(runs)}
            if "/statuses" in path:
                return [{"state": status}]
            if "/deployments?" in path:
                return list(deployments)
            raise AssertionError(path)

        thread = {"stage": "deploying", "pr": 7, "merge_sha": "m1", "merged_at": 0}
        with mock.patch.object(pr, "DEPLOY_ENVIRONMENT", environment), mock.patch.object(pr, "github", side_effect=github):
            return pr.step(thread)

    def test_a_green_production_deployment_is_live(self):
        moved = self.step("production", [], [{"id": 1, "environment": "production - api"}])
        self.assertEqual(moved[0], "deployed")
        self.assertIn("`api`", moved[1])

    def test_another_environment_does_not_count(self):
        moved = self.step("production", [], [{"id": 1, "environment": "staging"}])
        self.assertEqual(moved[0], "needs_human")
        self.assertIn("deploy provider", moved[1])

    def test_with_no_deploy_watch_the_merge_is_live_once_every_workflow_is_green(self):
        self.assertEqual(self.step("", [{"name": "ci", "status": "completed", "conclusion": "success"}])[0], "deployed")

    def test_with_no_deploy_watch_a_running_workflow_waits(self):
        self.assertIsNone(self.step("", [{"name": "ci", "status": "in_progress", "conclusion": None}]))


class AddressReviewTest(unittest.TestCase):
    PR = {"number": 7, "merged": False, "state": "open", "mergeable": True, "head": {"sha": "head", "ref": "fix/issue-7-x"}}
    REVIEWS = [{"id": 11, "state": "CHANGES_REQUESTED", "user": {"login": "eng"}, "body": "Rename the helper."}]
    COMMENTS = [{"path": "apps/web/src/a.tsx", "line": 3, "original_line": 3, "body": "Use the constant.",
                 "diff_hunk": "@@ -1 +1 @@"}]

    def step(self, thread, on=True, team=("eng",)):
        def github(path, *args, **kwargs):
            if path.endswith("/reviews?per_page=100"):
                return self.REVIEWS
            if "/reviews/11/comments" in path:
                return self.COMMENTS
            return dict(self.PR)

        with mock.patch.object(pr, "ADDRESS_REVIEW", on), mock.patch.object(pr, "github", side_effect=github),              mock.patch.object(pr, "checks_state", return_value="green"),              mock.patch.object(pr, "team_logins", return_value=set(team)),              mock.patch.object(pr, "review_state", return_value=("changes", "eng")):
            return pr.step(thread)

    def test_an_engineer_change_request_starts_a_review_run(self):
        thread = {"stage": "pr_open", "pr": 7}
        moved = self.step(thread)
        self.assertEqual(moved[0], "review_fixing")
        self.assertEqual((thread["review_attempts"], thread["branch"], thread["reviews_done"]), (1, "fix/issue-7-x", [11]))
        self.assertEqual(thread["review"]["comments"][0]["body"], "Use the constant.")
        self.assertEqual(thread["changes_body"], "Rename the helper.")
        self.assertNotIn("react", thread)

    def test_a_review_already_applied_waits_for_the_reviewer(self):
        self.assertIsNone(self.step({"stage": "pr_open", "pr": 7, "reviews_done": [11], "review_attempts": 1}))

    def test_after_three_rounds_a_human_takes_it(self):
        moved = self.step({"stage": "pr_open", "pr": 7, "review_attempts": pr.REVIEW_FIX_ATTEMPTS})
        self.assertEqual(moved[0], "needs_human")
        self.assertIn(f"after {pr.REVIEW_FIX_ATTEMPTS} rounds", moved[1])

    def test_a_change_request_from_outside_engineering_goes_to_a_human(self):
        thread = {"stage": "pr_open", "pr": 7}
        self.assertEqual(self.step(thread, team=("other",))[0], "needs_human")
        self.assertEqual(thread["react"], "x")

    def test_with_the_switch_off_a_human_takes_it(self):
        self.assertEqual(self.step({"stage": "pr_open", "pr": 7}, on=False)[0], "needs_human")

    def test_the_answer_comments_and_asks_for_a_new_review(self):
        with mock.patch.object(pr, "github", return_value={}) as github:
            self.assertTrue(pr.answer_review(7, "eng", "- renamed @team", "abcdef1234", 1))
        comment, request = github.call_args_list
        self.assertNotIn("@team", comment.kwargs["body"]["body"])
        self.assertIn(f"abcdef1, round 1 of {pr.REVIEW_FIX_ATTEMPTS}", comment.kwargs["body"]["body"])
        self.assertEqual(request.kwargs["body"], {"reviewers": ["eng"]})

    def test_a_failed_answer_does_not_raise(self):
        with mock.patch.object(pr, "github", side_effect=OSError("down")):
            self.assertFalse(pr.answer_review(7, "eng", "x", "abcdef1", 1))


class ExtraFilesTest(unittest.TestCase):
    def test_extra_files_lists_what_the_plan_does_not(self):
        # Two tests outside the plan that only assert the new rounding.
        paths = {"apps/web/src/a.tsx", "apps/web/src/b.test.ts", "shared/utils/c.test.ts"}
        self.assertEqual(pr.extra_files(paths, PLAN), ["apps/web/src/b.test.ts", "shared/utils/c.test.ts"])

    def test_a_guarded_path_is_never_offered_as_an_extra_file(self):
        self.assertEqual(pr.extra_files({"apps/web/src/b.ts", ".github/workflows/ci.yml"}, PLAN), [])

    def test_a_patch_inside_the_plan_has_no_extra_file(self):
        self.assertEqual(pr.extra_files(pr.patch_paths(PATCH), PLAN), [])


def reviews(*entries):
    return [{"state": state, "user": {"login": login}, "commit_id": sha} for login, state, sha in entries]


class ReviewTest(unittest.TestCase):
    PR = {"number": 1, "head": {"sha": "head"}}

    def state(self, entries):
        with mock.patch.object(pr, "github", return_value=reviews(*entries)), \
             mock.patch.object(pr, "team_logins", return_value={"eng"}):
            return pr.review_state(self.PR)

    def test_an_engineer_approval_of_the_head_merges(self):
        self.assertEqual(self.state([("eng", "APPROVED", "head")]), ("approved", "eng"))

    def test_an_approval_from_outside_engineering_does_not_count(self):
        self.assertEqual(self.state([("designer", "APPROVED", "head")]), ("waiting", ""))

    def test_an_approval_of_an_older_commit_does_not_count(self):
        self.assertEqual(self.state([("eng", "APPROVED", "old")]), ("waiting", ""))

    def test_changes_requested_by_anyone_stops_the_merge(self):
        self.assertEqual(self.state([("eng", "APPROVED", "head"), ("designer", "CHANGES_REQUESTED", "head")]),
                         ("changes", "designer"))

    def test_a_later_approval_replaces_a_change_request(self):
        self.assertEqual(self.state([("eng", "CHANGES_REQUESTED", "old"), ("eng", "APPROVED", "head")]),
                         ("approved", "eng"))


def reference(number, is_pr, state):
    issue = {"number": number, "state": state, **({"pull_request": {"url": "x"}} if is_pr else {})}
    return {"event": "cross-referenced", "source": {"issue": issue}}


class AssignIssueTest(unittest.TestCase):
    def test_the_rejecting_engineer_becomes_the_only_assignee(self):
        with mock.patch.object(pr, "github", return_value={}) as github:
            self.assertTrue(pr.assign_issue("4151", "maxbo"))
        github.assert_called_once_with("repos/owner/repo/issues/4151", method="PATCH",
                                       body={"assignees": ["maxbo"]})

    def test_a_failed_assignment_does_not_raise(self):
        with mock.patch.object(pr, "github", side_effect=OSError("down")):
            self.assertFalse(pr.assign_issue("4151", "eng"))


class OpenPrsForTest(unittest.TestCase):
    def test_only_open_prs_count(self):
        # A real timeline: an issue, two closed PRs, one merged PR and one open PR.
        timeline = [reference(4018, False, "open"), reference(4007, True, "closed"), reference(4038, True, "open"),
                    reference(4079, True, "closed"), reference(4082, True, "closed"), {"event": "labeled"}]
        with mock.patch.object(pr, "github", return_value=timeline):
            self.assertEqual(pr.open_prs_for("4011"), [4038])

    def test_no_reference_means_no_open_pr(self):
        with mock.patch.object(pr, "github", return_value=[{"event": "labeled"}]):
            self.assertEqual(pr.open_prs_for("1"), [])


if __name__ == "__main__":
    unittest.main()
