import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import quota_history as history


class CanonicalHistoryTests(unittest.TestCase):
    def snapshot(self, **extra):
        return {'fetched_at': '2026-01-01T02:00:00Z', 'providers': {'grok': {
            'history_id': 'a'*64, 'plan': 'Pro', 'windows': [
                {'label': 'Weekly', 'used_percent': 30, 'reset_at': '2026-01-07T00:00:00Z', 'duration_seconds': 604800}], **extra}}}

    def test_later_source_fetch_is_not_mistaken_for_future_observation(self):
        snapshot = {'fetched_at': '2026-01-01T00:00:00Z', 'providers': {'grok': {
            'fetched_at': '2026-01-01T00:00:30Z', 'observed_at': '2026-01-01T00:00:29Z',
            'windows': [{'label': 'Weekly', 'used_percent': 20}]}}}
        clean = history.sanitize_snapshot(snapshot)['providers']['grok']
        self.assertIsNone(clean['unavailable_reason'])
        self.assertEqual(clean['fetched_at'], '2026-01-01T00:00:30+00:00')

    def test_stale_success_cannot_become_healthy_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(history, '_path', return_value=Path(tmp)/'history.db'):
            history.record_snapshot(self.snapshot(stale=True))
            rows = history.read_history()
        self.assertEqual(rows[0]['status'], 'stale-data')
        self.assertIsNone(rows[0]['used_percent'])

    def test_metadata_survives_exchange_and_storage(self):
        raw = self.snapshot(observed_at='2026-01-01T01:59:30Z')
        clean = history.sanitize_snapshot(raw)
        with tempfile.TemporaryDirectory() as tmp, patch.object(history, '_path', return_value=Path(tmp)/'history.db'):
            history.record_snapshot(clean)
            row = history.read_history()[0]
        self.assertEqual(row.get('plan'), 'Pro')
        self.assertEqual(row.get('observed_at'), '2026-01-01T01:59:30+00:00')
        self.assertEqual(row.get('duration_seconds'), 604800)

    def test_plan_change_breaks_forecast_continuity(self):
        rows = []
        for hour in range(3):
            rows.append({'fetched_at': f'2026-01-01T0{hour}:00:00+00:00',
                         'provider': 'grok', 'account': 'a'*64, 'label': 'Account',
                         'window': 'Weekly', 'reset_at': '2026-01-07T00:00:00+00:00',
                         'status': 'ok', 'identity_verified': 1, 'used_percent': 10 + hour*10,
                         'plan': 'Old' if hour == 0 else 'New'})
        result = history.forecasts(rows, now=rows[-1]['fetched_at'])
        self.assertIsNone(result[0]['rate_percentage_points_per_hour'])

    def test_old_schema_is_readable_and_old_writer_survives_upgrade(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp, patch.object(history, '_path', return_value=Path(tmp)/'history.db'):
            db = sqlite3.connect(history._path())
            db.execute('CREATE TABLE samples (fetched_at TEXT, provider TEXT, account TEXT, label TEXT, window TEXT, reset_at TEXT, used_percent REAL, status TEXT, identity_verified INTEGER, PRIMARY KEY(fetched_at,provider,account,window,reset_at))')
            old = ('2026-01-01T00:00:00+00:00', 'grok', 'a'*64, 'Shared', 'Weekly', '2026-01-07T00:00:00+00:00', 20, 'ok', 1)
            db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)', old); db.commit()
            self.assertIsNone(history.read_history()[0]['plan'])
            history.record_snapshot(self.snapshot())
            db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)', ('2026-01-01T01:00:00+00:00',)+old[1:]); db.commit()
            self.assertEqual(db.execute('SELECT * FROM samples WHERE fetched_at=?',(old[0],)).fetchone(), old)
            db.close()
            rows=history.read_history()
            self.assertEqual(len(rows), 3)
            self.assertIsNone(rows[0]['plan'])
            self.assertIsNone(rows[1]['duration_seconds'])

    def test_old_observation_is_not_retimed_by_fresh_sweep(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(history, '_path', return_value=Path(tmp)/'history.db'):
            history.record_snapshot(self.snapshot(observed_at='2026-01-01T00:00:00Z'))
            self.assertEqual(history.read_history()[0]['status'], 'stale-data')


if __name__ == '__main__':
    unittest.main()
