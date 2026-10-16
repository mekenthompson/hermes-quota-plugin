"""Offline period forecasts: synthetic fixtures, never provider credentials."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('forecast_history',Path(__file__).parents[1]/'quota_history.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

def row(hour,used=20,**kw):
    r=dict(fetched_at=f'2026-01-01T{hour:02d}:00:00+00:00',provider='grok',account='a'*64,label='Shared',window='Weekly',reset_at='2026-01-03T00:00:00+00:00',used_percent=used,status='ok',identity_verified=1)
    r.update(kw);return r

class ForecastTest(unittest.TestCase):
    def test_rate_and_projection_are_account_level(self):
        f=h.forecasts([row(0,20),row(1,22),row(2,24)],now='2026-01-01T02:00:00+00:00')[0]
        self.assertEqual(f['status'],'exhausts-before-reset')
        self.assertEqual(f['remaining_percent'],76)
        self.assertEqual(f['rate_percentage_points_per_hour'],2)
        self.assertEqual(f['projected_used_percent_at_reset'],116)
        self.assertEqual(f['estimated_exhaustion_at'],'2026-01-02T16:00:00+00:00')

    def test_unknown_observations_do_not_flood_account_cards(self):
        rows=[row(0,20,account='unverified:0',identity_verified=0),row(1,22,account='unverified:1',identity_verified=0),row(2,24,account='unverified:2',identity_verified=0)]
        values=h.forecasts(rows,now='2026-01-01T02:00:00+00:00')
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]['status'],'unknown-account')
        self.assertIsNone(values[0]['rate_percentage_points_per_hour'])

    def test_discontinuities_never_join_old_rates(self):
        for middle in (row(1,22,status='timeout',used_percent=None),row(1,22,reset_at=''),row(1,22,reset_at='2026-01-04T00:00:00+00:00'),row(1,10),row(1,22,account='b'*64)):
            with self.subTest(middle=middle):
                out=h.forecasts([row(0,20),middle,row(2,24)],now='2026-01-01T02:00:00+00:00')
                current=[f for f in out if f['fetched_at']==row(2)['fetched_at']][0]
                self.assertEqual(current['status'],'insufficient-data')
                self.assertIsNone(current['projected_used_percent_at_reset'])

    def test_stale_reset_future_and_zero_burn_are_explicit(self):
        rows=[row(0,20),row(1,20),row(2,20)]
        for now,status in [('2026-01-01T02:00:00+00:00','no-observed-burn'),('2026-01-01T05:00:00+00:00','stale'),('2026-01-01T01:00:00+00:00','future-observation')]:
            self.assertEqual(h.forecasts(rows,now=now)[0]['status'],status)
        self.assertEqual(h.forecasts([row(2,20,reset_at='2026-01-01T01:00:00+00:00')],now='2026-01-01T02:00:00+00:00')[0]['status'],'reset-due')

    def test_cli_and_dashboard_expose_same_cached_forecast(self):
        import argparse,contextlib,io,json,sys,types
        from unittest import mock
        pkg=types.ModuleType('forecast_plugin');pkg.__path__=[str(Path(__file__).parents[1])]
        constants=types.ModuleType('hermes_constants');constants.get_hermes_home=lambda:Path('/unused-test-home')
        with mock.patch.dict(sys.modules,{'forecast_plugin':pkg,'hermes_constants':constants}):
            commands=__import__('forecast_plugin.commands',fromlist=['_handle_cli'])
            history=__import__('forecast_plugin.quota_history',fromlist=['forecasts'])
            parser=argparse.ArgumentParser();commands.setup_argparse(parser)
            expected=[{'status':'insufficient-data','provider':'grok'}]
            out=io.StringIO()
            with mock.patch.object(history,'read_history',return_value=[]),mock.patch.object(history,'forecasts',return_value=expected),contextlib.redirect_stdout(out):
                commands._handle_cli(parser.parse_args(['forecast','--json']))
                self.assertEqual(json.loads(out.getvalue())['forecasts'],expected)
                fastapi=types.ModuleType('fastapi')
                class Router:
                    def get(self,path):return lambda fn:fn
                    post=get
                fastapi.APIRouter=Router;fastapi.HTTPException=Exception
                with mock.patch.dict(sys.modules,{'fastapi':fastapi}):
                    api=__import__('forecast_plugin.dashboard.plugin_api',fromlist=['quota_forecast'])
                    with mock.patch.object(api,'_cache',return_value=types.SimpleNamespace(__package__='forecast_plugin')):
                        self.assertEqual(api.quota_forecast()['forecasts'],expected)

    def test_distinct_accounts_are_not_summed(self):
        rows=[row(i,20+i) for i in range(3)]+[row(i,70+i,account='b'*64) for i in range(3)]
        values=h.forecasts(rows,now='2026-01-01T02:00:00+00:00')
        self.assertEqual(len(values),2)
        self.assertEqual([f['used_percent'] for f in values],[22,72])
        self.assertEqual([f['rate_percentage_points_per_hour'] for f in values],[1,1])

if __name__=='__main__':unittest.main()
