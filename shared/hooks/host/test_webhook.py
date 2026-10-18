"""Run with: python3 -m unittest (no dependencies)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import webhook

URL = "https://agents.example.com/webhooks/github"


class EnsureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.patches = [mock.patch.object(webhook, "SECRET_FILE", folder / "github_webhook_secret"),
                        mock.patch.object(webhook, "ID_FILE", folder / "github_webhook_id"),
                        mock.patch.object(webhook, "wanted_events", return_value=["pull_request", "issues"])]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def run_with(self, existing):
        calls = []

        def github(path, method="GET", body=None, **kwargs):
            calls.append((path, method, body))
            return {"id": 42}

        with mock.patch.object(webhook, "find", return_value=existing), mock.patch.object(webhook.gh, "github", side_effect=github):
            done = webhook.ensure(URL)
        return done, calls

    def test_a_new_hook_gets_every_subscribed_event_and_a_stored_secret(self):
        done, calls = self.run_with(None)
        self.assertTrue(done.startswith("created hook 42"))
        path, method, body = calls[0]
        self.assertEqual((path.endswith("/hooks"), method, body["events"]), (True, "POST", ["pull_request", "issues"]))
        self.assertEqual(body["config"]["secret"], webhook.SECRET_FILE.read_text())
        self.assertEqual(webhook.ID_FILE.read_text(), "42")

    def test_an_existing_hook_with_a_stored_secret_keeps_it(self):
        webhook.SECRET_FILE.write_text("kept")
        done, calls = self.run_with({"id": 42, "config": {"url": URL}})
        self.assertTrue(done.startswith("updated"))
        self.assertNotIn("secret", calls[0][2]["config"])
        self.assertEqual(webhook.SECRET_FILE.read_text(), "kept")

    def test_an_existing_hook_with_no_secret_on_the_host_gets_a_new_one(self):
        done, calls = self.run_with({"id": 42, "config": {"url": URL}})
        self.assertTrue(done.startswith("rotated"))
        self.assertEqual(calls[0][2]["config"]["secret"], webhook.SECRET_FILE.read_text())


class EventsTest(unittest.TestCase):
    def test_the_events_are_the_union_of_every_agent_subscription(self):
        events = webhook.wanted_events(Path(__file__).resolve().parents[3])
        self.assertIn("pull_request", events)
        self.assertIn("issues", events)


if __name__ == "__main__":
    unittest.main()
