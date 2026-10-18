"""Run with: python3 -m unittest test_pull_request (no dependencies)."""

import unittest
from unittest import mock

import pull_request as pr

PLAN = {"files": ["apps/web/src/a.tsx", "apps/web/src/a.test.tsx (new)"]}
PATCH = """diff --git a/apps/web/src/a.tsx b/apps/web/src/a.tsx
--- a/apps/web/src/a.tsx
diff --git a/apps/web/src/a.test.tsx b/apps/web/src/a.test.tsx
new file mode 100644
"""


class ScopeTest(unittest.TestCase):
    def test_a_patch_inside_the_plan_passes(self):
        self.assertIsNone(pr.scope_error(pr.patch_paths(PATCH), PLAN))

    def test_a_file_outside_the_plan_fails(self):
        self.assertIn("does not list", pr.scope_error({"apps/web/src/b.tsx"}, PLAN))

    def test_a_guarded_path_fails_even_when_the_plan_lists_it(self):
        for path in (".github/workflows/ci.yml", "infra/x.tf", ".legion/rules/STANDARDS.md",
                     ".claude/skills/x/SKILL.md", "api/.env", "api/.env.production"):
            self.assertIn("must never change", pr.scope_error({path}, {"files": [path]}), path)

    def test_a_rename_counts_both_paths(self):
        self.assertEqual(pr.patch_paths("diff --git a/x/old.ts b/.github/new.ts\n"), {"x/old.ts", ".github/new.ts"})

    def test_a_header_the_pattern_cannot_read_fails_closed(self):
        # git quotes a name with a space or a special character; the regex reads neither, so the plan never matches.
        for header in ('diff --git "a/apps/web/my file.ts" "b/apps/web/my file.ts"',
                       "diff --git a/apps/web/x y.ts b/.github/workflows/x y.yml"):
            paths = pr.patch_paths(f"{header}\n--- a\n")
            self.assertIn("does not list", pr.scope_error(paths, PLAN), header)


class StagedPathsTest(unittest.TestCase):
    def test_git_lists_every_applied_path_with_both_sides_of_a_rename(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def run(*args):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                                *args], cwd=repo, check=True, capture_output=True)

            run("init", "-q")
            (repo / "old.ts").write_text("a\n")
            run("add", "-A")
            run("commit", "-q", "-m", "base")
            (repo / "old.ts").rename(repo / "my new.ts")
            (repo / ".github").mkdir()
            (repo / ".github" / "ci.yml").write_text("on: push\n")
            run("add", "-A")
            self.assertEqual(pr.staged_paths(repo), {"old.ts", "my new.ts", ".github/ci.yml"})


class CommitPatchTest(unittest.TestCase):
    def test_a_path_outside_the_plan_in_what_git_applied_stops_before_any_push(self):
        with mock.patch.object(pr, "git") as git, mock.patch.object(pr, "git_auth", return_value="h"), \
                mock.patch.object(pr, "staged_paths", return_value={"apps/web/src/a.tsx", "infra/main.tf"}):
            with self.assertRaises(pr.ScopeRefused) as refused:
                pr.commit_patch({"login": "bot", "id": 1}, "a" * 40, "fix/x", PATCH, "fix: x", PLAN)
        self.assertIn("infra/main.tf", str(refused.exception))
        self.assertFalse([c for c in git.call_args_list if "push" in c.args or "commit" in c.args])
        self.assertEqual(pr.describe(refused.exception), str(refused.exception))



class TextTest(unittest.TestCase):
    def test_attribution_lines_go_but_a_claude_path_stays(self):
        text = "see .claude/skills/x.md\n🤖 Generated with [Claude Code](x)\nCo-Authored-By: Claude <a@b>"
        self.assertEqual(pr.without_attribution(text), "see .claude/skills/x.md")

    def test_a_mention_cannot_ping(self):
        self.assertNotIn("@team", pr.no_mention("ping @team"))

    def test_labels_follow_the_packages_that_exist(self):
        paths = {"apps/web/src/a.tsx", "packages/ui/b.ts", "pnpm-lock.yaml", "services/api/c.go", "docs/x.md"}
        existing = {"apps/web", "packages/ui", "dependencies", "docs"}
        with mock.patch.object(pr.config, "PACKAGE_ROOTS", ("apps", "packages")):
            self.assertEqual(pr.labels_for(paths, existing), ["apps/web", "dependencies", "docs", "packages/ui"])

    def test_no_package_root_gives_no_package_label(self):
        with mock.patch.object(pr.config, "PACKAGE_ROOTS", ()):
            self.assertEqual(pr.labels_for({"apps/web/src/a.tsx", "api/go.sum"}, {"apps/web", "dependencies"}),
                             ["dependencies"])


def fake_github(calls, refuse_team=False):
    """A fake GitHub that opens PR 9 and records every call as (path, method, body)."""
    def github(path, *args, method="GET", body=None, **kwargs):
        calls.append((path, method, body))
        if path == "user":
            return {"login": "legion-bot", "id": 1}
        if path.endswith("/pulls") and method == "POST":
            return {"number": 9, "html_url": "u"}
        if refuse_team and path.endswith("/requested_reviewers") and "team_reviewers" in (body or {}):
            raise OSError("422")
        return []
    return github


class OpenPullRequestTest(unittest.TestCase):
    REVIEWERS = "repos/acme/shop/pulls/9/requested_reviewers"
    ASSIGNEE = ("repos/acme/shop/issues/9/assignees", "POST", {"assignees": ["legion-bot"]})

    def open(self, refuse_team=False, people=("ana",), teams=("engineering",)):
        calls = []
        with mock.patch.object(pr, "github", side_effect=fake_github(calls, refuse_team)), mock.patch.object(pr, "git"), \
             mock.patch.object(pr, "git_auth", return_value="h"), mock.patch.object(pr.axiom, "emit") as emit, \
             mock.patch.object(pr, "staged_paths", return_value=pr.patch_paths(PATCH)), \
             mock.patch.object(pr, "REPO", "acme/shop"), mock.patch.object(pr.config, "BASE_BRANCH", "main"), \
             mock.patch.object(pr, "PR_REVIEWERS", list(people)), mock.patch.object(pr, "PR_TEAM_REVIEWERS", list(teams)):
            result = pr.open_pull_request("valac", "7", "a" * 40, "fix/x", PATCH, "fix: x", "body", PLAN)
        return result, calls, emit

    def test_a_bot_pr_has_the_bot_as_assignee_and_the_configured_reviewers(self):
        result, calls, _ = self.open()
        self.assertEqual(result, {"pr": 9, "url": "u"})
        self.assertIn(("repos/acme/shop/pulls", "POST",
                       {"title": "fix: x", "head": "fix/x", "base": "main", "body": "body"}), calls)
        self.assertIn((self.REVIEWERS, "POST", {"reviewers": ["ana"]}), calls)
        self.assertIn((self.REVIEWERS, "POST", {"team_reviewers": ["engineering"]}), calls)
        self.assertIn(self.ASSIGNEE, calls)

    def test_no_configured_reviewer_sends_no_request(self):
        _, calls, _ = self.open(people=(), teams=())
        self.assertFalse([c for c in calls if c[0] == self.REVIEWERS])

    def test_a_refused_team_review_still_returns_the_pr_and_logs_under_the_agent(self):
        # GitHub can answer 422 to a team request; the PR is open all the same, and the agent must say so.
        result, calls, emit = self.open(refuse_team=True)
        self.assertEqual(result, {"pr": 9, "url": "u"})
        self.assertIn(self.ASSIGNEE, calls)
        emit.assert_called_once()
        self.assertEqual(emit.call_args.args[:2], ("valac", "valac.pr.reviewers_failed"))


if __name__ == "__main__":
    unittest.main()
