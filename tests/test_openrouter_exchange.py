"""OpenRouter money survives sample/ingest without transporting arbitrary text."""
import dataclasses
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
pkg = types.ModuleType('openrouter_exchange_test')
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
history = importlib.import_module(pkg.__name__ + '.quota_history')
provider = importlib.import_module(pkg.__name__ + '.quota_providers.openrouter')

class OpenRouterExchangeTest(unittest.TestCase):
    def snapshot(self, record):
        return {'fetched_at': '2026-01-01T00:00:00Z', 'providers': {'openrouter': record}}

    def test_real_provider_money_survives_two_exchange_passes(self):
        def get(url, secret):
            return ({'data': {'total_credits': '20.005', 'total_usage': '12'}}, None) if url.endswith('/credits') else ({'data': {'limit': None, 'usage': 6, 'usage_daily': 1, 'usage_weekly': 2, 'usage_monthly': 3, 'email': 'private@example.test'}}, None)
        with mock.patch.object(provider, '_resolve_credentials', return_value=[('Key 1 (native)', 'PRIVATE_SECRET')]), mock.patch.object(provider, '_get_json', side_effect=get):
            record = dataclasses.asdict(provider.fetch_openrouter_quota())
        clean = history.sanitize_snapshot(history.sanitize_snapshot(self.snapshot(record)))['providers']['openrouter']
        self.assertEqual(clean['account_balances'], [{'currency': 'USD', 'total_balance': '8.005'}])
        self.assertIn('Key 1 (native): usage this month UTC USD 3.0000', clean['details'])
        self.assertEqual(clean['windows'], [])
        for private in ('PRIVATE_SECRET', 'private@example.test', 'access_token'):
            self.assertNotIn(private, json.dumps(clean))

    def test_widget_shows_wallet_and_usage_from_exchange(self):
        from widget_harness import render, text, tooltip_text
        record = {'label': 'OpenRouter wallet', 'windows': [],
                  'account_balances': [{'currency': 'USD', 'total_balance': '8.005'}],
                  'details': ['Key 1 (native): usage this month UTC USD 3.0000']}
        data = history.sanitize_snapshot(self.snapshot(record))
        data['age_s'] = 1
        status = render(data=data, component='status')
        pane = text(render(data=data))
        self.assertIn('$8.005 USD', text(status))
        self.assertIn('usage this month UTC USD 3.0000', tooltip_text(status))
        self.assertIn('usage this month UTC USD 3.0000', pane)
        self.assertNotIn('%', text(status))

    def test_exchange_refuses_arbitrary_details_and_invalid_balances(self):
        raw = {'label': 'openrouter', 'details': ['Bearer secret', 'private@example.test', 'Key 1 (native): usage total USD NaN', 'Key 1 (native): usage total USD 4.0000'],
               'account_balances': [{'currency': 'USD', 'total_balance': 'NaN'}, {'currency': 'USD', 'total_balance': True}, {'currency': 'sk-secret', 'total_balance': '3'}, {'currency': 'USD', 'total_balance': '4', 'access_token': 'secret'}]}
        clean = history.sanitize_snapshot(self.snapshot(raw))['providers']['openrouter']
        self.assertEqual(clean['details'], ['Key 1 (native): usage total USD 4.0000'])
        self.assertEqual(clean['account_balances'], [{'currency': 'USD', 'total_balance': '4'}])
        raw['unavailable_reason'] = 'auth-failed'
        failed = history.sanitize_snapshot(self.snapshot(raw))['providers']['openrouter']
        self.assertEqual(failed['details'], [])
        self.assertEqual(failed['account_balances'], [])

if __name__ == '__main__':
    unittest.main()
