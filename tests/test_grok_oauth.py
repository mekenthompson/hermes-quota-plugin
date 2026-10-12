"""Offline tests for the Grok OAuth subscription fetcher."""

from __future__ import annotations

import json
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
