"""Offline tests for the Grok OAuth subscription fetcher."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from quota_providers import grok


_TOKEN = "fake-xai-oauth-token"
_BILLING = {
    "config": {
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "end": "2026-10-17T00:00:00+00:00",
        },
        "creditUsagePercent": 14.0,
        "onDemandCap": {"val": 0},
        "onDemandUsed": {"val": 2500},
        "prepaidBalance": {"val": 0},
        "productUsage": [
            {"product": "GrokBuild", "usagePercent": 10},
            {"product": "GrokChat", "usagePercent": 4},
        ],
    }
}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self, _limit):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class GrokOAuthTests(unittest.TestCase):
    def test_expired_legacy_snapshot_does_not_become_a_second_account(self):
        def jwt(exp):
            payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return f"header.{payload}.signature"

        live, expired = jwt(200), jwt(50)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = home / "auth.json"
            auth.write_text(json.dumps({
                "providers": {"xai-oauth": {"tokens": {"access_token": expired}}},
                "credential_pool": {"xai-oauth": [{"access_token": live}]},
            }))
            before = auth.read_bytes()
            constants = types.ModuleType("hermes_constants")
            setattr(constants, "get_hermes_home", lambda: home)
            setattr(constants, "get_default_hermes_root", lambda: home)
            with mock.patch.dict("sys.modules", {"hermes_constants": constants}), \
                 mock.patch("time.time", return_value=100):
                tokens = grok._saved_xai_tokens()
                with mock.patch.object(grok, "_oauth_tokens", return_value=tokens), \
                     mock.patch.object(grok, "urlopen_no_redirect", return_value=_Response(_BILLING)) as request:
                    result = grok._fetch_grok_oauth()
            self.assertEqual(tokens, [live])
            self.assertEqual(result.accounts, [])
            self.assertEqual(request.call_count, 2)  # billing + settings, one credential
            self.assertEqual(auth.read_bytes(), before)

    def test_saved_pool_entries_and_legacy_only_failures_stay_visible(self):
        def jwt(exp):
            payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return f"header.{payload}.signature"

        live, expired = jwt(200), jwt(50)
        cases = [
            (expired, [], [expired]),
            (live, [live], [live]),
            (live, ["opaque-account"], [live, "opaque-account"]),
            (expired, [expired, live], [expired, live]),
            (expired, ["opaque-account"], ["opaque-account"]),
            ("malformed.jwt.value", [live], ["malformed.jwt.value", live]),
        ]
        for legacy, pool, expected in cases:
            with self.subTest(pool_size=len(pool), legacy_expired=legacy == expired), \
                 tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                auth = home / "auth.json"
                auth.write_text(json.dumps({
                    "providers": {"xai-oauth": {"tokens": {"access_token": legacy}}},
                    "credential_pool": {"xai-oauth": [{"access_token": t} for t in pool]},
                }))
                before = auth.read_bytes()
                constants = types.ModuleType("hermes_constants")
                setattr(constants, "get_hermes_home", lambda: home)
                setattr(constants, "get_default_hermes_root", lambda: home)
                with mock.patch.dict("sys.modules", {"hermes_constants": constants}), \
                     mock.patch("time.time", return_value=100):
                    self.assertEqual(grok._saved_xai_tokens(), expected)
                self.assertEqual(auth.read_bytes(), before)

    def test_unknown_jwt_expiry_is_not_treated_as_expired(self):
        for payload in ({}, {"exp": True}, {"exp": "50"}, {"exp": float("nan")}, []):
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            with self.subTest(payload=payload):
                self.assertFalse(grok._known_expired(f"header.{encoded}.signature"))

    def test_subscription_percent_ignores_on_demand_and_prepaid(self):
        def opener(request, timeout):
            del timeout
            self.assertEqual(request.get_header("Authorization"), f"Bearer {_TOKEN}")
            self.assertNotIn(_TOKEN, request.full_url)
            if request.full_url.endswith("/v1/settings"):
                return _Response({"subscription_tier_display": "SuperGrok"})
            return _Response(_BILLING)

        with mock.patch.object(grok, "urlopen_no_redirect", opener):
            result = grok._oauth_account(_TOKEN, 1)

        self.assertEqual([window.used_percent for window in result.windows], [14.0])
        self.assertEqual(result.windows[0].label, "Weekly pool")
        self.assertEqual(result.plan, "SuperGrok")
        self.assertIn("Pay-as-you-go: off", result.details)
        self.assertIn("Prepaid balance: $0", result.details)
        self.assertNotIn(_TOKEN, " ".join(result.details))

    def test_missing_subscription_percent_is_not_replaced_by_on_demand(self):
        payload = {"config": dict(_BILLING["config"])}
        del payload["config"]["creditUsagePercent"]
        payload["config"]["onDemandCap"] = {"val": 100}
        payload["config"]["onDemandUsed"] = {"val": 80}

        with mock.patch.object(grok, "urlopen_no_redirect", lambda *_a, **_k: _Response(payload)):
            result = grok._oauth_account(_TOKEN, 1)

        self.assertEqual(result.windows, [])
        self.assertEqual(result.unavailable_reason, "subscription-usage-unavailable")
        self.assertIn("Pay-as-you-go: on (cap 100 raw units, used 80 raw units)", result.details)

    def test_registered_path_uses_oauth_without_enabling_cookies(self):
        oauth = grok.QuotaResult(label="grok", windows=[grok.QuotaWindow(label="Weekly pool", used_percent=3)])
        with mock.patch.object(grok, "_fetch_grok_oauth", return_value=oauth), \
             mock.patch.object(grok, "_grok_enabled", return_value=False), \
             mock.patch.object(grok, "fetch_grok_quota") as cookies:
            result = grok._fetch_grok_registered()
        self.assertIs(result, oauth)
        cookies.assert_not_called()

    def test_cookie_fallback_stays_disabled_without_a_token(self):
        missing = grok.build_unavailable("grok", "no-credentials")
        with mock.patch.object(grok, "_fetch_grok_oauth", return_value=missing), \
             mock.patch.object(grok, "_grok_enabled", return_value=False), \
             mock.patch.object(grok, "fetch_grok_quota") as cookies:
            result = grok._fetch_grok_registered()
        cookies.assert_not_called()
        self.assertEqual(result.unavailable_reason, "no-credentials")
        self.assertTrue(any("opt-in" in line for line in result.details))


if __name__ == "__main__":
    unittest.main()
