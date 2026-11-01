"""Offline process and thread regressions for cross-profile coordination."""
import concurrent.futures
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from quota_providers import builtin as b

PAYLOAD = {'five_hour': {'utilization': 91, 'resets_at': '2026-10-14T12:00:00+00:00'}}

class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return json.dumps(PAYLOAD).encode()


def worker(directory, token, gate, calls, results):
    b._backoff_state_path = lambda: str(Path(directory) / 'backoff.json')
    def network(*args, **kwargs):
        with calls.get_lock(): calls.value += 1
        time.sleep(.15)
        return Response()
    b.urlopen_no_redirect = network
    gate.wait(5)
    results.put(b._request_anthropic_usage(token))


def store_worker(directory, token, gate):
    b._backoff_state_path = lambda: str(Path(directory) / 'backoff.json')
    original = tempfile.mkstemp
    def delayed(*args, **kwargs):
        time.sleep(.2)
        return original(*args, **kwargs)
    gate.wait(5)
    with mock.patch.object(tempfile, 'mkstemp', side_effect=delayed):
        b._store_shared_payload(token, PAYLOAD)
        b._open_anthropic_cooldown(token)


def load_worker(directory, result, ready):
    b._backoff_state_path = lambda: str(Path(directory)/'backoff.json')
    original = b._read_anthropic_state
    def signal_reader(path):
        ready.set()
        return original(path)
    b._read_anthropic_state = signal_reader
    result.put(b._load_shared_payload('synthetic'))


class SharedConcurrencyTests(unittest.TestCase):
    def test_platform_without_nofollow_still_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state'
            target = Path(directory)/'target'
            target.write_text('{}')
            path.symlink_to(target)
            with mock.patch.object(b.os, 'O_NOFOLLOW', 0):
                with self.assertRaises((OSError, ValueError)):
                    b._read_anthropic_state(str(path))

    def test_windows_lock_fallback_keeps_persistence(self):
        import sys
        import types
        fake = types.SimpleNamespace(LK_NBLCK=1, locking=mock.Mock())
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(sys.modules, {'fcntl': None, 'msvcrt': fake}), mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')):
                b._store_shared_payload('synthetic', PAYLOAD)
                b._open_anthropic_cooldown('synthetic')
                self.assertEqual(b._load_shared_payload('synthetic'), PAYLOAD)
                self.assertTrue(b._anthropic_cooling_down('synthetic'))
            self.assertGreaterEqual(fake.locking.call_count, 2)

    def test_empty_shared_payload_still_reports_no_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')), mock.patch.object(b, '_core_anthropic_token', return_value='synthetic'), mock.patch.object(b, '_core_anthropic_is_oauth', return_value=True):
                b._store_shared_payload('synthetic', {})
                result = b._fetch_anthropic_single()
            self.assertEqual(result.unavailable_reason, 'no-data')

    def test_cli_and_footer_show_stale_observation(self):
        from test_claude_multi_account import CliRenderTests
        helper = CliRenderTests()
        cache = helper._cache()
        detail = 'Observed 2026-10-13T09:00:00+00:00 · 3500s old · stale; 429 cooldown'
        cache['providers']['anthropic']['details'] = [detail]
        footer, cli, _, _ = helper._render(cache)
        self.assertIn(detail, cli)
        self.assertIn('stale; 429 cooldown', footer)

    def test_stale_observation_is_visible_in_clean_widget(self):
        from widget_harness import render, text
        detail = 'Observed 2026-10-13T09:00:00+00:00 · 3500s old · stale; 429 cooldown'
        record = {'windows': [{'label': 'Current session', 'used_percent': 91}], 'details': [detail]}
        for provider_id in ('anthropic', 'anthropic:work'):
            with self.subTest(provider=provider_id):
                rendered = text(render(component='row', id=provider_id, provider=record, mode='clean'))
                self.assertIn(detail, rendered)

    def test_shared_fifo_read_is_nonblocking(self):
        import os
        ctx = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            os.mkfifo(Path(directory)/b._ANTHROPIC_SHARED_FILENAME)
            ready, result = ctx.Event(), ctx.Queue()
            process = ctx.Process(target=load_worker, args=(directory, result, ready))
            process.start()
            reached_read = ready.wait(10)
            if not reached_read:
                process.join(.1)
                if process.is_alive(): process.kill(); process.join()
                self.fail('worker did not reach the shared-state read')
            process.join(1)
            blocked = process.is_alive()
            if blocked: process.kill(); process.join()
            self.assertFalse(blocked, 'FIFO must be rejected before reading')
            self.assertIsNone(result.get(timeout=1))

    def test_shared_symlink_and_oversize_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            share = Path(directory)/b._ANTHROPIC_SHARED_FILENAME
            target = Path(directory)/'target'
            body = json.dumps({b._backoff_key('synthetic'): {'at': time.time(), 'payload': PAYLOAD}})
            target.write_text(body)
            share.symlink_to(target)
            with mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')):
                self.assertIsNone(b._load_shared_payload('synthetic'))
                share.unlink()
                share.write_text(body + ' ' * (2 * 1024 * 1024))
                self.assertIsNone(b._load_shared_payload('synthetic'))

    def test_hostile_timestamp_does_not_block_other_token_write(self):
        with tempfile.TemporaryDirectory() as directory:
            share = Path(directory)/b._ANTHROPIC_SHARED_FILENAME
            share.write_text(json.dumps({'hostile': {'at': int('9'*400), 'payload': {}}}))
            with mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')):
                b._store_shared_payload('synthetic', PAYLOAD)
            self.assertIn(b._backoff_key('synthetic'), json.loads(share.read_text()))

    def test_persistence_wait_uses_request_deadline(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')):
                with open(b._shared_payload_path()+'.lock', 'w') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with mock.patch.object(b, 'urlopen_no_redirect', return_value=Response()):
                        start = time.monotonic()
                        b._request_anthropic_usage('synthetic', timeout=.08)
                        elapsed = time.monotonic() - start
                    self.assertLess(elapsed, .18)

    def test_stale_observation_is_not_republished_as_fresh(self):
        import importlib
        import sys
        import types
        root = Path(__file__).resolve().parent.parent
        package = types.ModuleType('shared_review')
        package.__path__ = [str(root)]
        with mock.patch.dict(sys.modules, {'shared_review': package}):
            provider = importlib.import_module('shared_review.quota_providers.builtin')
            cache = importlib.import_module('shared_review.quota_cache')
        with tempfile.TemporaryDirectory() as directory:
            now = time.time()
            at = now - 3500
            key = provider._backoff_key('synthetic')
            (Path(directory)/provider._ANTHROPIC_SHARED_FILENAME).write_text(json.dumps({key: {'at': at, 'payload': PAYLOAD}}))
            (Path(directory)/'backoff.json').write_text(json.dumps({key: now + 100}))
            with mock.patch.object(provider, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')), mock.patch.object(provider, '_core_anthropic_token', return_value='synthetic'), mock.patch.object(provider, '_core_anthropic_is_oauth', return_value=True), mock.patch.object(provider, 'urlopen_no_redirect') as op, mock.patch.object(cache, '_cache_path', return_value=str(Path(directory)/'quota_cache.json')), mock.patch.dict(cache.PROVIDER_FETCHERS, {'anthropic': provider._fetch_anthropic_single}, clear=True):
                refreshed = cache.refresh_quota_cache(budget=1)
                record = refreshed['providers']['anthropic']
            self.assertEqual(op.call_count, 0)
            self.assertIn('stale', ' '.join(record['details']))
            self.assertIn('429 cooldown', ' '.join(record['details']))
            self.assertIn('3500s old', ' '.join(record['details']))
            self.assertEqual(record['windows'][0]['used_percent'], 91)
            from datetime import datetime, timezone
            self.assertIn(datetime.fromtimestamp(at, timezone.utc).isoformat(), record['details'][0])
            from test_claude_multi_account import CliRenderTests
            from widget_harness import render, text
            footer, cli, _, _ = CliRenderTests()._render(refreshed)
            self.assertIn(record['details'][0], footer)
            self.assertIn(record['details'][0], cli)
            self.assertIn(record['details'][0], text(render(component='row', id='anthropic', provider=record, mode='clean')))

    def test_different_process_tokens_both_survive(self):
        ctx = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            gate = ctx.Event()
            processes = [ctx.Process(target=store_worker, args=(directory, token, gate)) for token in ('a', 'b')]
            for process in processes: process.start()
            gate.set()
            for process in processes:
                process.join(10)
                if process.is_alive(): process.kill(); process.join()
                self.assertEqual(process.exitcode, 0)
            expected = {b._backoff_key('a'), b._backoff_key('b')}
            self.assertEqual(set(json.loads((Path(directory)/b._ANTHROPIC_SHARED_FILENAME).read_text())), expected)
            self.assertEqual(set(json.loads((Path(directory)/'backoff.json').read_text())), expected)

    def test_different_thread_tokens_both_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            original = tempfile.mkstemp
            def delayed(*args, **kwargs):
                time.sleep(.2)
                return original(*args, **kwargs)
            with mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')), mock.patch.object(tempfile, 'mkstemp', side_effect=delayed):
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    list(pool.map(lambda token: b._store_shared_payload(token, PAYLOAD), ('a', 'b')))
            self.assertEqual(set(json.loads((Path(directory)/b._ANTHROPIC_SHARED_FILENAME).read_text())), {b._backoff_key('a'), b._backoff_key('b')})

    def test_eight_processes_same_token_make_one_request(self):
        ctx = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            gate, calls, results = ctx.Event(), ctx.Value('i', 0), ctx.Queue()
            processes = [ctx.Process(target=worker, args=(directory, 'synthetic', gate, calls, results)) for _ in range(8)]
            for process in processes: process.start()
            gate.set()
            try:
                values = [results.get(timeout=15) for _ in processes]
                for process in processes:
                    process.join(5)
                    self.assertEqual(process.exitcode, 0)
                self.assertEqual(values, [(PAYLOAD, None)] * 8)
                self.assertEqual(calls.value, 1)
            finally:
                for process in processes:
                    if process.is_alive(): process.kill(); process.join()

    def test_eight_threads_same_token_make_one_request(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = threading.Barrier(8)
            def network(*args, **kwargs):
                time.sleep(.15)
                return Response()
            def request(_):
                gate.wait(5)
                return b._request_anthropic_usage('synthetic')
            with mock.patch.object(b, '_backoff_state_path', return_value=str(Path(directory)/'backoff.json')), mock.patch.object(b, 'urlopen_no_redirect', side_effect=network) as op:
                with concurrent.futures.ThreadPoolExecutor(8) as pool:
                    values = list(pool.map(request, range(8)))
            self.assertEqual(values, [(PAYLOAD, None)] * 8)
            self.assertEqual(op.call_count, 1)
