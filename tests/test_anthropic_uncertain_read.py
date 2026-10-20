"""Public-fetcher regressions: uncertain reads must never erase other accounts."""
import errno
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from quota_providers import builtin as b, get_fetcher

PAYLOAD = {'five_hour': {'utilization': 91}}


class AnthropicUncertainReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.backoff = self.directory / 'backoff.json'
        self.share = self.directory / b._ANTHROPIC_SHARED_FILENAME
        self.account_a = b._backoff_key('existing-account-a')
        for patch in (
            mock.patch.object(b, '_backoff_state_path', return_value=str(self.backoff)),
            mock.patch.object(b, '_claude_accounts_setting', return_value=([], False)),
            mock.patch.object(b, '_core_anthropic_token', return_value='new-account-b'),
            mock.patch.object(b, '_core_anthropic_is_oauth', return_value=True),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.fetch = get_fetcher('anthropic')

    def seed_account_a(self):
        self.share.write_text(json.dumps({self.account_a: {'at': time.time(), 'payload': PAYLOAD}}))
        self.backoff.write_text(json.dumps({self.account_a: time.time() + 600}))
        return self.share.read_bytes(), self.backoff.read_bytes()

    def refresh_b(self, rate_limited):
        if rate_limited:
            error = urllib.error.HTTPError(b._ANTHROPIC_USAGE_URL, 429, 'synthetic', None, None)
            # Python 3.9's HTTPError.close cannot close a missing fp.
            if error.fp is not None:
                self.addCleanup(error.close)
            opener = mock.Mock(side_effect=error)
        else:
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(PAYLOAD).encode()
            opener = mock.Mock(return_value=response)
        with mock.patch.object(b, 'urlopen_no_redirect', opener):
            result = self.fetch()
        self.assertEqual(opener.call_count, 1, 'uncertain cache reads must still fail open')
        self.assertEqual(result.unavailable_reason, 'http-429' if rate_limited else None)
        if not rate_limited:
            self.assertEqual(result.windows[0].used_percent, 91)

    def test_uncertain_io_preserves_other_account_state_bytes(self):
        original_open = os.open
        for error in (errno.EIO, errno.EACCES):
            for rate_limited in (False, True):
                with self.subTest(error=error, rate_limited=rate_limited):
                    before = self.seed_account_a()

                    def fail_read(path, flags, *args, **kwargs):
                        if (str(path) in (str(self.share), str(self.backoff))
                                and flags & os.O_ACCMODE == os.O_RDONLY):
                            raise OSError(error, 'injected uncertain read')
                        return original_open(path, flags, *args, **kwargs)

                    with mock.patch.object(b.os, 'open', side_effect=fail_read):
                        self.refresh_b(rate_limited)
                    self.assertEqual((self.share.read_bytes(), self.backoff.read_bytes()), before)
                    self.assertEqual(list(self.directory.glob('.anthropic_*.tmp')), [])

    def test_oversized_valid_other_account_state_is_not_replaced(self):
        for rate_limited in (False, True):
            with self.subTest(rate_limited=rate_limited):
                self.seed_account_a()
                for path in (self.share, self.backoff):
                    with path.open('ab') as stream:
                        stream.write(b' ' * b._ANTHROPIC_STATE_MAX_BYTES)
                before = self.share.read_bytes(), self.backoff.read_bytes()
                self.refresh_b(rate_limited)
                self.assertEqual((self.share.read_bytes(), self.backoff.read_bytes()), before)

    def test_nonregular_other_account_state_is_not_replaced(self):
        for kind in ('symlink', 'directory', 'fifo'):
            if kind == 'fifo' and not hasattr(os, 'mkfifo'):
                continue
            for rate_limited in (False, True):
                with self.subTest(kind=kind, rate_limited=rate_limited):
                    for path in (self.share, self.backoff):
                        if path.is_dir():
                            path.rmdir()
                        elif path.exists() or path.is_symlink():
                            path.unlink()
                    before = self.seed_account_a()
                    targets = []
                    for path in (self.share, self.backoff):
                        target = path.with_suffix('.target')
                        path.replace(target)
                        targets.append(target)
                        if kind == 'symlink':
                            path.symlink_to(target)
                        elif kind == 'directory':
                            path.mkdir()
                        else:
                            os.mkfifo(path)
                    identities = [(path.lstat().st_mode, path.lstat().st_ino)
                                  for path in (self.share, self.backoff)]
                    self.refresh_b(rate_limited)
                    self.assertEqual([target.read_bytes() for target in targets], list(before))
                    self.assertEqual([(path.lstat().st_mode, path.lstat().st_ino)
                                      for path in (self.share, self.backoff)], identities)

    def test_absent_or_demonstrably_corrupt_state_can_be_initialized(self):
        for body in (None, b'invalid json{', b'\xff', b'[]'):
            for rate_limited in (False, True):
                with self.subTest(body=body, rate_limited=rate_limited):
                    for path in (self.share, self.backoff):
                        path.unlink(missing_ok=True)
                        if body is not None:
                            path.write_bytes(body)
                    self.refresh_b(rate_limited)
                    path = self.backoff if rate_limited else self.share
                    self.assertIn(b._backoff_key('new-account-b'), json.loads(path.read_bytes()))
