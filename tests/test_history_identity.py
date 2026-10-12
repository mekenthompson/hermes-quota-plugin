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
    def test_unknown_is_not_token_fingerprint(self):
        self.assertIsNone(identity_hint("grok", "opaque-token"))
