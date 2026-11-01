import importlib,sys,types,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).parents[1]
pkg=types.ModuleType('managed_quota_test');pkg.__path__=[str(ROOT)];sys.modules['managed_quota_test']=pkg
constants=types.ModuleType('hermes_constants');constants.get_hermes_home=lambda:Path('/unused')
with mock.patch.dict(sys.modules,{'hermes_constants':constants}):
    cache=importlib.import_module('managed_quota_test.quota_cache')

class ManagedViewTest(unittest.TestCase):
    def test_managed_refresh_never_polls_or_replaces_combined_cache(self):
        with mock.patch.object(cache,'managed_collection_enabled',return_value=True),mock.patch.object(cache,'_fetch_one') as fetch,mock.patch.object(cache,'_cache_path') as path:
            with self.assertRaisesRegex(ValueError,'collector-managed'):
                cache.refresh_quota_cache()
            fetch.assert_not_called();path.assert_not_called()
    def test_managed_source_sampling_remains_allowed(self):
        with mock.patch.object(cache,'managed_collection_enabled',return_value=True),mock.patch.object(cache,'PROVIDER_FETCHERS',{'grok':lambda:cache.QuotaResult(label='Shared')}):
            result=cache.refresh_quota_cache(provider_ids=['grok'],persist=False)
            self.assertIn('grok',result['providers'])
            self.assertIsNone(result['providers']['grok']['unavailable_reason'])
    def test_dashboard_reload_reads_managed_cache_without_vendor_refresh(self):
        fastapi=types.ModuleType('fastapi')
        class Router:
            def get(self,path):return lambda fn:fn
            post=get
        fastapi.APIRouter=Router
        with mock.patch.dict(sys.modules,{'fastapi':fastapi}):
            api=importlib.import_module('managed_quota_test.dashboard.plugin_api')
        fake=types.SimpleNamespace(managed_collection_enabled=lambda:True,read_quota_cache=lambda:{'providers':{}},refresh_quota_cache=mock.Mock())
        with mock.patch.object(api,'_cache',return_value=fake):
            result=api.quota_refresh()
        self.assertIs(result['managed_collection'],True)
        fake.refresh_quota_cache.assert_not_called()

    def test_only_explicit_boolean_enables_managed_mode(self):
        config=types.ModuleType('hermes_cli.config')
        for value,expected in ((True,True),(False,False),('true',False),(None,False)):
            config.load_config_readonly=lambda:{'plugins':{'entries':{'quota':{'settings':{'managedCollection':value}}}}}
            with mock.patch.dict(sys.modules,{'hermes_cli.config':config}):
                self.assertIs(cache.managed_collection_enabled(),expected)

if __name__=='__main__':unittest.main()
