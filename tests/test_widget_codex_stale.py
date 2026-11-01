"""Stale Codex chips keep cached values visibly distinct from fresh quota."""
import unittest
from widget_harness import render, nodes, text, tooltip_text


def account(used=6, **extra):
    return {'windows': [{'label': 'Weekly', 'used_percent': used}], **extra}


def payload(provider=None, age=0):
    return {'age_s': age, 'providers': {
        'openai-codex': account() if provider is None else provider}}


def values(tree):
    return [n['props'] for n in nodes(tree)
            if n.get('props', {}).get('className') == 'tabular-nums']


class CodexStaleWidgetTests(unittest.TestCase):
    def test_stale_cached_value_is_struck_through_in_both_modes(self):
        for mode in ('all', 'worst'):
            with self.subTest(mode=mode):
                tree = render(component='status', statusMode=mode,
                              data=payload(age=1000))
                value = values(tree)[0]
                self.assertEqual(value['children'], '94%')
                self.assertEqual(value['style']['textDecoration'], 'line-through')
                self.assertIn('94% left · stale', tooltip_text(tree))
                button = next(n for n in nodes(tree) if n.get('type') == 'button')
                self.assertIn('stale', button['props']['aria-label'])
                # The chip's mini ring gauge is drawn in the muted tone.
                ring = next(n for n in nodes(tree) if 'data-ring' in n.get('props', {}))
                self.assertEqual(ring['props']['data-ring'], 'muted')
                arc = next(n for n in nodes(ring) if n.get('type') == 'circle'
                           and n['props'].get('strokeDasharray'))
                self.assertEqual(arc['props']['stroke'], 'var(--ui-stroke-tertiary)')

    def test_fresh_data_removes_strikethrough(self):
        stale = render(component='status', data=payload(age=1000))
        fresh = render(component='status', data=payload(age=0))
        self.assertEqual(values(stale)[0]['children'], values(fresh)[0]['children'])
        self.assertFalse(values(fresh)[0].get('style'))
        self.assertNotIn('stale', tooltip_text(fresh))

    def test_untrusted_cached_payload_remains_marked_stale(self):
        for flag in ('isError', 'isPlaceholderData'):
            with self.subTest(flag=flag):
                tree = render(component='status', data=payload(), **{flag: True})
                self.assertEqual(values(tree)[0]['children'], '94%')
                self.assertEqual(values(tree)[0]['style']['textDecoration'], 'line-through')

    def test_missing_or_failed_data_stays_unknown_without_strikethrough(self):
        for provider in ({'windows': []}, account(unavailable_reason='timeout')):
            for mode in ('all', 'worst'):
                with self.subTest(provider=provider, mode=mode):
                    tree = render(component='status', statusMode=mode,
                                  data=payload(provider, age=1000))
                    value = values(tree)[0]
                    self.assertEqual(value['children'], 'unknown')
                    self.assertFalse(value.get('style'))

    def test_stale_selection_preserves_cached_multi_account_choice(self):
        provider = {'accounts': [account(100, label='Exhausted'),
                                 account(6, label='Available')]}
        for age in (0, 1000):
            tree = render(component='status', data=payload(provider, age=age))
            self.assertIn('Available', text(tree))
            self.assertEqual(values(tree)[0]['children'], '94%')

    def test_stale_zero_and_fractional_values_keep_existing_format(self):
        for used, expected in ((100, 'limit reached'), (99.9, '<1%')):
            tree = render(component='status', data=payload(account(used), age=1000))
            self.assertEqual(values(tree)[0]['children'], expected)
            self.assertEqual(values(tree)[0]['style']['textDecoration'], 'line-through')

    def test_stale_codex_does_not_participate_in_worst_percentage_ranking(self):
        data = payload(account(99), age=1000)
        data['providers']['anthropic'] = {'windows': [
            {'label': 'Weekly', 'used_percent': 20}]}
        tree = render(component='status', statusMode='worst', data=data)
        self.assertIn('Anthropic 80%', text(tree))
        self.assertNotIn('OpenAI Codex', text(tree))


if __name__ == '__main__':
    unittest.main()
