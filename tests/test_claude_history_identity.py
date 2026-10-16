"""Authenticated Claude identity, separate from untrusted local account metadata."""
import json,sys,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).parents[1]))
import quota_providers.builtin as b

class Resp:
    def __init__(self,payload):self.data=json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n):return self.data[:n]

class ClaudeIdentityTests(unittest.TestCase):
    def test_identity_is_from_authenticated_account_and_organization(self):
        payload={'account':{'uuid':'11111111-1111-4111-8111-111111111111','email':'hidden@example.invalid'},'organization':{'uuid':'22222222-2222-4222-8222-222222222222'}}
        calls=[]
        def opener(req,timeout):
            calls.append((req.full_url,req.get_header('Authorization'),timeout));return Resp(payload)
        with mock.patch.object(b,'urlopen_no_redirect',side_effect=opener):
            first=b._claude_history_identity('synthetic-one',timeout=2)
            second=b._claude_history_identity('synthetic-rotated',timeout=2)
            self.assertRegex(first,r'^[a-f0-9]{64}$')
            self.assertEqual(first,second)
            payload['organization']['uuid']='33333333-3333-4333-8333-333333333333'
            self.assertNotEqual(first,b._claude_history_identity('synthetic-other',timeout=2))
        self.assertEqual(calls[0],('https://api.anthropic.com/api/oauth/profile','Bearer synthetic-one',2))
        self.assertNotIn('11111111',first)

    def test_opt_in_extra_account_binds_identity_to_the_usage_token(self):
        import tempfile
        from quota_providers.base import Deadline
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'.credentials.json').write_text(json.dumps({'claudeAiOauth':{'accessToken':'synthetic-usage-token'}}))
            entry={'id':'work','label':'Work','config_dir':tmp,'verify_history_identity':True}
            usage={'five_hour':{'utilization':20,'resets_at':'2026-01-02T00:00:00Z'}}
            with mock.patch.object(b,'_request_anthropic_usage',return_value=(usage,None)),mock.patch.object(b,'_claude_history_identity',return_value='a'*64) as identity:
                accounts=b._claude_account_results([entry],primary_token=None,deadline=Deadline(15))
                self.assertEqual(accounts[0].history_id,'a'*64)
                self.assertEqual(identity.call_args.args,('synthetic-usage-token',))
            entry.pop('verify_history_identity')
            with mock.patch.object(b,'_request_anthropic_usage',return_value=(usage,None)),mock.patch.object(b,'_claude_history_identity') as identity:
                accounts=b._claude_account_results([entry],primary_token=None,deadline=Deadline(15))
                identity.assert_not_called()
                self.assertIsNone(accounts[0].history_id)

    def test_opted_in_duplicate_primary_keeps_identity_without_second_usage_read(self):
        entry={'id':'personal','label':'Personal','config_dir':'/synthetic','verify_history_identity':True}
        usage={'five_hour':{'utilization':20,'resets_at':'2026-01-02T00:00:00Z'}}
        with mock.patch.object(b,'_core_anthropic_token',return_value='synthetic-primary'),mock.patch.object(b,'_core_anthropic_is_oauth',return_value=True),mock.patch.object(b,'_read_claude_account_token',return_value=('synthetic-primary',None)),mock.patch.object(b,'_request_anthropic_usage',return_value=(usage,None)) as fetch,mock.patch.object(b,'_claude_history_identity',return_value='b'*64) as identity:
            result=b._fetch_anthropic_multi([entry])
            self.assertEqual(result.history_id,'b'*64)
            self.assertEqual(result.accounts,[])
            fetch.assert_called_once()
            self.assertEqual(identity.call_args.args,('synthetic-primary',))

    def test_duplicate_primary_identity_remains_opt_in_and_failure_safe(self):
        entry={'id':'personal','label':'Personal','config_dir':'/synthetic'}
        usage={'five_hour':{'utilization':20,'resets_at':'2026-01-02T00:00:00Z'}}
        for opted_in,failed in ((False,False),(True,True)):
            entry['verify_history_identity']=opted_in
            with mock.patch.object(b,'_core_anthropic_token',return_value='synthetic-primary'),mock.patch.object(b,'_core_anthropic_is_oauth',return_value=True),mock.patch.object(b,'_read_claude_account_token',return_value=('synthetic-primary',None)),mock.patch.object(b,'_request_anthropic_usage',return_value=(None,'auth-failed') if failed else (usage,None)),mock.patch.object(b,'_claude_history_identity') as identity:
                result=b._fetch_anthropic_multi([entry])
                self.assertIsNone(result.history_id)
                self.assertEqual(result.accounts,[])
                identity.assert_not_called()

    def test_setting_requires_explicit_boolean_opt_in(self):
        import types
        config=types.ModuleType('hermes_cli.config')
        config.load_config_readonly=lambda:{'plugins':{'entries':{'quota':{'settings':{'claudeAccounts':[{'id':'work','configDir':'/synthetic','verifyHistoryIdentity':True}]}}}}}
        with mock.patch.dict(sys.modules,{'hermes_cli.config':config}):
            entries,_=b._claude_accounts_setting()
        self.assertIs(entries[0].get('verify_history_identity'),True)

    def test_missing_or_unavailable_profile_is_not_identity(self):
        for payload in ({},{'account':{'uuid':'x'}},{'account':{'uuid':'not-a-uuid'},'organization':{'uuid':'not-a-uuid'}}):
            with mock.patch.object(b,'urlopen_no_redirect',return_value=Resp(payload)):
                self.assertIsNone(b._claude_history_identity('synthetic',timeout=1))
        with mock.patch.object(b,'urlopen_no_redirect',side_effect=TimeoutError()):
            self.assertIsNone(b._claude_history_identity('synthetic',timeout=1))

if __name__=='__main__':unittest.main()
