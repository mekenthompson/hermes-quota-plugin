import unittest
from unittest import mock
from quota_providers import builtin


class FreshnessTests(unittest.TestCase):
    def test_codex_duration_comes_from_vendor_not_label_or_reset(self):
        for raw, expected in (({}, None), ({'limit_window_seconds': 604800}, 604800)):
            payload = {'rate_limit': {'primary_window': dict(raw, used_percent=20, reset_at=1893456000)}}
            result = builtin._parse_codex_payload(payload)
            self.assertEqual(getattr(result.windows[0], 'duration_seconds', None), expected)

    def test_anthropic_declared_period_key_carries_duration(self):
        windows, _ = builtin._parse_anthropic_usage({'seven_day': {'utilization': 20}})
        self.assertEqual(windows[0].duration_seconds, 604800)

    def test_cached_success_preserves_machine_readable_age_and_staleness(self):
        payload = builtin._ObservedAnthropicPayload(
            {'seven_day': {'utilization': 42, 'resets_at': '2026-01-07T00:00:00Z'}},
            1767225600, True)
        with mock.patch.object(builtin, '_anthropic_usage_payload', return_value=(payload, None)):
            result = builtin._fetch_anthropic_single()
        self.assertTrue(getattr(result, 'stale', False))
        self.assertEqual(getattr(result, 'observed_at', None), '2026-01-01T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
