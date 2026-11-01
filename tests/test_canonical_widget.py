import unittest
from widget_harness import text
from test_widget_monitor import quota_payload, run


class CanonicalWidgetTests(unittest.TestCase):
    def test_shared_history_is_visible_without_agent_monitor(self):
        data=quota_payload()
        data['managed_collection']=True
        data['canonical_history']={'forecasts':[{'label':'Personal','window':'Weekly','status':'insufficient-data'}],
            'weeks': {'averages':[], 'cycles':[{'provider':'anthropic', 'window':'Weekly','coverage':0.4,'eligible':False,'partial':True,'saturated':False,'discontinuity':True,'reset_at':'2026-01-08'}]}}
        out=run(component='pane', data=data, storage={'monitorEnabled':False})
        body=text(out['tree'])
        self.assertIn('Shared-account history',body)
        self.assertIn('insufficient-data',body)
        self.assertIn('40% coverage',body)
        self.assertIn('partial',body)
        self.assertIn('No eligible completed weeks',body)


if __name__ == '__main__': unittest.main()
