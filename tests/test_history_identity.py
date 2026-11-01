import base64
import json
import unittest
from quota_providers.history_identity import identity_hint

class IdentityTests(unittest.TestCase):
    def test_refresh_does_not_change_identity(self):
        def token(exp):
            payload = base64.urlsafe_b64encode(json.dumps({"sub":"synthetic-subject", "exp":exp}).encode()).decode().rstrip("=")
            return f"header.{payload}.signature"
        self.assertEqual(identity_hint("grok", token(1)), identity_hint("grok", token(2)))
        self.assertNotEqual(identity_hint("grok", token(1)), identity_hint("openai-codex", token(1)))
        self.assertEqual(len(identity_hint("grok", token(1))), 64)
    def test_codex_requires_account_identity(self):
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "synthetic-subject"}).encode()).decode().rstrip("=")
        token = f"header.{payload}.signature"
        self.assertIsNone(identity_hint("openai-codex", token))

    def test_effective_codex_accounts_remain_distinct(self):
        from unittest import mock
        from quota_providers import codex
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "synthetic-subject"}).encode()).decode().rstrip("=")
        token = f"header.{payload}.signature"
        rows = [{"access_token": token, "account_id": "account-A"},
                {"access_token": token, "account_id": "account-B"}]
        with mock.patch.object(codex, "_saved_rows", return_value=(rows, True)):
            credentials = codex._credentials()
        self.assertEqual(len(credentials), 2)
        hints = [identity_hint("openai-codex", c[0], c[1]) for c in credentials]
        self.assertEqual(len(set(hints)), 2)

    def test_unknown_is_not_token_fingerprint(self):
        self.assertIsNone(identity_hint("grok", "opaque-token"))
