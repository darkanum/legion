"""Run with: python3 -m unittest test_github (no dependencies)."""

import unittest
from unittest import mock

import github


class ChecksTest(unittest.TestCase):
    def state(self, rollup):
        answer = {"data": {"repository": {"object": {"statusCheckRollup": rollup}}}}
        with mock.patch.object(github, "github", return_value=answer):
            return github.checks_state("sha")

    def test_success_is_green(self):
        self.assertEqual(self.state({"state": "SUCCESS"}), "green")

    def test_failure_or_error_is_red(self):
        self.assertEqual(self.state({"state": "FAILURE"}), "red")
        self.assertEqual(self.state({"state": "ERROR"}), "red")

    def test_a_running_check_is_pending(self):
        self.assertEqual(self.state({"state": "PENDING"}), "pending")

    def test_no_check_at_all_is_pending(self):
        self.assertEqual(self.state(None), "pending")


class GuardedTest(unittest.TestCase):
    def test_guarded_paths_and_env_files_are_listed(self):
        paths = [".github/workflows/ci.yml", "infra/main.tf", "api/.env.production", "web/src/a.tsx", "docs/README.md"]
        self.assertEqual(github.guarded_paths(paths), [".github/workflows/ci.yml", "infra/main.tf", "api/.env.production"])


class TeamTest(unittest.TestCase):
    def test_a_team_lists_its_members(self):
        with mock.patch.object(github.config, "TEAM", "acme/engineering"),                 mock.patch.object(github, "pages", return_value=iter([[{"login": "ana"}, {"login": "bo"}]])) as pages:
            self.assertEqual(github.team_logins(), {"ana", "bo"})
        self.assertEqual(pages.call_args.args[0], "orgs/acme/teams/engineering/members?per_page=100")

    def test_no_team_takes_the_collaborators_who_can_push_and_drops_bots(self):
        people = [{"login": "ana", "type": "User"}, {"login": "ci[bot]", "type": "Bot"}]
        with mock.patch.object(github.config, "TEAM", ""), mock.patch.object(github, "pages", return_value=iter([people])):
            self.assertEqual(github.team_logins(), {"ana"})

    def test_the_mention_names_the_team_or_the_owner(self):
        with mock.patch.object(github.config, "TEAM", "acme/engineering"):
            self.assertEqual(github.team_mention(), "@acme/engineering")
        with mock.patch.object(github.config, "TEAM", ""), mock.patch.object(github.config, "REPO", "ana/shop"):
            self.assertEqual(github.team_mention(), "@ana")


if __name__ == "__main__":
    unittest.main()
