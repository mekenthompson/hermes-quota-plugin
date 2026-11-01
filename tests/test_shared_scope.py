"""Effective request scopes, not token equality, identify Codex accounts."""
import base64
import json
import unittest
from unittest import mock
from quota_providers import codex


class SharedScopeTests(unittest.TestCase):
    def test_same_token_with_distinct_request_accounts_is_not_collapsed(self):
        claims = {'sub': 'synthetic-user', 'https://api.openai.com/auth': {'chatgpt_account_id': 'default'}}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        token = 'header.' + payload + '.signature'
        rows = [{'access_token': token, 'account_id': name} for name in ('first', 'second')]
        with mock.patch.object(codex, '_saved_rows', return_value=(rows, True)):
            credentials = codex._credentials()
        self.assertEqual([row[1] for row in credentials], ['first', 'second'])


if __name__ == '__main__':
    unittest.main()
