"""Codex special-reset announcement backend.

Offline tests: the transport is injected or backed by a loopback server. The
real endpoint is only exercised by the separate live smoke probe, never here.
"""

from __future__ import annotations

import importlib
import json
import threading
import time
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "quota_plugin_under_test" not in sys.modules:
    _pkg = types.ModuleType("quota_plugin_under_test")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["quota_plugin_under_test"] = _pkg


def module():
    return importlib.import_module("quota_plugin_under_test.codex_special_reset")


def record(reset_id="reset-1", reset_type="regular",
           announced_at="2026-10-20T18:04:05+00:00",
           source=None, status=None):
    value = {
        "id": reset_id,
        "reset_type": reset_type,
        "announced_at": announced_at,
        "text": "a reset is coming",
        "source": source if source is not None else {
            "type": "x_post",
            "author": "thsottiaux",
            "url": "https://x.com/thsottiaux/status/1234567890123456789",
        },
        "stats": {"forecast": "ignore me"},
    }
    if status is not None:
        value["status"] = status
    return value


def payload(latest=None, scheduled=None):
    return {
        "data": {
            "latest_reset": latest,
            "scheduled_reset": scheduled,
            "active_watch": {"id": "watch-1"},
            "stats": {"observed": 4},
        },
        "meta": {"api_version": "v1", "generated_at": "2026-10-22T10:00:00+00:00"},
    }


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def ok_transport(status_payload, headers=None, calls=None):
    body = json.dumps(status_payload).encode("utf-8")

    def transport(url, timeout, etag):
        if calls is not None:
            calls.append((url, timeout, etag))
        return 200, dict(headers or {"cache-control": "max-age=14400",
                                     "etag": '"v1-tag"'}), body

    return transport


class _TempState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_path = str(Path(self._tmp.name) / "special_state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def refresh(self, previous=None, *, transport, now):
        return module().refresh(previous, transport=transport, now=lambda: now,
                                state_path=self.state_path)


class SelectionTests(unittest.TestCase):
    def test_valid_scheduled_announcement_wins_over_latest(self):
        latest = record("latest-1", "banked", "2026-10-19T08:00:00+00:00")
        scheduled = record("sched-1", "regular", "2026-10-21T09:30:00+00:00",
                           status="scheduled")

        chosen = module().select_announcement(payload(latest, scheduled))

        self.assertEqual(chosen, {
            "id": "sched-1",
            "status": "scheduled",
            "reset_type": "regular",
            "announced_at": "2026-10-21T09:30:00+00:00",
            "scheduled_for": None,
            "source_url": "https://x.com/thsottiaux/status/1234567890123456789",
        })

    def test_observed_only_scheduled_source_falls_back_to_latest(self):
        observed = record("sched-observed", status="scheduled", source={
            "type": "usage_observation", "author": "thsottiaux",
            "url": "https://x.com/thsottiaux/status/123",
        })
        latest = record("latest-2", "banked", "2026-10-18T07:15:00+00:00")

        chosen = module().select_announcement(payload(latest, observed))

        self.assertEqual(chosen["id"], "latest-2")
        self.assertEqual(chosen["status"], "recorded")
        self.assertEqual(chosen["reset_type"], "banked")

    def test_scheduled_with_untrusted_author_or_url_falls_back(self):
        for expected, scheduled in (
            ("bad-author", record("sched", status="scheduled", source={
                "type": "x_post", "author": "someone_else",
                "url": "https://x.com/thsottiaux/status/123",
            })),
            ("bad-host", record("sched", status="scheduled", source={
                "type": "x_post", "author": "thsottiaux",
                "url": "https://example.com/thsottiaux/status/123",
            })),
            ("bad-path", record("sched", status="scheduled", source={
                "type": "x_post", "author": "thsottiaux",
                "url": "https://x.com/thsottiaux/status/123?ref=evil",
            })),
            ("bad-time", record("sched", "regular", "not-a-time",
                                status="scheduled")),
            ("naive-time", record("sched", "regular", "2026-10-21T09:30:00",
                                  status="scheduled")),
            ("bad-type", record("sched", "monthly", status="scheduled")),
            ("not-scheduled", record("sched", status="recorded")),
        ):
            with self.subTest(case=expected):
                latest = record("latest-fallback", "regular",
                                "2026-10-17T06:00:00+00:00")
                chosen = module().select_announcement(payload(latest, scheduled))
                self.assertEqual(chosen["id"], "latest-fallback")
                self.assertEqual(chosen["status"], "recorded")

    def test_no_valid_explicit_announcement_is_none_not_a_fake_reset(self):
        observed = record("observed", source={
            "type": "observed", "author": "thsottiaux",
            "url": "https://x.com/thsottiaux/status/123",
        })
        self.assertIsNone(module().select_announcement(payload(observed, None)))
        self.assertIsNone(module().select_announcement(payload(None, None)))

    def test_output_keeps_the_schedule_but_ignores_text_and_statistics(self):
        scheduled = record("sched-2", status="scheduled")
        scheduled["scheduled_for"] = "2026-10-21T10:00:00+00:00"
        scheduled["text"] = "\x1b[31mcontrol characters\x07"
        scheduled["stats"] = {"confidence": 0.99}

        chosen = module().select_announcement(payload(None, scheduled))

        self.assertEqual(set(chosen), {
            "id", "status", "reset_type", "announced_at", "scheduled_for",
            "source_url",
        })
        # The publisher's estimate is carried, but the record stays scheduled:
        # a scheduled time is not evidence that the reset ran.
        self.assertEqual(chosen["scheduled_for"], "2026-10-21T10:00:00+00:00")
        self.assertEqual(chosen["status"], "scheduled")
        self.assertNotIn("control characters", repr(chosen))

    def test_wrong_api_version_is_a_schema_error(self):
        body = payload(None, None)
        body["meta"]["api_version"] = "v2"
        with self.assertRaises(module().SpecialResetError) as raised:
            module().select_announcement(body)
        self.assertEqual(raised.exception.reason, "schema")


class ValidationTests(unittest.TestCase):
    def test_source_url_must_match_end_to_end_without_a_final_newline(self):
        bad_url = "https://x.com/thsottiaux/status/1234567890123456789\n"
        bad_scheduled = record(
            "sched-newline", status="scheduled",
            source={"type": "x_post", "author": "thsottiaux", "url": bad_url})
        latest = record("latest-fallback", "regular", "2026-10-17T06:00:00+00:00")

        chosen = module().select_announcement(payload(latest, bad_scheduled))

        self.assertEqual(chosen["id"], "latest-fallback")
        normalized = module().normalize_entry({
            "announcement": {
                "id": "reset-1",
                "status": "recorded",
                "reset_type": "regular",
                "announced_at": "2026-10-20T18:04:05+00:00",
                "scheduled_for": None,
                "source_url": bad_url,
            },
            "fetched_at": "2026-10-22T10:00:00+00:00",
            "stale": False,
            "unavailable_reason": None,
        })
        self.assertIsNone(normalized["announcement"])
        self.assertTrue(normalized["stale"])
        self.assertEqual(normalized["unavailable_reason"], "schema")

    def test_utc_normalization_rejects_an_offset_that_overflows(self):
        self.assertIsNone(module()._iso_utc("9999-12-31T23:59:59-14:00"))


class FetchCacheTests(_TempState):
    def test_malformed_explicit_announcement_retains_last_success_as_stale(self):
        first = self.refresh(None, transport=ok_transport(payload(record(), None)),
                             now=1000.0)
        malformed = record(announced_at="not-a-date")
        entry = self.refresh(first, transport=ok_transport(payload(malformed, None)),
                             now=16000.0)
        self.assertEqual(entry["announcement"], first["announcement"])
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["unavailable_reason"], "schema")

    def test_429_without_retry_header_still_backs_off(self):
        calls = []

        def limited(*args):
            calls.append(args)
            return 429, {}, b""

        first = self.refresh(None, transport=limited, now=1000.0)
        self.refresh(first, transport=limited, now=1001.0)
        self.assertEqual(len(calls), 1)

    def test_success_writes_exact_contract_and_honors_max_age(self):
        latest = record("latest-3", "banked", "2026-10-20T18:04:05+00:00")
        calls = []
        transport = ok_transport(payload(latest, None), calls=calls)
        now = 1_000_000.0

        entry = self.refresh(None, transport=transport, now=now)

        self.assertEqual(entry, {
            "announcement": {
                "id": "latest-3",
                "status": "recorded",
                "reset_type": "banked",
                "announced_at": "2026-10-20T18:04:05+00:00",
                "scheduled_for": None,
                "source_url": "https://x.com/thsottiaux/status/1234567890123456789",
            },
            "fetched_at": stamp(now),
            "stale": False,
            "unavailable_reason": None,
        })
        self.assertEqual(calls, [(module().ENDPOINT, module().TIMEOUT_S, None)])
        state = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
        self.assertEqual(state["etag"], '"v1-tag"')
        self.assertEqual(state["expires_at"], now + 14400)

        def forbidden(*_args):
            raise AssertionError("origin re-fetched inside Cache-Control max-age")

        cached = self.refresh(entry, transport=forbidden, now=now + 14399)
        self.assertEqual(cached, entry)

        refreshed = self.refresh(entry, transport=transport, now=now + 14401)
        self.assertEqual(refreshed["announcement"]["id"], "latest-3")
        self.assertEqual(refreshed["fetched_at"], stamp(now + 14401))

    def test_not_modified_renews_fetched_at_and_keeps_announcement(self):
        latest = record("latest-4")
        now = 2_000_000.0
        first = self.refresh(None, transport=ok_transport(payload(latest, None)),
                             now=now)

        seen = []

        def not_modified(url, timeout, etag):
            seen.append(etag)
            return 304, {"cache-control": "max-age=14400", "etag": '"v1-tag-2"'}, b""

        renewed = self.refresh(first, transport=not_modified, now=now + 14401)

        self.assertEqual(seen, ['"v1-tag"'])
        self.assertEqual(renewed["announcement"], first["announcement"])
        self.assertEqual(renewed["fetched_at"], stamp(now + 14401))
        self.assertFalse(renewed["stale"])
        self.assertIsNone(renewed["unavailable_reason"])

    def test_error_keeps_last_announcement_and_marks_it_stale(self):
        latest = record("latest-5")
        now = 3_000_000.0
        first = self.refresh(None, transport=ok_transport(payload(latest, None)),
                             now=now)

        def failing(_url, _timeout, _etag):
            return 500, {}, b"upstream exploded"

        failed = self.refresh(first, transport=failing, now=now + 14401)

        self.assertEqual(failed["announcement"], first["announcement"])
        self.assertEqual(failed["fetched_at"], first["fetched_at"])
        self.assertTrue(failed["stale"])
        self.assertEqual(failed["unavailable_reason"], "http-500")

    def test_first_failure_reports_unknown_without_inventing_data(self):
        def failing(_url, _timeout, _etag):
            raise TimeoutError("slow")

        entry = self.refresh(None, transport=failing, now=4_000_000.0)

        self.assertEqual(entry, {
            "announcement": None,
            "fetched_at": None,
            "stale": True,
            "unavailable_reason": "timeout",
        })

    def test_retry_after_blocks_the_next_attempt(self):
        latest = record("latest-6")
        now = 5_000_000.0
        first = self.refresh(None, transport=ok_transport(payload(latest, None)),
                             now=now)

        def rate_limited(_url, _timeout, _etag):
            return 429, {"retry-after": "120"}, b""

        limited = self.refresh(first, transport=rate_limited, now=now + 14401)
        self.assertEqual(limited["unavailable_reason"], "http-429")
        self.assertTrue(limited["stale"])

        def forbidden(*_args):
            raise AssertionError("request sent during Retry-After")

        blocked = self.refresh(limited, transport=forbidden, now=now + 14500)
        self.assertEqual(blocked["announcement"], first["announcement"])
        self.assertTrue(blocked["stale"])
        self.assertEqual(blocked["unavailable_reason"], "rate-limited")

        allowed = self.refresh(blocked, transport=ok_transport(payload(latest, None)),
                               now=now + 14522)
        self.assertFalse(allowed["stale"])
        self.assertIsNone(allowed["unavailable_reason"])

    def test_oversize_bad_json_and_schema_never_clobber_last_success(self):
        latest = record("latest-7")
        now = 6_000_000.0
        first = self.refresh(None, transport=ok_transport(payload(latest, None)),
                             now=now)
        cases = (
            ("oversize", lambda _u, _t, _e: (200, {}, b"x" * (64 * 1024 + 1))),
            ("bad-json", lambda _u, _t, _e: (200, {}, b"{not json")),
            ("schema", lambda _u, _t, _e: (
                200, {}, b'{"data": {}, "meta": {"api_version": "v9"}}')),
        )
        for reason, transport in cases:
            with self.subTest(reason=reason):
                entry = self.refresh(first, transport=transport, now=now + 14401)
                self.assertEqual(entry["announcement"], first["announcement"])
                self.assertEqual(entry["fetched_at"], first["fetched_at"])
                self.assertTrue(entry["stale"])
                self.assertEqual(entry["unavailable_reason"], reason)

    def test_any_redirect_status_is_a_redirect_failure(self):
        entry = self.refresh(
            None,
            transport=lambda _u, _t, _e: (302, {"location": "https://evil"}, b""),
            now=6_500_000.0,
        )
        self.assertIsNone(entry["announcement"])
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["unavailable_reason"], "redirect")

    def test_discarded_newer_fetch_does_not_rebind_validators_to_old_entry(self):
        first_payload = payload(record("announcement-a"), None)
        second_payload = payload(record("announcement-b"), None)
        now = 8_000_000.0
        first = self.refresh(
            None,
            transport=ok_transport(first_payload, {
                "cache-control": "max-age=0", "etag": '"tag-a"'}),
            now=now,
        )
        self.assertEqual(first["announcement"]["id"], "announcement-a")

        # A parallel fetch stores B's validator and a far-future expiry while
        # this caller discards the result and keeps A.
        self.refresh(
            first,
            transport=ok_transport(second_payload, {
                "cache-control": "max-age=14400", "etag": '"tag-b"'}),
            now=now + 1,
        )

        seen = []

        def conditional(_url, _timeout, etag):
            seen.append(etag)
            if etag == '"tag-b"':
                return 304, {"cache-control": "max-age=14400",
                             "etag": '"tag-b"'}, b""
            return 200, {"cache-control": "max-age=0",
                         "etag": '"tag-a"'}, json.dumps(first_payload).encode()

        entry = self.refresh(first, transport=conditional, now=now + 2)

        self.assertEqual(seen, [None], "state validator was used for an older entry")
        self.assertEqual(entry["announcement"]["id"], "announcement-a")
        self.assertEqual(entry["fetched_at"], stamp(now + 2))
        self.assertFalse(entry["stale"])

    def test_304_without_a_matching_validator_cannot_renew_the_old_entry(self):
        first_payload = payload(record("announcement-a"), None)
        now = 8_100_000.0
        first = self.refresh(
            None,
            transport=ok_transport(first_payload, {
                "cache-control": "max-age=0", "etag": '"tag-a"'}),
            now=now,
        )
        self.refresh(
            first,
            transport=ok_transport(payload(record("announcement-b"), None), {
                "cache-control": "max-age=0", "etag": '"tag-b"'}),
            now=now + 1,
        )

        entry = self.refresh(
            first,
            transport=lambda _u, _t, _e: (304, {"etag": '"tag-b"'}, b""),
            now=now + 2,
        )

        self.assertEqual(entry["announcement"], first["announcement"])
        self.assertEqual(entry["fetched_at"], first["fetched_at"])
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["unavailable_reason"], "http-304")

    def test_state_write_uses_a_unique_temp_not_a_fixed_sibling(self):
        blocker = Path(self.state_path + ".tmp")
        blocker.mkdir()

        entry = self.refresh(
            None,
            transport=ok_transport(payload(record("latest-8"), None)),
            now=9_000_000.0,
        )

        self.assertFalse(entry["stale"])
        state = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
        self.assertEqual(state["etag"], '"v1-tag"')
        self.assertTrue(blocker.is_dir())


def cache_module():
    return importlib.import_module("quota_plugin_under_test.quota_cache")


def base_module():
    return importlib.import_module("quota_plugin_under_test.quota_providers.base")


class _CacheIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.cache_path = self.home / "quota_cache.json"
        self.qc = cache_module()
        self.base = base_module()
        self._cache_path = mock.patch.object(
            self.qc, "_cache_path", return_value=str(self.cache_path))
        self._cache_path.start()

    def tearDown(self):
        self._cache_path.stop()
        self._tmp.cleanup()

    def run_refresh(self, fetchers, special_refresh, budget=1.0):
        with mock.patch.dict(self.qc.PROVIDER_FETCHERS, fetchers, clear=True), \
             mock.patch.object(self.qc.codex_special_reset, "refresh",
                               side_effect=special_refresh):
            return self.qc.refresh_quota_cache(budget=budget)

    def codex_unavailable(self, reason="no-credentials"):
        return self.base.build_unavailable("openai-codex", reason)

    def test_configured_codex_refreshes_and_writes_root_key(self):
        special = {
            "announcement": {
                "id": "reset-9", "status": "recorded", "reset_type": "regular",
                "announced_at": "2026-10-20T18:04:05+00:00",
                "scheduled_for": None,
                "source_url": "https://x.com/thsottiaux/status/123",
            },
            "fetched_at": "2026-10-22T10:00:00+00:00",
            "stale": False,
            "unavailable_reason": None,
        }
        seen = []

        def fake(previous=None, **_kwargs):
            seen.append(previous)
            return special

        fetch = self.base.QuotaResult(
            "openai-codex",
            windows=[self.base.QuotaWindow("Weekly", used_percent=10.0)])
        cache = self.run_refresh({"openai-codex": lambda: fetch}, fake)

        self.assertEqual(cache["codex_special_reset"], special)
        self.assertEqual(seen, [None])
        on_disk = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["codex_special_reset"], special)

    def test_unrelated_slow_provider_does_not_starve_announcement(self):
        special = module().empty_entry("probe-success")
        called = threading.Event()
        release = threading.Event()
        fetch = self.base.QuotaResult("openai-codex", windows=[
            self.base.QuotaWindow("Weekly", used_percent=10.0)])

        def slow_provider():
            release.wait(2.0)
            return self.base.QuotaResult("deepseek")

        def fake(previous=None, **kwargs):
            called.set()
            return special

        try:
            cache = self.run_refresh({"deepseek": slow_provider,
                                      "openai-codex": lambda: fetch},
                                     fake, budget=0.2)
            self.assertTrue(called.is_set(), "unrelated provider starved metadata")
            self.assertEqual(cache["codex_special_reset"], special)
        finally:
            release.set()

    def test_announcement_transport_receives_remaining_sweep_timeout(self):
        seen = []
        fetch = self.base.QuotaResult("openai-codex", windows=[
            self.base.QuotaWindow("Weekly", used_percent=10.0)])

        def fake(previous=None, **kwargs):
            seen.append(kwargs.get("timeout"))
            return module().empty_entry()

        self.run_refresh({"openai-codex": lambda: fetch}, fake, budget=0.2)
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0])
        self.assertGreater(seen[0], 0)
        self.assertLessEqual(seen[0], 0.2)

    def test_unconfigured_codex_never_calls_the_announcement_source(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("special-reset source called without Codex")

        cache = self.run_refresh(
            {"openai-codex": lambda: self.codex_unavailable()}, forbidden)

        self.assertEqual(cache["codex_special_reset"], {
            "announcement": None,
            "fetched_at": None,
            "stale": True,
            "unavailable_reason": "codex-not-configured",
        })

    def test_no_codex_provider_never_calls_the_announcement_source(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("special-reset source called without Codex")

        cache = self.run_refresh({"deepseek": lambda: self.base.QuotaResult(
            "deepseek", windows=[self.base.QuotaWindow("Monthly", 1.0)])},
            forbidden)

        self.assertEqual(
            cache["codex_special_reset"]["unavailable_reason"],
            "codex-not-configured")

    def test_newer_cross_process_success_on_disk_is_not_clobbered(self):
        newest = {
            "announcement": {
                "id": "disk-reset", "status": "scheduled", "reset_type": "regular",
                "announced_at": "2026-10-21T09:30:00+00:00",
                "scheduled_for": None,
                "source_url": "https://x.com/thsottiaux/status/456",
            },
            "fetched_at": "2026-10-22T11:00:00+00:00",
            "stale": False,
            "unavailable_reason": None,
        }
        self.cache_path.write_text(json.dumps({
            "fetched_at": "2026-10-22T11:00:00+00:00",
            "providers": {},
            "codex_special_reset": newest,
        }), encoding="utf-8")
        stale = {
            "announcement": newest["announcement"],
            "fetched_at": "2026-10-22T09:00:00+00:00",
            "stale": True,
            "unavailable_reason": "http-500",
        }
        fetch = self.base.QuotaResult(
            "openai-codex",
            windows=[self.base.QuotaWindow("Weekly", used_percent=10.0)])

        cache = self.run_refresh(
            {"openai-codex": lambda: fetch}, lambda **_kw: stale)

        self.assertEqual(cache["codex_special_reset"], newest)

    def test_slow_announcement_source_cannot_stretch_the_refresh_budget(self):
        def slow(previous=None, **_kwargs):
            time.sleep(5.0)
            return {
                "announcement": None, "fetched_at": None,
                "stale": False, "unavailable_reason": None,
            }

        fetch = self.base.QuotaResult(
            "openai-codex",
            windows=[self.base.QuotaWindow("Weekly", used_percent=10.0)])
        started = time.monotonic()
        cache = self.run_refresh(
            {"openai-codex": lambda: fetch}, slow, budget=0.4)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5, "announcement fetch outlived the sweep budget")
        self.assertEqual(
            cache["codex_special_reset"]["unavailable_reason"], "timeout")
        self.assertTrue(cache["codex_special_reset"]["stale"])

    def test_exhausted_budget_does_not_start_the_announcement_request(self):
        calls = []

        def should_not_run(previous=None, **_kwargs):
            calls.append(previous)
            return {
                "announcement": None, "fetched_at": None,
                "stale": False, "unavailable_reason": None,
            }

        fetch = self.base.QuotaResult(
            "openai-codex",
            windows=[self.base.QuotaWindow("Weekly", used_percent=10.0)])
        cache = self.run_refresh(
            {"openai-codex": lambda: fetch}, should_not_run, budget=0.0)
        time.sleep(0.05)

        self.assertEqual(calls, [])
        self.assertEqual(
            cache["codex_special_reset"]["unavailable_reason"], "timeout")

    def test_announcement_state_path_is_resolved_on_the_calling_thread(self):
        special = {
            "announcement": None,
            "fetched_at": None,
            "stale": True,
            "unavailable_reason": "not-fetched",
        }
        main_thread = threading.current_thread()
        resolved = {}

        def resolve_state_path(explicit=None):
            resolved["thread"] = threading.current_thread()
            resolved["path"] = str(self.home / "special_state.json")
            return resolved["path"]

        worker = {}

        def fake(previous=None, **kwargs):
            worker["thread"] = threading.current_thread()
            worker["state_path"] = kwargs.get("state_path")
            return special

        fetch = self.base.QuotaResult(
            "openai-codex",
            windows=[self.base.QuotaWindow("Weekly", used_percent=10.0)])
        with mock.patch.object(self.qc.codex_special_reset, "_state_path",
                               side_effect=resolve_state_path):
            self.run_refresh({"openai-codex": lambda: fetch}, fake)

        self.assertIs(resolved["thread"], main_thread)
        self.assertIsNot(worker["thread"], main_thread)
        self.assertEqual(worker["state_path"], resolved["path"])


class CliStatusContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmp.name) / "quota_cache.json"
        self.qc = cache_module()
        self.commands = importlib.import_module(
            "quota_plugin_under_test.commands")
        self._cache_path = mock.patch.object(
            self.qc, "_cache_path", return_value=str(self.cache_path))
        self._cache_path.start()

    def tearDown(self):
        self._cache_path.stop()
        self._tmp.cleanup()

    def write_cache(self, special=None):
        cache = {"fetched_at": "2026-10-22T10:00:00+00:00",
                 "providers": {"openai-codex": {"windows": []}}}
        if special is not None:
            cache["codex_special_reset"] = special
        self.cache_path.write_text(json.dumps(cache), encoding="utf-8")

    def render(self):
        return json.loads(self.commands._render_quota_json())

    def test_absent_backend_key_renders_a_stable_unknown_shape(self):
        self.write_cache(None)

        special = self.render()["codex_special_reset"]

        self.assertEqual(special, {
            "announcement": None,
            "fetched_at": None,
            "stale": True,
            "unavailable_reason": "not-fetched",
        })

    def test_valid_announcement_is_passed_through_exactly(self):
        entry = {
            "announcement": {
                "id": "reset-10", "status": "scheduled", "reset_type": "regular",
                "announced_at": "2026-10-21T09:30:00+00:00",
                "scheduled_for": None,
                "source_url": "https://x.com/thsottiaux/status/789",
            },
            "fetched_at": "2026-10-22T10:00:00+00:00",
            "stale": False,
            "unavailable_reason": None,
        }
        self.write_cache(entry)

        self.assertEqual(self.render()["codex_special_reset"], entry)

    def test_cached_cli_path_never_calls_the_announcement_source(self):
        self.write_cache(None)
        args = type("Args", (), {
            "quota_command": "status", "json": True, "cached": True,
            "max_age": None, "version_json": False,
        })()
        with mock.patch.object(self.qc.codex_special_reset, "refresh",
                               side_effect=AssertionError("network on cached path")), \
             mock.patch("builtins.print") as printed:
            self.commands._handle_cli(args)

        self.assertEqual(printed.call_count, 1)
        payload = json.loads(printed.call_args.args[0])
        self.assertIn("codex_special_reset", payload)
        self.assertEqual(payload["codex_special_reset"]["unavailable_reason"],
                         "not-fetched")

    def test_untrusted_cached_announcement_is_dropped_not_shown(self):
        self.write_cache({
            "announcement": {
                "id": "evil", "status": "recorded", "reset_type": "regular",
                "announced_at": "2026-10-21T09:30:00+00:00",
                "scheduled_for": None,
                "source_url": "https://example.com/thsottiaux/status/789",
            },
            "fetched_at": "2026-10-22T10:00:00+00:00",
            "stale": False,
            "unavailable_reason": None,
        })

        special = self.render()["codex_special_reset"]

        self.assertIsNone(special["announcement"])
        self.assertTrue(special["stale"])

    def test_announcement_without_a_trustworthy_success_time_is_stale(self):
        valid = {
            "announcement": {
                "id": "reset-12", "status": "recorded", "reset_type": "regular",
                "announced_at": "2026-10-21T09:30:00+00:00",
                "scheduled_for": None,
                "source_url": "https://x.com/thsottiaux/status/789",
            },
            "fetched_at": None,
            "stale": False,
            "unavailable_reason": None,
        }
        for value in (valid, {**valid, "fetched_at": "yesterday"}):
            with self.subTest(fetched_at=value["fetched_at"]):
                self.write_cache(value)
                special = self.render()["codex_special_reset"]
                self.assertIsNotNone(special["announcement"])
                self.assertTrue(special["stale"])


class _FakeResponse:
    def __init__(self, body=b"{}", status=200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {
            "Cache-Control": "max-age=14400",
            "Content-Type": "application/json; charset=utf-8",
        }
        self.closed = False

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def close(self):
        self.closed = True


class TransportSafetyTests(unittest.TestCase):
    def test_default_request_is_anonymous_and_disables_redirect_proxies(self):
        sr = module()
        response = _FakeResponse()
        with mock.patch.object(sr.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            status, _headers, _body = sr._default_transport(
                sr.ENDPOINT, 2.0, '"etag-1"')

        self.assertEqual(status, 200)
        handlers = build.call_args.args
        proxies = [h for h in handlers
                   if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        self.assertTrue(any(isinstance(h, sr._NoRedirect) for h in handlers))
        opener = build.return_value
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, sr.ENDPOINT)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(request.get_header("If-none-match"), '"etag-1"')
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2.0)
        self.assertTrue(response.closed)

    def test_redirect_is_refused_without_reading_the_target(self):
        sr = module()
        error = urllib.error.HTTPError(
            sr.ENDPOINT, 302, "Found", {"Location": "https://evil.example"}, None)
        with mock.patch.object(sr.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(sr.SpecialResetError) as raised:
                sr._default_transport(sr.ENDPOINT, 2.0, None)
        self.assertEqual(raised.exception.reason, "redirect")

    def test_oversize_body_is_rejected_by_the_default_transport(self):
        sr = module()
        response = _FakeResponse(body=b"x" * (sr.MAX_BODY_BYTES + 1))
        with mock.patch.object(sr.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            with self.assertRaises(sr.SpecialResetError) as raised:
                sr._default_transport(sr.ENDPOINT, 2.0, None)
        self.assertEqual(raised.exception.reason, "oversize")

    def test_non_json_200_response_is_rejected_by_the_default_transport(self):
        sr = module()
        response = _FakeResponse(
            body=b"<html>not json</html>",
            headers={"Cache-Control": "max-age=14400",
                     "Content-Type": "text/html; charset=utf-8"},
        )
        with mock.patch.object(sr.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            with self.assertRaises(sr.SpecialResetError) as raised:
                sr._default_transport(sr.ENDPOINT, 2.0, None)
        self.assertEqual(raised.exception.reason, "content-type")
        self.assertTrue(response.closed)

    def test_missing_content_type_200_response_is_rejected(self):
        sr = module()
        response = _FakeResponse(
            body=b"{}", headers={"Cache-Control": "max-age=14400"})
        with mock.patch.object(sr.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            with self.assertRaises(sr.SpecialResetError) as raised:
                sr._default_transport(sr.ENDPOINT, 2.0, None)
        self.assertEqual(raised.exception.reason, "content-type")

    def test_announcement_refresh_never_opens_provider_credentials(self):
        sr = module()
        with tempfile.TemporaryDirectory() as home:
            auth = Path(home) / "auth.json"
            auth.write_text('{"credential_pool": {"openai-codex": []}}',
                            encoding="utf-8")
            stub = types.ModuleType("hermes_constants")
            stub.get_hermes_home = lambda: Path(home)
            opened = []
            real_open = open

            def tracking(file, *args, **kwargs):
                opened.append(str(file))
                return real_open(file, *args, **kwargs)

            with mock.patch.dict(sys.modules, {"hermes_constants": stub}), \
                 mock.patch("builtins.open", side_effect=tracking):
                entry = sr.refresh(
                    None,
                    transport=ok_transport(payload(record("latest-11"), None)),
                    now=lambda: 7_000_000.0,
                )

        self.assertFalse(entry["stale"])
        self.assertFalse(any(path.endswith("auth.json") for path in opened))


if __name__ == "__main__":
    unittest.main()
