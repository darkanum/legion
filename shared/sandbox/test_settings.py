"""Run with: python3 -m unittest (no dependencies)."""

import os
import unittest
from unittest import mock

import settings


class ManagedSettingsTest(unittest.TestCase):
    def test_every_guarded_prefix_is_denied_to_edit_and_write(self):
        with mock.patch.object(settings.config, "GUARDED", (".github/", "db/schema.sql")):
            deny = settings.managed_settings()["permissions"]["deny"]
        for rule in ("Edit(.github/**)", "Write(.github/**)", "Edit(db/schema.sql)", "Write(db/schema.sql)"):
            self.assertIn(rule, deny)

    def test_the_network_and_the_environment_stay_denied(self):
        deny = settings.managed_settings()["permissions"]["deny"]
        for rule in ("Bash(git push:*)", "Bash(curl:*)", "Bash(printenv:*)", "Read(**/.env*)", "WebFetch"):
            self.assertIn(rule, deny)

    def test_the_repository_tools_come_from_the_settings(self):
        with mock.patch.dict(os.environ, {"LEGION_SANDBOX_ALLOW": "Bash(go test:*), Bash(make:*)"}):
            allow = settings.managed_settings()["permissions"]["allow"]
        self.assertIn("Bash(go test:*)", allow)
        self.assertIn("Bash(make:*)", allow)
        self.assertNotIn("Bash(pnpm:*)", allow)


class AllowlistTest(unittest.TestCase):
    def test_hosts_are_anchored_escaped_and_unique(self):
        env = {"LEGION_PROXY_HOSTS": "proxy.golang.org,api.github.com", "LEGION_POSTHOG_HOST": "eu.posthog.com"}
        with mock.patch.dict(os.environ, env):
            lines = settings.allowlist().splitlines()
        self.assertIn(r"^proxy\.golang\.org$", lines)
        self.assertIn(r"^eu\.posthog\.com$", lines)
        self.assertEqual(lines.count(r"^api\.github\.com$"), 1)


if __name__ == "__main__":
    unittest.main()
