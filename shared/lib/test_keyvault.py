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
NO_APP = {"LEGION_GITHUB_APP_ID": "", "LEGION_HOSTNAME": ""}


class VaultNamesTest(unittest.TestCase):
    def names(self, agents, **env):
        with mock.patch.dict(os.environ, {**NO_APP, **env}):
            return keyvault.vault_names(agents, AGENTS_DIR)

    def test_a_vault_name_uses_dashes(self):
        self.assertEqual(self.names(["orobas"])["claude_oauth_token"], "claude-oauth-token")
        self.assertEqual(keyvault.vault_name("github_app_key.pem"), "github-app-key-pem")

    def test_the_webhook_pair_is_one_per_host_whatever_agents_it_runs(self):
        names = self.names(["marbas", "valac", "orobas"])
        self.assertEqual((names["github_webhook_secret"], names["github_webhook_id"]),
                         ("github-webhook-secret", "github-webhook-id"))

    def test_the_hostname_tells_the_pair_of_each_host_apart_in_one_vault(self):
        names = self.names(["orobas"], LEGION_HOSTNAME="agents.example.com")
        self.assertEqual(names["github_webhook_secret"], "github-webhook-secret-agents-example-com")

    def test_a_secret_two_agents_share_is_fetched_once(self):
        names = self.names(["marbas", "valac"])
        self.assertEqual(list(names).count("claude_oauth_token"), 1)

    def test_orobas_gets_no_slack_or_sandbox_secret(self):
        names = self.names(["orobas"])
        self.assertNotIn("slack_bot_token", names)
        self.assertNotIn("github_sandbox_token", names)

    def test_a_github_app_takes_its_key_instead_of_the_two_tokens(self):
        names = self.names(["marbas"], LEGION_GITHUB_APP_ID="1", LEGION_GITHUB_APP_INSTALLATION_ID="2")
        self.assertNotIn("github_host_token", names)
        self.assertNotIn("github_sandbox_token", names)
        self.assertEqual(names["github_app_key.pem"], "github-app-key-pem")

    def test_every_agent_folder_with_a_setup_lists_its_secrets(self):
        for setup in AGENTS_DIR.glob("*/setup.sh"):
            if setup.parent.name != "shared":
                self.assertTrue(keyvault.agent_secrets(setup.parent.name, AGENTS_DIR), setup.parent.name)


class WriteSecretsTest(unittest.TestCase):
    def write(self, fake, agents=("orobas",)):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, NO_APP), \
                mock.patch.object(keyvault, "token", return_value="t"), \
                mock.patch.object(keyvault, "get_json", side_effect=fake) as get:
            written = keyvault.write_secrets("legion-kv", list(agents), Path(tmp), AGENTS_DIR)
            files = {path.name: path for path in Path(tmp).iterdir()}
            texts = {name: path.read_text() for name, path in files.items()}
            modes = {name: stat.S_IMODE(path.stat().st_mode) for name, path in files.items()}
        return written, texts, modes, get

    def test_each_secret_lands_in_its_file_with_mode_600(self):
        written, texts, modes, _ = self.write(lambda url, headers: {"value": "v-" + url.split("/secrets/")[1].split("?")[0]})
        self.assertIn("github_webhook_secret", written)
        self.assertEqual(texts["github_webhook_secret"], "v-github-webhook-secret")
        if os.name == "posix":
            self.assertEqual(modes["github_webhook_secret"], 0o600)

    def test_several_agents_fetch_the_webhook_pair_once(self):
        _, _, _, get = self.write(lambda url, headers: {"value": "v"}, agents=("marbas", "valac", "orobas"))
        fetched = [call.args[0].split("/secrets/")[1].split("?")[0] for call in get.call_args_list]
        self.assertEqual(fetched.count("github-webhook-secret"), 1)
        self.assertEqual(len(fetched), len(set(fetched)))

    def missing(self, names):
        def fake(url, headers):
            name = url.split("/secrets/")[1].split("?")[0]
            if name in names:
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return {"value": "v"}
        return fake

    def test_a_webhook_pair_not_stored_yet_is_skipped(self):
        written, _, _, _ = self.write(self.missing({"github-webhook-secret", "github-webhook-id"}))
        self.assertIn("claude_oauth_token", written)
        self.assertNotIn("github_webhook_secret", written)

    def test_a_missing_agent_secret_fails_the_setup(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.write(self.missing({"axiom-ingest-token"}))


if __name__ == "__main__":
    unittest.main()
