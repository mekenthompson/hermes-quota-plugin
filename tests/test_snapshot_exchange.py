import importlib,sys,types,tempfile,json,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).parents[1]
pkg=types.ModuleType('quota_exchange_test');pkg.__path__=[str(ROOT)];sys.modules['quota_exchange_test']=pkg
constants=types.ModuleType('hermes_constants');setattr(constants,'get_hermes_home',lambda:Path('/unused'))
with mock.patch.dict(sys.modules,{'hermes_constants':constants}):
    cache=importlib.import_module('quota_exchange_test.quota_cache')
    history=importlib.import_module('quota_exchange_test.quota_history')

class ExchangeTest(unittest.TestCase):
    def setUp(self):
        self.modules=mock.patch.dict(sys.modules,{'quota_exchange_test':pkg,'quota_exchange_test.quota_cache':cache,'quota_exchange_test.quota_history':history})
        self.modules.start()
    def tearDown(self):
        self.modules.stop()
    def test_provider_sample_does_not_poll_other_accounts_or_write_cache(self):
        from quota_exchange_test.quota_providers import QuotaResult,QuotaWindow
        one=mock.Mock(return_value=QuotaResult(label='grok',windows=[QuotaWindow(label='week',used_percent=20)]))
        other=mock.Mock()
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(cache,'_cache_path',return_value=str(Path(tmp)/'cache.json')),mock.patch.object(cache,'PROVIDER_FETCHERS',{'grok':one,'other':other}):
            result=cache.refresh_quota_cache(provider_ids=['grok'],persist=False)
            self.assertEqual(list(result['providers']),['grok']);one.assert_called_once();other.assert_not_called()
            self.assertFalse(Path(tmp,'cache.json').exists())
            with self.assertRaises(ValueError):cache.refresh_quota_cache(provider_ids=['not-a-provider'],persist=False)
    def test_cli_sample_and_ingest_do_not_reintroduce_private_fields(self):
        import argparse,contextlib,io
        with mock.patch.dict(sys.modules,{'hermes_constants':constants}):
            commands=importlib.import_module('quota_exchange_test.commands')
        parser=argparse.ArgumentParser();commands.setup_argparse(parser)
        sample={'fetched_at':'2026-01-01T00:00:00Z','providers':{'grok':{'label':'Shared','access_token':'must-not-leak','windows':[]}}}
        out=io.StringIO()
        with mock.patch.object(commands,'refresh_quota_cache',return_value=sample) as refresh,contextlib.redirect_stdout(out):
            commands._handle_cli(parser.parse_args(['sample','--provider','grok']))
        self.assertNotIn('must-not-leak',out.getvalue())
        refresh.assert_called_once_with(provider_ids=['grok'],persist=False)
        with mock.patch.object(sys,'stdin',io.StringIO(out.getvalue())),mock.patch.object(cache,'ingest_snapshot') as ingest:
            commands._handle_cli(parser.parse_args(['ingest']))
            self.assertEqual(ingest.call_args.args[0]['providers']['grok']['label'],'Shared')

    def test_exchange_strips_private_fields_and_scrubs_errors(self):
        raw={'fetched_at':'2026-01-01T00:00:00Z','providers':{'grok':{'history_id':'a'*64,'label':'Shared','access_token':'hidden','details':['hidden'],'raw':{'private':'hidden'},'unavailable_reason':'Bearer hidden','windows':[{'label':'week','used_percent':20}]}}}
        clean=history.sanitize_snapshot(raw)
        self.assertNotIn('hidden',json.dumps(clean))
        self.assertEqual(clean['providers']['grok']['unavailable_reason'],'fetch-error')
        self.assertEqual(clean['providers']['grok']['windows'],[])
    def test_ingest_validates_then_records_and_updates_display_cache(self):
        raw={'fetched_at':'2026-01-01T00:00:00Z','providers':{'grok':{'history_id':'a'*64,'label':'Shared','windows':[{'label':'week','used_percent':20,'reset_at':'2026-01-03T00:00:00Z'}]}}}
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(history,'_path',return_value=Path(tmp)/'history.sqlite3'),mock.patch.object(cache,'_cache_path',return_value=str(Path(tmp)/'cache.json')):
            cache.ingest_snapshot(raw)
            self.assertEqual(history.read_history()[0]['used_percent'],20)
            self.assertEqual(cache.read_quota_cache()['providers']['grok']['history_id'],'a'*64)
            with self.assertRaises(ValueError):cache.ingest_snapshot({'fetched_at':'bad','providers':{}})
            self.assertEqual(len(history.read_history()),1)

if __name__=='__main__':unittest.main()
