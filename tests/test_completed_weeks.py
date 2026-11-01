import unittest
from datetime import datetime, timedelta, timezone
import quota_history as h


class CompletedWeekTests(unittest.TestCase):
    def cycle(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [dict(fetched_at=(start+timedelta(hours=n)).isoformat(), provider='grok',
                     account='a'*64, label='Shared', window='Weekly',
                     reset_at=(start+timedelta(days=7)).isoformat(),
                     used_percent=n/2, status='ok', identity_verified=1,
                     plan='Pro', duration_seconds=604800) for n in range(168)]

    def summarize(self, rows):
        self.assertTrue(callable(getattr(h, 'completed_weeks', None)), 'canonical completed-week API required')
        return h.completed_weeks(rows, now='2026-01-09T00:00:00Z')

    def test_ended_cycle_is_averaged_once_not_as_hourly_snapshots(self):
        result = self.summarize(self.cycle())
        self.assertEqual(len(result['cycles']), 1)
        self.assertTrue(result['cycles'][0]['eligible'])
        self.assertEqual(result['averages'][0]['completed_cycles'], 1)
        self.assertEqual(result['averages'][0]['average_used_percent'], 83.5)

    def test_unknown_duration_partial_and_plan_changes_are_not_averaged(self):
        for kind in ('unknown', 'partial', 'plan', 'reset', 'failure'):
            with self.subTest(kind=kind):
                rows=self.cycle()
                if kind == 'unknown':
                    for r in rows: r['duration_seconds']=None
                elif kind == 'partial': rows=rows[20:]
                elif kind == 'plan': rows[80]['plan']='Changed'
                elif kind == 'reset': rows[80]['reset_at']='2026-01-08T01:00:00+00:00'
                else: rows[80]['status']='auth-failed'; rows[80]['used_percent']=None
                result=self.summarize(rows)
                self.assertEqual(result['averages'], [])
                self.assertFalse(any(r['eligible'] for r in result['cycles']))

    def test_unfinished_cycle_is_not_completed(self):
        self.assertTrue(callable(getattr(h, 'completed_weeks', None)))
        result=h.completed_weeks(self.cycle()[:30],now='2026-01-03T00:00:00Z')
        self.assertEqual(result['cycles'], [])
        self.assertEqual(result['averages'], [])


if __name__ == '__main__': unittest.main()
