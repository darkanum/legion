"""Run with: python3 -m unittest test_github_app (no dependencies: PyJWT is only imported to sign, and that is stubbed)."""

import os
import unittest
from unittest import mock

import github
import github_app

APP = {"LEGION_GITHUB_APP_ID": "123", "LEGION_GITHUB_APP_INSTALLATION_ID": "456"}


class AppTest(unittest.TestCase):
    def setUp(self):
        github_app._cache.clear()
        github_app._identity = None
        github._me = None
        for patcher in (mock.patch.dict(os.environ, APP), mock.patch.object(github_app, "app_jwt", return_value="jwt")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def minted(self, *expires):
        """A fake access_tokens endpoint: one token per call, with the given expiry times."""
        answers = iter({"token": f"t{n}", "expires_at": stamp} for n, stamp in enumerate(expires))
        return mock.patch.object(github_app, "post", side_effect=lambda path, bearer, body=None: next(answers))

    def test_the_host_token_is_kept_until_five_minutes_before_it_ends(self):
        expires = github_app.expiry("2026-10-18T13:00:00Z")
        with self.minted("2026-10-18T13:00:00Z", "2026-10-18T14:00:00Z") as post:
            self.assertEqual(github_app.token(now=expires - 3600), "t0")
            self.assertEqual(github_app.token(now=expires - 400), "t0")
            self.assertEqual(github_app.token(now=expires - 200), "t1")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.args[0], "app/installations/456/access_tokens")

    def test_the_sandbox_token_asks_only_for_read_permissions(self):
        with self.minted("2026-10-18T13:00:00Z") as post:
            github_app.token(read_only=True, now=github_app.expiry("2026-10-18T12:00:00Z"))
        permissions = post.call_args.args[2]["permissions"]
        self.assertEqual(set(permissions.values()), {"read"})
        self.assertNotIn("write", str(permissions))

    def test_the_identity_is_the_app_bot_user(self):
        def get(path, bearer):
            return {"slug": "legion"} if path == "app" else {"login": "legion[bot]", "id": 99}

        with mock.patch.object(github_app, "get", side_effect=get) as calls, \
                mock.patch.object(github_app, "token", return_value="t"):
            self.assertEqual(github.me(), {"login": "legion[bot]", "id": 99})
        self.assertEqual(calls.call_args.args[0], "users/legion%5Bbot%5D")

    def test_github_token_takes_the_app_token_for_both_github_names(self):
        with mock.patch.object(github_app, "token", side_effect=lambda read_only=False: f"app-{read_only}"):
            self.assertEqual(github.token(github.HOST_TOKEN), "app-False")
            self.assertEqual(github.token(github.SANDBOX_TOKEN), "app-True")

    def test_without_the_app_settings_the_token_files_are_read(self):
        with mock.patch.dict(os.environ, {"LEGION_GITHUB_APP_ID": ""}), \
                mock.patch("pathlib.Path.read_text", return_value="file-token\n"):
            self.assertFalse(github_app.enabled())
            self.assertEqual(github.token(github.HOST_TOKEN), "file-token")


class JwtTest(unittest.TestCase):
    def test_the_jwt_is_rs256_signed_by_the_app_key_and_ends_within_ten_minutes(self):
        try:
            import jwt
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            self.skipTest("PyJWT[crypto] is not installed (shared/host/requirements.txt)")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        with mock.patch.dict(os.environ, APP), mock.patch("pathlib.Path.read_text", return_value=pem):
            token = github_app.app_jwt(now=1_800_000_000)
        claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
        self.assertEqual(claims["iss"], "123")
        # GitHub refuses an exp more than ten minutes ahead; iat sits a minute back to cover clock drift.
        self.assertLess(claims["exp"] - 1_800_000_000, 600)
        self.assertLess(claims["iat"], 1_800_000_000)


if __name__ == "__main__":
    unittest.main()
