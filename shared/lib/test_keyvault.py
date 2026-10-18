"""Run with: python3 -m unittest test_keyvault (no dependencies)."""

import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import keyvault

AGENTS_DIR = Path(__file__).resolve().parents[2]  # the repository root: one folder per agent


class VaultNamesTest(unittest.TestCase):
    def test_a_vault_name_uses_dashes(self):
        self.assertEqual(keyvault.vault_names("orobas", AGENTS_DIR)["claude_oauth_token"], "claude-oauth-token")

    def test_each_agent_reads_its_own_webhook_secret_under_the_shared_file_name(self):
        names = keyvault.vault_names("marbas", AGENTS_DIR)
        self.assertEqual(names["github_webhook_secret"], "github-webhook-secret-marbas")
        self.assertEqual(names["github_webhook_id"], "github-webhook-id-marbas")

    def test_orobas_gets_no_slack_or_sandbox_secret(self):
        names = keyvault.vault_names("orobas", AGENTS_DIR)
        self.assertNotIn("slack_bot_token", names)
        self.assertNotIn("github_sandbox_token", names)

    def test_every_agent_folder_with_a_setup_lists_its_secrets(self):
        for setup in AGENTS_DIR.glob("*/setup.sh"):
            if setup.parent.name != "shared":
                self.assertTrue(keyvault.agent_secrets(setup.parent.name, AGENTS_DIR), setup.parent.name)


class WriteSecretsTest(unittest.TestCase):
    def test_each_secret_lands_in_its_file_with_mode_600(self):
        def fake(url, headers):
            return {"value": "v-" + url.split("/secrets/")[1].split("?")[0]}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(keyvault, "token", return_value="t"), \
                mock.patch.object(keyvault, "get_json", side_effect=fake):
            written = keyvault.write_secrets("legion-kv-orobas", ["orobas"], Path(tmp), AGENTS_DIR)
            self.assertIn("github_webhook_secret", written)
            path = Path(tmp) / "github_webhook_secret"
            self.assertEqual(path.read_text(), "v-github-webhook-secret-orobas")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def run_with_missing(self, missing):
        def fake(url, headers):
            name = url.split("/secrets/")[1].split("?")[0]
            if name in missing:
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return {"value": "v"}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(keyvault, "token", return_value="t"),                 mock.patch.object(keyvault, "get_json", side_effect=fake):
            return keyvault.write_secrets("v", ["orobas"], Path(tmp), AGENTS_DIR)

    def test_a_webhook_pair_not_stored_yet_is_skipped(self):
        written = self.run_with_missing({"github-webhook-secret-orobas", "github-webhook-id-orobas"})
        self.assertIn("claude_oauth_token", written)
        self.assertNotIn("github_webhook_secret", written)

    def test_a_missing_agent_secret_fails_the_setup(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.run_with_missing({"axiom-ingest-token"})

if __name__ == "__main__":
    unittest.main()
