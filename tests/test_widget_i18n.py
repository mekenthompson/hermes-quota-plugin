"""Every app language for the desktop widget: bundles, formatting, direction.

Runs the real `desktop/plugin.js` through `tests/widget_monitor_renderer.cjs`
(TZ=UTC) against the same SYNTHETIC payloads as the monitor/accounts tests.

- Each bundle (the Hermes app languages + the `tr` backend pack) carries every
  en key, functions where en has functions, and never an empty string.
- Rendered views (Quota, Live, Usage, Health, Kanban, Accounts, Settings,
  status bar) show no raw key in any language.
- Dates, numbers, money, percentages and durations follow the APP language,
  not the OS locale; Arabic roots render right-to-left.
- The registered-once sidebar label is rebuilt on a locale change.
"""
import json
import re
import unittest
from pathlib import Path

from test_widget_accounts import accounts_payload  # noqa: E402
from test_widget_monitor import NOW, quota_payload, run, snapshot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Bundled app languages (apps/desktop/src/i18n/languages.ts) + the tr pack.
LOCALES = ['en', 'tr', 'de', 'es', 'fr', 'ru', 'ja', 'zh', 'zh-hant', 'ar']


def strings(tree, out=None):
    """Every user-visible string in a rendered tree (children + text props)."""
    out = [] if out is None else out
    if isinstance(tree, dict):
        props = tree.get('props', {}) or {}
        for key in ('title', 'aria-label', 'label', 'placeholder', 'tip', 'description'):
            v = props.get(key)
            if isinstance(v, str):
                out.append(v)
        for v in props.values():
            if isinstance(v, (dict, list)):
                strings(v, out)
        strings(props.get('children'), out)
    elif isinstance(tree, list):
        for v in tree:
            strings(v, out)
    elif isinstance(tree, str):
        out.append(tree)
    return out


class BundleParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run(expr='''(() => {
          const en = I18N_EN;
          const samples = [[1, "X", "Y", "Z"], [2, "a", "b", "c"], [5, 7, 9, 11], [21, "a", "b"], [0, "", ""]];
          const out = {locales: Object.keys(I18N_BUNDLES), bundles: {}};
          for (const [id, b] of Object.entries(I18N_BUNDLES)) {
            const r = {missing: [], extra: [], kind: [], empty: [], threw: [], locale: b._locale};
            for (const k of Object.keys(en)) {
              if (!(k in b)) { r.missing.push(k); continue; }
              if ((typeof en[k] === "function") !== (typeof b[k] === "function")) r.kind.push(k);
            }
            for (const [k, v] of Object.entries(b)) {
              if (!(k in en)) r.extra.push(k);
              if (typeof v === "string") { if (!v.trim()) r.empty.push(k); continue; }
              if (typeof v !== "function") { r.kind.push(k); continue; }
              for (const args of samples) {
                try {
                  const s = v(...args);
                  if (typeof s !== "string" || !s.trim() || /undefined|\\[object|NaN/.test(s)) r.empty.push(k + JSON.stringify(args));
                } catch (e) { r.threw.push(k + ": " + e.message); }
              }
            }
            out.bundles[id] = r;
          }
          return out;
        })()''')['value']

    def test_every_app_language_is_registered(self):
        self.assertEqual(sorted(self.report['locales']), sorted(LOCALES))

    def test_every_bundle_has_every_key_with_the_same_kind(self):
        for loc in LOCALES:
            with self.subTest(locale=loc):
                r = self.report['bundles'][loc]
                self.assertEqual(r['locale'], loc)
                self.assertEqual((r['missing'], r['extra'], r['kind']), ([], [], []))

    def test_no_empty_strings_and_functions_render(self):
        for loc in LOCALES:
            with self.subTest(locale=loc):
                r = self.report['bundles'][loc]
                self.assertEqual((r['empty'], r['threw']), ([], []))

    def test_translated_bundles_differ_from_english(self):
        """A copy-pasted English bundle would pass parity; most strings must change."""
        out = run(expr='''Object.fromEntries(Object.entries(I18N_BUNDLES).map(([id, b]) => [id,
          Object.keys(I18N_EN).filter((k) => typeof I18N_EN[k] === "string" && b[k] === I18N_EN[k]).length]))''')['value']
        total = run(expr='Object.values(I18N_EN).filter((v) => typeof v === "string").length')['value']
        for loc in LOCALES[1:]:
            with self.subTest(locale=loc):
                self.assertLess(out[loc], total * 0.2, out[loc])

    def test_registration_passes_all_bundles(self):
        out = run(expr='Object.keys(TEST_CTX_BUNDLES())'.replace('TEST_CTX_BUNDLES()', 'I18N_BUNDLES'))
        self.assertEqual(sorted(out['value']), sorted(LOCALES))
        src = (ROOT / 'desktop' / 'plugin.js').read_text(encoding='utf-8')
        self.assertIn('ctx.i18n.register(I18N_BUNDLES)', src)


class NoRawKeysTests(unittest.TestCase):
    """A missing translation falls back to en, a missing en key shows the key itself."""

    @classmethod
    def setUpClass(cls):
        cls.keys = set(run(expr='Object.keys(I18N_EN)')['value'])
        cls.key_re = re.compile(r'\b(%s)\b' % '|'.join(sorted((re.escape(k) for k in cls.keys if re.search(r'[A-Z_]', k) and len(k) > 5), key=len, reverse=True)))

    def assert_no_keys(self, tree, where):
        for s in strings(tree):
            hit = self.key_re.search(s)
            self.assertIsNone(hit, '%s: raw key %r in %r' % (where, hit and hit.group(0), s))

    def test_views_in_every_language(self):
        data, mon = quota_payload(), snapshot()
        for loc in LOCALES:
            for view in ('quota', 'live', 'usage', 'health', 'kanban'):
                with self.subTest(locale=loc, view=view):
                    out = run(component='pane', data=data, monitor=mon, storage={'paneView': view}, locale=loc)
                    self.assert_no_keys(out['tree'], '%s/%s' % (loc, view))
            for component in ('settings', 'status', 'agents', 'cost'):
                with self.subTest(locale=loc, view=component):
                    out = run(component=component, data=data, monitor=mon, locale=loc,
                              storage={'budgetDaily': 5})
                    self.assert_no_keys(out['tree'], '%s/%s' % (loc, component))

    def test_accounts_view_in_every_language(self):
        for loc in LOCALES:
            with self.subTest(locale=loc):
                expr = '''AccountsView({page: true, initialState: null, accountsOverride: {data: __DATA__, scope: {connectionId: 'local', profile: 'work'}, isFetching: false, isLoading: false}})'''
                out = run(expr=expr.replace('__DATA__', json.dumps(accounts_payload())), locale=loc)
                self.assert_no_keys(out['value'], 'accounts/' + loc)

    def test_status_tooltips_are_translated(self):
        out = run(expr='''[makeProviderTip("x", {windows: [{label: "5h", used_percent: 40, reset_at: new Date(Date.now() + 3 * 3600e3 + 12 * 60e3 + 5e3).toISOString()}], plan: "pro"}),
                          makeProviderTip("y", {unavailable_reason: "no-credentials"}), codexValue(0), codexValue(null)]''', locale='de')
        tip, unavailable, locked, unknown = out['value']
        self.assertIn('Tarif: pro', tip)
        self.assertIn('60\u00a0% übrig', tip)
        self.assertIn('Reset 3\u00a0Std. 12\u00a0Min.', tip)
        self.assertIn('nicht verfügbar (no-credentials)', unavailable)
        self.assertEqual((locked, unknown), ('Limit erreicht', 'unbekannt'))


class LocaleFormattingTests(unittest.TestCase):
    """The APP language picks the format (TZ=UTC, LANG=en_US for the process)."""

    STAMP = '2026-10-22T12:27:49Z'

    def fmt(self, locale, expr):
        return run(expr=expr, locale=locale)['value']

    def test_turkish_date_uses_turkish_month(self):
        s = self.fmt('tr', 'checkedDateTime(%r)' % self.STAMP)
        self.assertIn('Eki', s)
        self.assertNotIn('Oct', s)
        self.assertIn('12:27:49', s)

    def test_english_keeps_english(self):
        s = self.fmt('en', 'checkedDateTime(%r)' % self.STAMP)
        self.assertIn('Oct', s)

    def test_german_numbers_use_comma_decimals(self):
        out = self.fmt('de', '[fmtNum(1.5, 1), fmtUsd(1234.5), fmtUsd(12.5), fmtPct(42), fmtTokens(1234567), fmtSeconds(4.25), fmtUsdShort(12345)]')
        num, big, small, pct, tokens, secs, short = out
        self.assertEqual(num, '1,5')
        self.assertEqual(big.replace('\u00a0', ' '), '1.235 $')
        self.assertEqual(small.replace('\u00a0', ' '), '12,50 $')
        self.assertEqual(pct.replace('\u00a0', ' '), '42 %')
        self.assertIn('1,2', tokens)
        self.assertIn('Mio', tokens)
        self.assertEqual(secs, '4,3\u00a0s')
        self.assertIn('12', short)
        self.assertIn('$', short)

    def test_turkish_percent_and_money(self):
        pct, money = self.fmt('tr', '[fmtPct(42), fmtUsd(1234.5)]')
        self.assertEqual(pct, '%42')
        self.assertEqual(money, '$1.235')

    def test_english_formats_are_unchanged(self):
        out = self.fmt('en', '[fmtNum(1.5, 1), fmtUsd(1234.5), fmtUsd(12.5), fmtPct(42), fmtTokens(1234567), fmtTokens(12345), fmtUsdShort(12345), fmtSpan(3 * 3600 + 12 * 60)]')
        self.assertEqual(out, ['1.5', '$1,235', '$12.50', '42%', '1.2M', '12k', '$12k', '3h 12m'])

    def test_cjk_dates_put_the_year_first(self):
        for loc in ('ja', 'zh', 'zh-hant'):
            with self.subTest(locale=loc):
                s = self.fmt(loc, 'checkedDateTime(%r)' % self.STAMP)
                self.assertRegex(s, r'^2026\D+10\D+22')

    def test_durations_follow_the_language(self):
        expr = 'relativeCountdown(new Date(Date.now() + (26 * 3600 + 30) * 1000).toISOString())'
        expected = {'en': '1d 2h', 'tr': '1g 2sa', 'de': '1\u00a0T. 2\u00a0Std.', 'ja': '1日2時間', 'zh': '1天2小时', 'ru': '1\u00a0д 2\u00a0ч'}
        for loc, want in expected.items():
            with self.subTest(locale=loc):
                self.assertEqual(self.fmt(loc, expr), want)

    def test_absolute_reset_today_and_tomorrow_are_translated(self):
        expr = '[absoluteReset(new Date(Date.now() + 60e3).toISOString()), absoluteReset(new Date(Date.now() + 86400e3).toISOString())]'
        out = self.fmt('de', expr)
        # Near UTC midnight "+1 min" can already be tomorrow; one of the words is enough.
        self.assertTrue(any(s.startswith(('heute ', 'morgen ')) for s in out), out)
        self.assertTrue(out[1].startswith('morgen ') or not out[1].startswith('tomorrow'), out)

    def test_arabic_uses_western_digits(self):
        out = self.fmt('ar', '[fmtNum(1234.5, 1), checkedDateTime(%r)]' % self.STAMP)
        for s in out:
            self.assertIsNone(re.search('[\u0660-\u0669]', s), s)
        self.assertIn('2026', out[1])

    def test_weekday_and_clock_labels_follow_the_language(self):
        out = run(component='pane', data=quota_payload(), monitor=snapshot(), storage={'paneView': 'usage'}, locale='de')
        found = set(strings(out['tree'])) & {'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So', 'Mo.', 'Di.', 'Mi.', 'Do.', 'Fr.', 'Sa.', 'So.'}
        self.assertGreaterEqual(len(found), 5, 'German weekday labels missing')
        self.assertFalse(set(strings(out['tree'])) & {'Mon', 'Tue', 'Wed', 'Thu', 'Fri'}, 'English weekday labels')


class DirectionTests(unittest.TestCase):
    def root(self, locale):
        return run(component='pane', data=quota_payload(), monitor=snapshot(), locale=locale)['tree']

    def test_arabic_root_is_rtl(self):
        tree = self.root('ar')
        self.assertEqual(tree['props'].get('dir'), 'rtl')
        self.assertTrue(tree['props'].get('lang', '').startswith('ar'))

    def test_other_roots_are_ltr(self):
        for loc in ('en', 'tr', 'de', 'ja'):
            with self.subTest(locale=loc):
                self.assertEqual(self.root(loc)['props'].get('dir'), 'ltr')

    def test_arrows_and_stripes_mirror(self):
        rtl = run(expr='[arrowFwd(), isRtl(), localeDir()]', locale='ar')['value']
        ltr = run(expr='[arrowFwd(), isRtl(), localeDir()]', locale='en')['value']
        self.assertEqual(rtl, ['←', True, 'rtl'])
        self.assertEqual(ltr, ['→', False, 'ltr'])

    def test_no_physical_left_right_in_layout_styles(self):
        src = (ROOT / 'desktop' / 'plugin.js').read_text(encoding='utf-8')
        body = src[:src.index('const I18N_EN = {')]
        for prop in ('marginLeft', 'marginRight', 'paddingLeft', 'borderLeft', 'textAlign: "left"', 'textAlign: "right"'):
            with self.subTest(prop=prop):
                self.assertNotIn(prop, body)


class LocaleSwitchTests(unittest.TestCase):
    def test_nav_label_and_pane_title_follow_a_locale_switch(self):
        expr = '''(async () => {
          let cur = "en";
          const labels = [];
          let listener = null;
          CTX = {
            i18n: { t: (k) => (k === "_locale" ? cur : k), onLocaleChange: (fn) => { listener = fn; return () => {}; } },
            register: (item) => { if (item.id === "nav") labels.push(item.data.label); return () => {}; },
            onDispose: () => {},
          };
          _renderLocale = null;
          startNavBadge(CTX);
          for (const next of ["de", "ja", "ar", "tr"]) {
            cur = next;
            listener();
            await new Promise((r) => setTimeout(r, 300));
          }
          return labels;
        })()'''
        self.assertEqual(run(expr=expr)['value'], ['Quota', 'Kontingent', 'クォータ', 'الحصة', 'Kota'])


if __name__ == '__main__':
    unittest.main()
