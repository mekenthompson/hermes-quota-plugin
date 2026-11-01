"""Accounts tab of the desktop widget, rendered offline.

Runs the real `desktop/plugin.js` through `tests/widget_monitor_renderer.cjs` against a SYNTHETIC
`hermes quota monitor accounts` payload (labels work / home / spare, never real pool data) and drives
the tab's controller the way the UI does: drag -> drop -> confirm -> the CLI argv the gateway runs,
rollback when the write fails, keyboard reorder, strategy changes, paused writes on stale data, the
not-signed-in actions, the hide toggle and the en/tr bundles.
"""
import json
import re
import time
import unittest
from pathlib import Path

from test_widget_monitor import run, quota_payload, snapshot  # noqa: E402
from widget_harness import nodes, text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = time.time()
KEY = 'anthropic@default'


def accounts_payload(**over):
    rows = [
        {'id': 'a1a1a1', 'label': 'work', 'auth_type': 'oauth', 'source': 'manual', 'priority': 0, 'position': 0,
         'status': 'active', 'status_until': None, 'cooldown_models': [], 'tried_first': True,
         'windows': [{'label': '5h', 'used': 40, 'reset': NOW + 3600}], 'quota_match': 'label'},
        {'id': 'b2b2b2', 'label': 'home', 'auth_type': 'oauth', 'source': 'manual', 'priority': 1, 'position': 1,
         'status': 'exhausted', 'status_until': NOW + 1800, 'cooldown_models': [], 'tried_first': False,
         'windows': [], 'quota_match': None},
        {'id': 'c3c3c3', 'label': 'spare', 'auth_type': 'api_key', 'source': 'env:SYNTH', 'priority': 2,
         'position': 2, 'status': 'cooldown', 'status_until': NOW + 600, 'cooldown_models': ['claude-test-large'],
         'tried_first': False, 'windows': [], 'quota_match': None},
    ]
    data = {
        'monitor_version': 1, 'generated_at': NOW, 'active_profile': 'work', 'root_profile': 'default',
        'strategies': ['fill_first', 'round_robin', 'least_used', 'random'], 'default_strategy': 'fill_first',
        'pools': [
            {'provider': 'anthropic', 'owner': 'default', 'shared': True, 'used_by': ['default', 'work', 'home'],
             'strategies': {'default': 'fill_first', 'work': 'round_robin', 'home': 'fill_first'},
             'explicit_strategy': {'default': False, 'work': True, 'home': False}, 'rows': rows,
             'quota': {'windows': [], 'plan': None, 'unavailable': None, 'stale': False}, 'writable': True},
            {'provider': 'anthropic', 'owner': 'solo', 'shared': False, 'used_by': ['solo'],
             'strategies': {'solo': 'least_used'}, 'explicit_strategy': {'solo': True},
             'rows': [dict(rows[0], id='d4d4d4', label='solo-acc')], 'quota': None, 'writable': False},
        ],
        'profiles': [
            {'profile': 'default', 'is_root': True, 'model': {'provider': 'anthropic', 'model': 'claude-test-large'},
             'fallbacks': [{'provider': 'openrouter', 'model': 'test/other-model', 'custom_route': False}],
             'fallback_editable': False, 'fallback_command': 'hermes --profile default fallback',
             'strategies': {}, 'pools': {'anthropic': 'own'}},
            {'profile': 'work', 'is_root': False, 'model': {'provider': 'anthropic', 'model': 'claude-test-small'},
             'fallbacks': [], 'fallback_editable': False, 'fallback_command': 'hermes --profile work fallback',
             'strategies': {'anthropic': 'round_robin'}, 'pools': {'anthropic': 'shared'}},
            {'profile': 'home', 'is_root': False, 'model': {'provider': None, 'model': None}, 'fallbacks': [],
             'fallback_editable': False, 'fallback_command': 'hermes --profile home fallback',
             'strategies': {}, 'pools': {'anthropic': 'shared'}},
            {'profile': 'solo', 'is_root': False, 'model': {'provider': 'anthropic', 'model': 'claude-test-large'},
             'fallbacks': [], 'fallback_editable': False, 'fallback_command': 'hermes --profile solo fallback',
             'strategies': {'anthropic': 'least_used'}, 'pools': {'anthropic': 'own'}},
        ],
        'unavailable': [
            {'provider': 'nous', 'base': 'nous', 'reason': 'not-logged-in', 'kind': 'signin', 'retry_at': None,
             'settings_path': '/settings?tab=providers&pview=accounts', 'command': 'hermes auth add nous',
             'external_tool': None, 'optin_key': None, 'env': None},
            {'provider': 'openrouter', 'base': 'openrouter', 'reason': 'no-credentials', 'kind': 'signin',
             'retry_at': None, 'settings_path': '/settings?tab=providers&pview=keys',
             'command': 'hermes auth add openrouter', 'external_tool': None, 'optin_key': None, 'env': None},
            {'provider': 'copilot', 'base': 'copilot', 'reason': 'no-credentials', 'kind': 'signin',
             'retry_at': None, 'settings_path': None, 'command': 'gh auth login', 'external_tool': 'GitHub CLI',
             'optin_key': None, 'env': None},
            {'provider': 'grok', 'base': 'grok', 'reason': 'opt-in-disabled', 'kind': 'optin', 'retry_at': None,
             'settings_path': None, 'command': 'hermes config set plugins.entries.quota.settings.grokEnabled true',
             'external_tool': 'browser', 'optin_key': 'plugins.entries.quota.settings.grokEnabled', 'env': None},
            {'provider': 'gemini', 'base': 'gemini', 'reason': 'rate-limited', 'kind': 'rate_limited',
             'retry_at': NOW + 1500, 'settings_path': None, 'command': None, 'external_tool': 'Gemini CLI',
             'optin_key': None, 'env': None},
        ],
        'settings_paths': {}, 'capabilities': {'set_priority': True, 'set_strategy': True,
                                               'reorder_fallbacks': False, 'assign_account_to_profile': False},
        'errors': {}, 'recent_changes': [],
    }
    data.update(over)
    return data


SETUP = '''(() => {
  const saved = [];
  const ctl = createAccountsController();
  ctl.bind({data: DATA, scope: {connectionId: 'local', profile: 'work'}, flags: FLAGS, onSaved: (res, change) => saved.push(change.kind)});
  return {ctl, saved};
})()'''


def drive(steps, accounts=None, flags=None, **options):
    """Run `steps` (JS statements using `ctl`, may await) against a fresh controller; returns its outputs."""
    expr = ('(async () => { const {ctl, saved} = %s; let result = null, error = null;\n'
            'try { %s } catch (e) { error = String(e && e.message || e); }\n'
            'return {state: ctl.state, ids: ctl.ids(%r), saved, result, error}; })()'
            % (SETUP.replace('DATA', json.dumps(accounts or accounts_payload()))
               .replace('FLAGS', json.dumps(flags or {})), steps, KEY))
    return run(expr=expr, **options)


def find(tree, pred):
    return [n for n in nodes(tree) if pred(n)]


class AccountsControllerTests(unittest.TestCase):
    def test_drag_reorder_and_confirm_are_noops(self):
        out = drive('''
            result = [ctl.canReorder(KEY), ctl.dragStart(KEY, 'c3c3c3')];
            ctl.dragOver(KEY, 0, false);
            result.push(ctl.drop(KEY), ctl.move(KEY, 'b2b2b2', -1));
            result.push(await ctl.confirm());
        '''.replace('KEY', repr(KEY)))
        self.assertEqual(out['value']['result'], [False, False, None, None, None])
        self.assertEqual(out['value']['ids'], ['a1a1a1', 'b2b2b2', 'c3c3c3'])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['cli'], [])

    def test_drop_after_last_row_is_refused(self):
        out = drive('''ctl.dragStart(%r, 'a1a1a1'); ctl.dragOver(%r, 2, true); result = ctl.drop(%r);''' % (KEY, KEY, KEY))
        self.assertIsNone(out['value']['result'])
        self.assertEqual(out['value']['ids'], ['a1a1a1', 'b2b2b2', 'c3c3c3'])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['cli'], [])

    def test_drop_in_place_is_a_noop(self):
        out = drive('''ctl.dragStart(%r, 'b2b2b2'); ctl.dragOver(%r, 1, false); result = ctl.drop(%r);''' % (KEY, KEY, KEY))
        self.assertIsNone(out['value']['result'])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['cli'], [])

    def test_legacy_pending_changes_are_not_submitted(self):
        for kind in ('priority', 'strategy'):
            with self.subTest(kind=kind):
                out = drive('''
                    ctl.set({confirm: {kind: KIND, key: 'anthropic@default', to: 'random',
                        ids: ['c3c3c3', 'a1a1a1', 'b2b2b2'], argv: ['quota', 'monitor', 'set-priority']}});
                    result = await ctl.confirm();
                '''.replace('KIND', json.dumps(kind)), accounts=accounts_payload(
                    capabilities={'set_priority': True, 'set_strategy': True}))
                self.assertIsNone(out['value']['result'])
                self.assertEqual(out['cli'], [])
                self.assertEqual(out['value']['state']['orders'], {})
                self.assertEqual(out['value']['state']['strategies'], {})

    def test_keyboard_and_move_buttons_are_disabled(self):
        expr = '''(async () => {
          const {ctl} = %s;
          const pool = ctl.pool(%r);
          const tree = AccountRow({ctl, pool, row: pool.rows[0], index: 0, count: 3, firstActive: true, nowSec: Date.now() / 1000, page: false});
          let prevented = false;
          tree.props.onKeyDown({altKey: true, key: 'ArrowDown', preventDefault: () => { prevented = true; }});
          return {prevented, confirm: ctl.state.confirm, draggable: tree.props.draggable, row: tree};
        })()''' % (SETUP.replace('DATA', json.dumps(accounts_payload())).replace('FLAGS', '{}'), KEY)
        out = run(expr=expr)
        self.assertFalse(out['value']['prevented'])
        self.assertIsNone(out['value']['confirm'])
        self.assertFalse(out['value']['draggable'])
        buttons = find(out['value']['row'], lambda n: n.get('type') == 'button' and
                       str(n.get('props', {}).get('aria-label', '')).startswith('Move '))
        self.assertTrue(buttons)
        self.assertTrue(all(button['props']['disabled'] for button in buttons))

    def test_writes_paused_while_loading_or_stale(self):
        for flags, data in (({'isFetching': True}, accounts_payload()),
                            ({}, accounts_payload(generated_at=NOW - 3600))):
            with self.subTest(flags=flags):
                out = drive('''result = [ctl.dragStart(%r, 'a1a1a1'), ctl.move(%r, 'b2b2b2', -1),
                    ctl.proposeStrategy(ctl.pool(%r), 'home', 'random'), ctl.blocked()];''' % (KEY, KEY, KEY),
                    accounts=data, flags=flags)
                self.assertEqual(out['value']['result'][:3], [False, None, None])
                self.assertIn(out['value']['result'][3], ('loading', 'stale'))
                self.assertEqual(out['cli'], [])

    def test_unwritable_pool_cannot_be_dragged(self):
        out = drive('''result = ctl.dragStart('anthropic@solo', 'd4d4d4');''')
        self.assertFalse(out['value']['result'])

    def test_widget_hard_disables_account_mutations_even_when_backend_advertises_them(self):
        # A desktop widget can be newer than its gateway. A full/old backend's
        # write flags must never re-enable the trial's account mutation paths.
        data = accounts_payload(capabilities={'set_priority': True, 'set_strategy': True,
                                              'reorder_fallbacks': True,
                                              'assign_account_to_profile': True})
        out = drive('''
            result = [ctl.canReorder(KEY), ctl.dragStart(KEY, 'a1a1a1'), ctl.move(KEY, 'b2b2b2', -1),
                ctl.proposeStrategy(ctl.pool(KEY), 'home', 'round_robin')];
            ctl.set({confirm: {kind: 'priority', key: KEY, argv: ['quota', 'monitor', 'set-priority']}});
            result.push(await ctl.confirm());
        '''.replace('KEY', repr(KEY)), accounts=data)
        self.assertEqual(out['value']['result'], [False, False, None, None, None])
        self.assertEqual(out['cli'], [])
        self.assertEqual(out['value']['state']['orders'], {})
        self.assertEqual(out['value']['state']['strategies'], {})

    def test_account_write_transport_is_disabled_even_when_called_directly(self):
        out = run(expr='''(async () => {
            try {
                await runAccountsWrite(host.state.focusedSessionOwner.get(), ['quota', 'monitor', 'set-priority']);
                return 'unexpected success';
            } catch (e) { return String(e && e.message || e); }
        })()''')
        self.assertIn('disabled', out['value'].lower())
        self.assertEqual(out['cli'], [])

    def test_strategy_mutation_is_disabled_even_when_backend_advertises_it(self):
        data = accounts_payload(capabilities={'set_priority': True, 'set_strategy': True})
        out = drive('''result = [ctl.proposeStrategy(ctl.pool(KEY), 'home', 'round_robin'),
            ctl.strategyFor(ctl.pool(KEY), 'home')]; await ctl.confirm();'''.replace('KEY', repr(KEY)),
                    accounts=data)
        self.assertEqual(out['value']['result'], [None, 'fill_first'])
        self.assertIsNone(out['value']['state']['confirm'])
        self.assertEqual(out['cli'], [])

    def test_official_command_shown_in_the_dialog(self):
        out = run(expr='''[accountsOfficialCommand({kind: 'priority', owner: 'default', provider: 'anthropic', id: 'c3c3c3', to: 0}),
                           accountsOfficialCommand({kind: 'strategy', profile: 'home', provider: 'custom:a.b', to: 'random'})]''')
        self.assertEqual(out['value'], ['hermes --profile default auth priority anthropic c3c3c3 0',
                                        'hermes --profile home config set credential_pool_strategies.custom:a\\.b random'])


class AccountsViewTests(unittest.TestCase):
    def view(self, state=None, locale='en', data=None, page=False):
        init = 'null'
        if state:
            init = '(() => { const c = createAccountsController(); c.bind({data: __DATA__, flags: {}}); %s; return c.state; })()' % state
        expr = '''AccountsView({page: %s, initialState: %s, accountsOverride: {data: __DATA__, scope: {connectionId: 'local', profile: 'work'}, isFetching: false, isLoading: false}})''' % (
            'true' if page else 'false', init)
        return run(expr=expr.replace('__DATA__', json.dumps(data or accounts_payload())), locale=locale)

    def test_pools_rows_badges_and_shared_warning(self):
        out = self.view()
        tree = out['value']
        s = text(tree)
        for expected in ('Credential pools', 'shared · 3 profiles', 'own pool · solo', 'tried first', 'OAuth', 'API key',
                         'Shared pool of “default”: reordering also changes work, home.', 'Not signed in'):
            self.assertIn(expected, s)
        statuses = [n['props']['data-account-status'] for n in find(tree, lambda n: 'data-account-status' in n.get('props', {}))]
        self.assertEqual(statuses[:3], ['active', 'exhausted', 'cooldown'])
        rows = find(tree, lambda n: 'data-account-row' in n.get('props', {}))
        self.assertEqual([r['props']['data-account-row'] for r in rows], ['a1a1a1', 'b2b2b2', 'c3c3c3', 'd4d4d4'])
        self.assertEqual([r['props']['draggable'] for r in rows], [False, False, False, False])
        move_buttons = find(tree, lambda n: n.get('type') == 'button' and
                            str(n.get('props', {}).get('aria-label', '')).startswith('Move '))
        self.assertTrue(move_buttons)
        self.assertTrue(all(button['props']['disabled'] for button in move_buttons))
        strategies = find(tree, lambda n: n.get('props', {}).get('aria-disabled') is True)
        self.assertTrue(strategies)
        self.assertIn('Read-only: account order cannot be changed from this widget.', s)
        self.assertEqual(len(find(tree, lambda n: n.get('props', {}).get('data-tried-first'))), 2)
        dialog = find(tree, lambda n: n.get('type') == 'ConfirmDialog')[0]
        self.assertFalse(dialog['props']['open'])

    def test_empty_pools_with_unavailable_metadata_do_not_claim_no_credentials(self):
        data = accounts_payload(pools=[], metadata={
            'available': False, 'reason': 'no-supported-sanitized-host-api'})
        s = text(self.view(data=data)['value'])
        self.assertIn('Credential-pool metadata is unavailable', s)
        self.assertNotIn('No pooled credentials', s)
        self.assertNotIn('0 pools', s)

    def test_drag_is_disabled_even_with_writable_pool_flags(self):
        tree = self.view(state="c.dragStart('%s', 'c3c3c3'); c.dragOver('%s', 0, false)" % (KEY, KEY))['value']
        lines = {n['props']['data-account-row']: n['props'].get('data-drop-line')
                 for n in find(tree, lambda n: 'data-account-row' in n.get('props', {}))}
        self.assertTrue(all(value is None for value in lines.values()))
        self.assertTrue(all(not n['props']['draggable'] for n in find(
            tree, lambda n: 'data-account-row' in n.get('props', {}))))

    def test_backend_capabilities_cannot_open_mutation_confirmation(self):
        tree = self.view(state="c.propose('%s', 'c3c3c3', 0)" % KEY)['value']
        dialog = find(tree, lambda n: n.get('type') == 'ConfirmDialog')[0]
        self.assertFalse(dialog['props']['open'])
        rows = [n['props']['data-account-row'] for n in find(tree, lambda n: 'data-account-row' in n.get('props', {}))]
        self.assertEqual(rows[:3], ['a1a1a1', 'b2b2b2', 'c3c3c3'])

    def test_profile_matrix_and_fallbacks_are_read_only(self):
        tree = self.view(page=True)['value']
        table = find(tree, lambda n: n.get('props', {}).get('data-profile-matrix'))
        self.assertTrue(table)
        s = text(tree)
        self.assertIn('Fallback order is read-only here', s)
        self.assertIn('test/other-model', s)
        cmds = [n['props']['data-fallback-command'] for n in find(tree, lambda n: 'data-fallback-command' in n.get('props', {}))]
        self.assertEqual(cmds, ['hermes --profile default fallback', 'hermes --profile work fallback',
                                'hermes --profile home fallback', 'hermes --profile solo fallback'])
        self.assertIn('no per-profile account assignment', s)

    def test_not_signed_in_actions(self):
        expr = '''(async () => {
          const out = {};
          for (const item of __DATA__.unavailable) {
            const tree = UnavailableRow({item, nowSec: Date.now() / 1000});
            const flat = [];
            const walk = (n) => { if (Array.isArray(n)) n.forEach(walk); else if (n && typeof n === 'object') { flat.push(n); walk(n.props && n.props.children); } };
            walk(tree);
            const signin = flat.find(n => n.props && n.props['data-signin']);
            const copy = flat.find(n => n.props && n.props['data-copy-command']);
            if (signin) signin.props.onClick();
            if (copy) copy.props.onClick();
            out[item.provider] = {signin: signin ? signin.props.children : null, copy: copy ? copy.props['data-copy-command'] : null};
          }
          return out;
        })()'''.replace('__DATA__', json.dumps(accounts_payload()))
        out = run(expr=expr)
        v = out['value']
        self.assertEqual(v['nous'], {'signin': 'Sign in', 'copy': 'hermes auth add nous'})
        self.assertEqual(v['openrouter'], {'signin': 'Add API key', 'copy': 'hermes auth add openrouter'})
        self.assertEqual(v['copilot'], {'signin': None, 'copy': 'gh auth login'})
        self.assertEqual(v['grok'], {'signin': None, 'copy': 'hermes config set plugins.entries.quota.settings.grokEnabled true'})
        self.assertEqual(v['gemini'], {'signin': None, 'copy': None})  # rate-limited: wait, no sign-in
        self.assertEqual(out['navigated'], ['/settings?tab=providers&pview=accounts', '/settings?tab=providers&pview=keys'])
        self.assertEqual(out['clipboard'], ['hermes auth add nous', 'hermes auth add openrouter', 'gh auth login',
                                            'hermes config set plugins.entries.quota.settings.grokEnabled true'])
        s = text(self.view()['value'])
        self.assertRegex(s, r'rate-limiting quota reads\. No sign-in needed\. · retry in (24|25)m')
        self.assertIn('No API key found (no-credentials).', s)

    def test_turkish(self):
        s = text(self.view(locale='tr', state="c.propose('%s', 'c3c3c3', 0)" % KEY)['value'])
        for expected in ('Kimlik havuzları', 'paylaşılan · 3 profil', 'ilk denenen', 'Giriş yapılmamış', 'Giriş yap'):
            self.assertIn(expected, s)


class AccountsTabWiringTests(unittest.TestCase):
    def test_tab_routes_to_accounts_and_can_be_hidden(self):
        out = run(component='pane', data=quota_payload(), monitor=snapshot(), accounts=accounts_payload(),
                  storage={'paneView': 'accounts'})
        self.assertTrue(find(out['tree'], lambda n: n.get('props', {}).get('data-monitor-view') == 'accounts'))
        self.assertTrue(any(q['key'][2] == 'accounts' and q['enabled'] for q in out['queries']))
        hidden = run(component='pane', data=quota_payload(), monitor=snapshot(), accounts=accounts_payload(),
                     storage={'paneView': 'accounts', 'accountsTab': False})
        tabs = [n['props']['data-tab'] for n in find(hidden['tree'], lambda n: 'data-tab' in n.get('props', {}))]
        self.assertNotIn('accounts', tabs)
        self.assertFalse(find(hidden['tree'], lambda n: n.get('props', {}).get('data-monitor-view') == 'accounts'))
        settings = text(run(component='settings', data=quota_payload())['tree'])
        self.assertIn('Accounts tab', settings)

    def test_bundles_have_every_accounts_key(self):
        src = (ROOT / 'desktop' / 'plugin.js').read_text()
        en = src[src.index('const I18N_EN = {'):src.index('const I18N_TR = {')]
        tr = src[src.index('const I18N_TR = {'):]
        keys = set(re.findall(r'\n\t((?:accounts|strategy)[A-Za-z_]*|tab_accounts):', en))
        self.assertGreater(len(keys), 80)
        missing = [k for k in keys if not re.search(r'\n\t%s:' % re.escape(k), tr)]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
