"""Accounts view under the read-only trial: active-profile shape, unavailable pool metadata,
no credential-store read, and mutations refused before any state read or child process.

The pool *rendering* helpers are still covered with in-memory synthetic rows; end-to-end pool
listing is unavailable because no supported, sanitized metadata API exists in this trial.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from monitor_fixtures import Home, load

accounts = load("quota_monitor.accounts")
qm = load("quota_monitor")
store = load("quota_monitor.store")
commands = load("commands")

SECRET_BITS = ("SYNTH-ACCESS", "SYNTH-REFRESH", "sk-synthetic", "SYNTH-AGENT")


def _row(rid, label, priority, source="manual", auth_type="oauth", **extra):
    row = {"id": rid, "label": label, "auth_type": auth_type, "priority": priority, "source": source,
           "access_token": "SYNTH-ACCESS-" + rid, "refresh_token": "SYNTH-REFRESH-" + rid,
           "agent_key": "SYNTH-AGENT-" + rid, "base_url": "https://synthetic.invalid/v1", "request_count": 3}
    row.update(extra)
    return row


def build_root(base: Path) -> Path:
    """A synthetic root with decoy credential stores that the trial must never open."""
    root = base / "root"
    root.mkdir(parents=True)
    now = time.time()
    (root / "auth.json").write_text(json.dumps({"version": 1, "credential_pool": {
        "anthropic": [_row("aa11bb", "work", 0), _row("cc22dd", "home", 1),
                      _row("ee33ff", "spare", 2, source="claude_code", model_cooldowns={"claude-opus": now + 900})],
        "openrouter": [_row("0a1b2c", "key1", 0, auth_type="api_key", source="env:OPENROUTER_API_KEY",
                            api_key="sk-synthetic")],
    }}))
    (root / "config.yaml").write_text(json.dumps({
        "model": {"provider": "anthropic", "default": "claude-synth"},
        "fallback_providers": [{"provider": "openrouter", "model": "synth/model-a",
                                "api_key": "sk-synthetic", "base_url": "https://synthetic.invalid"}],
        "credential_pool_strategies": {"anthropic": "round_robin"},
        "plugins": {"entries": {"quota": {"settings": {"monitorEnabled": True}}}},
    }))
    (root / "quota_cache.json").write_text(json.dumps({
        "fetched_at": "2026-10-22T10:00:00+00:00",
        "providers": {
            "anthropic": {"label": "anthropic", "plan": "max", "unavailable_reason": None, "details": [],
                          "windows": [{"label": "5h", "used_percent": 40,
                                       "reset_at": "2026-10-22T12:00:00+00:00"}]},
            "nous": {"label": "nous", "unavailable_reason": "not-logged-in", "details": [], "windows": []},
            "grok": {"label": "grok", "unavailable_reason": "opt-in-disabled", "details": [], "windows": []},
            "copilot": {"label": "copilot", "unavailable_reason": "no-credentials", "details": [], "windows": []},
            "openai-codex": {"label": "openai-codex", "unavailable_reason": "reauth-required",
                             "details": [], "windows": []},
            "gemini": {"label": "gemini", "unavailable_reason": "rate-limited", "details": [], "windows": []},
        }}))
    return root


class _Env:
    def __init__(self, home: Path):
        self.home = home

    def __enter__(self):
        self.patch = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class RowRenderingTests(unittest.TestCase):
    """Pure helpers keep their shape coverage without reading any store."""

    def test_rows_order_status_and_tried_first(self):
        now = time.time()
        entries = [_row("aa11bb", "work", 0),
                   _row("cc22dd", "home", 1, last_status="exhausted", last_error_reset_at=now + 3600),
                   _row("ee33ff", "spare", 2, model_cooldowns={"claude-opus": now + 900})]
        rows = [accounts._row("anthropic", e, i, False, now) for i, e in enumerate(accounts._ordered(entries))]
        self.assertEqual([r["label"] for r in rows], ["work", "home", "spare"])
        self.assertEqual([r["status"] for r in rows], ["active", "exhausted", "cooldown"])
        self.assertEqual(rows[2]["cooldown_models"], ["claude-opus"])
        self.assertIsNotNone(rows[1]["status_until"])
        self.assertNotIn("access_token", json.dumps(rows))

    def test_quota_is_matched_by_label_only(self):
        now = time.time()
        qidx = accounts._quota_index([
            {"provider": "anthropic", "base": "anthropic", "windows": [{"label": "5h", "used": 40}]},
            {"provider": "anthropic:home", "base": "anthropic", "account_label": "home",
             "windows": [{"label": "5h", "used": 91}]},
        ])
        rows = [accounts._row("anthropic", _row("aa11bb", "work", 0), 0, False, now),
                accounts._row("anthropic", _row("cc22dd", "home", 1), 1, False, now)]
        accounts._attach_quota("anthropic", rows, qidx)
        self.assertEqual(rows[1]["quota_match"], "label")
        self.assertEqual(rows[1]["windows"][0]["used"], 91)
        self.assertEqual(rows[0]["windows"], [])


class AccountsListingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qm-acc-"))
        self.root = build_root(self.tmp)
        self.env = _Env(self.root)
        self.env.__enter__()

    def tearDown(self):
        self.env.__exit__()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self):
        return accounts.accounts_payload()

    def test_metadata_is_unavailable_and_no_credentials_are_claimed(self):
        p = self.payload()
        self.assertEqual(p["pools"], [])
        self.assertEqual(p["metadata"], {"available": False, "reason": "no-supported-sanitized-host-api"})
        for key in ("set_priority", "set_strategy", "reorder_fallbacks", "assign_account_to_profile"):
            self.assertFalse(p["capabilities"][key])
        self.assertEqual([x["profile"] for x in p["profiles"]], ["default"])

    def test_credential_store_is_never_opened(self):
        with mock.patch.object(accounts, "read_json", side_effect=AssertionError("credential read")):
            p = self.payload()
        self.assertEqual(p["pools"], [])
        # a synthetic auth.json exists; nothing in the payload may quote it
        text = json.dumps(p)
        for bit in SECRET_BITS + ("synthetic.invalid", "refresh_token", "access_token", "agent_key"):
            self.assertNotIn(bit, text)

    def test_profiles_models_and_fallbacks_come_from_the_active_profile(self):
        prof = {x["profile"]: x for x in self.payload()["profiles"]}["default"]
        self.assertEqual(prof["model"], {"provider": "anthropic", "model": "claude-synth"})
        self.assertEqual(prof["fallbacks"],
                         [{"provider": "openrouter", "model": "synth/model-a", "custom_route": True}])
        self.assertFalse(prof["fallback_editable"])
        self.assertEqual(prof["strategies"], {"anthropic": "round_robin"})

    def test_unavailable_reasons_are_classified(self):
        un = {u["provider"]: u for u in self.payload()["unavailable"]}
        self.assertEqual(un["nous"]["kind"], "signin")
        self.assertEqual(un["nous"]["command"], "hermes auth add nous")
        self.assertEqual(un["copilot"]["command"], "gh auth login")
        self.assertEqual(un["openai-codex"]["kind"], "reauth")
        self.assertEqual(un["grok"]["kind"], "optin")
        self.assertEqual(un["gemini"]["kind"], "rate_limited")
        self.assertIsNone(un["gemini"]["command"])

    def test_anthropic_rate_limit_retry_time(self):
        now = time.time()
        (self.root / "quota_anthropic_backoff.json").write_text(
            json.dumps({"digest1": now + 600, "old": now - 5}))
        cache = json.loads((self.root / "quota_cache.json").read_text())
        cache["providers"]["anthropic"]["unavailable_reason"] = "rate-limited"
        (self.root / "quota_cache.json").write_text(json.dumps(cache))
        un = {u["provider"]: u for u in self.payload()["unavailable"]}
        self.assertAlmostEqual(un["anthropic"]["retry_at"], now + 600, delta=1)

    def test_active_profile_detection_and_sibling_exclusion(self):
        self.assertEqual(self.payload()["active_profile"], "default")
        sibling = self.root / "profiles" / "coder"
        sibling.mkdir(parents=True)
        (sibling / "auth.json").write_text(json.dumps({"version": 1, "credential_pool": {
            "anthropic": [_row("5a5a5a", "solo-acc", 0)]}}))
        (sibling / "config.yaml").write_text(json.dumps({
            "model": {"provider": "openrouter", "default": "sibling-model"},
            "plugins": {"entries": {"quota": {"settings": {"monitorEnabled": True}}}}}))
        with _Env(sibling):
            p = accounts.accounts_payload()
            self.assertEqual(p["active_profile"], "coder")
            self.assertEqual([x["profile"] for x in p["profiles"]], ["coder"])
            self.assertEqual(p["profiles"][0]["model"]["model"], "sibling-model")

    def test_accounts_view_is_gated_by_enrollment(self):
        home = Home()
        try:
            with _Env(home.root):
                self.assertEqual(qm.accounts_view()["error_code"], "monitor-enrollment-off")
            home.enroll()
            with _Env(home.root):
                self.assertNotIn("error", qm.accounts_view())
        finally:
            home.close()

    def test_accounts_view_does_not_read_or_forward_legacy_credential_audit(self):
        legacy = [{"action": "priority", "profile": "default", "provider": "anthropic",
                   "detail": {"access_token": "SYNTH-LEGACY-AUDIT-SECRET"}}]
        with mock.patch.object(store, "audit_rows", return_value=legacy) as audit:
            payload = qm.accounts_view()
        audit.assert_not_called()
        self.assertNotIn("recent_changes", payload)
        self.assertNotIn("SYNTH-LEGACY-AUDIT-SECRET", json.dumps(payload))


class AccountsWriteTests(unittest.TestCase):
    """Account mutations are unavailable: refusal happens before any state read or CLI process."""

    def test_priority_and_strategy_refuse_before_state_or_runner(self):
        with mock.patch.object(accounts, "accounts_payload", side_effect=AssertionError("state read")):
            for fn, args in ((accounts.set_priority, ("anthropic", "ee33ff", 0, "default")),
                             (accounts.set_priority, ("bad provider!", "x", -1, "../x")),
                             (accounts.set_strategy, ("anthropic", "round_robin", "coder")),
                             (accounts.set_strategy, ("xai", "fastest", "nobody"))):
                with self.subTest(fn=fn.__name__, args=args):
                    with self.assertRaises(accounts.AccountsError) as cm:
                        fn(*args)
                    self.assertEqual(cm.exception.code, "trial-read-only")

    def test_refusal_does_not_touch_credential_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="qm-accw-"))
        try:
            root = build_root(tmp)
            before = (root / "auth.json").read_bytes()
            with _Env(root):
                with self.assertRaises(accounts.AccountsError):
                    accounts.set_priority("anthropic", "ee33ff", 0, profile="default")
                with self.assertRaises(accounts.AccountsError):
                    accounts.set_strategy("anthropic", "random", profile="default")
            self.assertEqual((root / "auth.json").read_bytes(), before)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_command_surface_reports_the_trial_error(self):
        parser = argparse.ArgumentParser()
        commands.setup_argparse(parser)
        args = parser.parse_args(["monitor", "set-priority", "--provider", "anthropic",
                                  "--target", "ab12cd", "--priority", "0", "--json"])
        self.assertEqual((args.monitor_command, args.provider, args.target, args.priority),
                         ("set-priority", "anthropic", "ab12cd", "0"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = args.func(args)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(buf.getvalue())["error_code"], "trial-read-only")


if __name__ == "__main__":
    unittest.main()
