"""Trial policy boundaries: default-off enrollment, active-profile-only scope, no external CLI
collection, no shared Kanban reads, unavailable account metadata and no account/budget writes.

The trial is deliberately read-only at the backend, so these tests pin what the plugin may collect
rather than only what it renders.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor_fixtures import Home, iso, load, walk_keys

qm = load('quota_monitor')
common = load('quota_monitor.common')
trial = load('quota_monitor.trial')
accounts = load('quota_monitor.accounts')
health = load('quota_monitor.health')
clis = load('quota_monitor.clis')
usage = load('quota_monitor.usage')
live = load('quota_monitor.live')
tools = load('quota_monitor.tools')
commands = load('commands')

ENABLED_CONFIG = """\
plugins:
  entries:
    quota:
      settings:
        monitorEnabled: true
model:
  provider: anthropic
  default: model-a
fallback_providers:
  - provider: openrouter
    model: model-b
"""


def _no_read(*_a, **_k):
    raise AssertionError("the monitor must not read outside its scope")


class _HomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Home(profiles=("work",))

    def tearDown(self) -> None:
        self.home.close()

    def enroll(self) -> None:
        self.home.write("config.yaml", ENABLED_CONFIG)

    def _cli(self, *argv):
        parser = argparse.ArgumentParser()
        commands.setup_argparse(parser)
        args = parser.parse_args(list(argv))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = args.func(args)
        return rc, buf.getvalue()


class EnrollmentTests(_HomeCase):
    def test_enrollment_defaults_off_without_config(self):
        self.assertFalse(trial.monitor_enrolled())
        self.assertEqual(trial.enrollment_status()["setting"], trial.ENROLLMENT_SETTING)

    def test_only_a_literal_boolean_true_enrolls(self):
        self.home.write("config.yaml", "plugins:\n  entries:\n    quota:\n      settings:\n"
                                       "        monitorEnabled: 'true'\n")
        self.assertFalse(trial.monitor_enrolled())
        self.home.write("config.yaml", "plugins:\n  entries:\n    quota:\n      settings:\n"
                                       "        monitorEnabled: 1\n")
        self.assertFalse(trial.monitor_enrolled())
        self.enroll()
        self.assertTrue(trial.monitor_enrolled())

    def test_snapshot_is_inert_until_enrolled(self):
        with patch.object(common, "profile_homes", side_effect=AssertionError("profile read")), \
                patch.object(usage, "usage_today_month", side_effect=AssertionError("usage read")), \
                patch.object(live, "live", side_effect=AssertionError("live read")):
            snap = qm.snapshot()
        self.assertFalse(snap["enrolled"])
        self.assertEqual(snap["collection"], "disabled")
        self.assertEqual(snap["error_code"], "monitor-enrollment-off")
        self.assertNotIn("live", snap)

    def test_every_monitor_payload_is_gated(self):
        for fn in (lambda: qm.history_payload(1), qm.accounts_view, qm.budget_payload,
                   qm.recommend_payload):
            with self.subTest(fn=fn):
                payload = fn()
                self.assertEqual(payload["error_code"], "monitor-enrollment-off")

    def test_cli_reports_the_enrollment_error(self):
        rc, out = self._cli("monitor", "snapshot", "--json")
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertEqual(payload["error_code"], "monitor-enrollment-off")


class InnerReaderEnrollmentTests(_HomeCase):
    """Direct imports of the low-level readers must be as inert as the CLI while monitor is OFF."""

    def _forbid(self, *patches):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target, attr in patches:
            stack.enter_context(patch.object(target, attr, side_effect=AssertionError("state read with monitor off")))

    def test_direct_readers_refuse_before_any_state_access(self):
        now = time.time()
        # No config at all: enrollment is OFF. Any sqlite/file/process access is a failure.
        self._forbid((common, "ro"), (live, "open_leases"), (live, "proc_table"),
                     (live.subprocess, "run"), (accounts, "read_config"),
                     (accounts, "read_json"), (health, "read_config"))
        readers = (
            ("live.live", lambda: live.live(now=now)),
            ("live.model_speed", lambda: live.model_speed([], now - 60)),
            ("live.model_speed_cached", lambda: live.model_speed_cached([])),
            ("usage.usage", lambda: usage.usage(now - 60, now, [iso(now)[:10]])),
            ("usage.top_sessions", lambda: usage.top_sessions(now - 60, now)),
            ("usage.usage_today_month", lambda: usage.usage_today_month()),
            ("health.health", lambda: health.health([], [])),
            ("accounts.accounts_payload", lambda: accounts.accounts_payload(items=[])),
        )
        for label, fn in readers:
            with self.subTest(reader=label):
                with self.assertRaises(trial.TrialError) as caught:
                    fn()
                self.assertEqual(caught.exception.code, "monitor-enrollment-off")

    def test_store_entrypoints_refuse_before_any_sqlite_or_file_access(self):
        from quota_monitor import store
        with patch.object(store.sqlite3, "connect", side_effect=AssertionError("sqlite connect")):
            for fn, args in ((store.kv_get, ("k",)), (store.kv_set, ("k", 1)),
                             (store.audit, ("a", "p", "prov", {})), (store.audit_rows, ()),
                             (store.append_readings, ([],)), (store.history_rows, (0, 1)),
                             (store.add_rate_sample, ({},)), (store.rate_samples, ()),
                             (store.read_prefs, ())):
                with self.subTest(fn=fn.__name__):
                    with self.assertRaises(store.trial.TrialError):
                        fn(*args)

    def test_enrollment_check_does_not_recurse_through_store(self):
        self.enroll()
        self.assertTrue(trial.monitor_enrolled())
        self.assertTrue(trial.monitor_enrolled(self.home.root))


class ProfileScopeTests(_HomeCase):
    def test_default_collection_does_not_enumerate_sibling_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "profiles" / "selected"
            active.mkdir(parents=True)
            (root / "profiles" / "private").mkdir()
            with patch.object(common, "hermes_home", return_value=active):
                self.assertEqual(common.profile_homes(), [("selected", active)])
                self.assertEqual(common.profile_homes(root), [("selected", active)])

    def test_public_readers_clamp_passed_sibling_homes(self):
        self.enroll()
        now = time.time()
        self.home.session("sibling", "cli", profile="work", started=now - 60, last=now - 10)
        sibling = [("work", self.home.homes["work"])]
        self.assertEqual(live.live(homes=sibling, now=now, record_sample=False)["running"], [])
        rows = usage.usage(now - 3600, now + 60, [iso(now)[:10]], homes=sibling)
        self.assertEqual(rows["totals"]["sessions"], 0)
        self.assertEqual(usage.top_sessions(now - 3600, now + 60, homes=sibling), [])
        # The active profile is read under its real label; the sibling name is ignored.
        chains = health.health(sibling, [])["chains"]
        self.assertEqual([c["profile"] for c in chains], ["default"])
        self.assertEqual(trial.allowed_homes(sibling)[0][1], self.home.root)


class ExternalCliBoundaryTests(_HomeCase):
    def test_collection_summary_presence_and_live_never_touch_external_cli_paths(self):
        project = self.home.cli_home / ".claude" / "projects" / "p1"
        project.mkdir(parents=True)
        (project / "sess.jsonl").write_text(json.dumps({
            "type": "assistant", "timestamp": iso(time.time()), "sessionId": "external",
            "message": {"model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 5},
                        "content": []}}) + "\n")
        (self.home.cli_home / ".copilot").mkdir()
        with patch.object(clis.os, "scandir", side_effect=AssertionError("outside profile scan")), \
                patch.object(clis.shutil, "which", side_effect=AssertionError("outside profile probe")):
            sessions, events = clis.collect(time.time() - 3600, time.time() + 60)
            summary = clis.summary(time.time() - 3600, time.time() + 60, [iso(time.time())[:10]])
            minute, active = clis.live([int(time.time() // 60) * 60])
            presence = clis.presence()
        self.assertEqual((sessions, events), ([], []))
        self.assertFalse(summary["available"])
        self.assertEqual(summary["reason"], "external-cli-collection-disabled")
        self.assertEqual(summary["sessions"], [])
        self.assertEqual(summary["presence"], [])
        self.assertEqual(active, [])
        self.assertTrue(all(not by for by in minute.values()))
        self.assertEqual(presence, [])


class KanbanBoundaryTests(_HomeCase):
    def test_kanban_readers_are_disabled(self):
        self.home.card("t_shared", "running", title="shared board card")
        self.assertEqual(usage.kanban_glance(self.home.root), {
            "boards": [], "available": False, "reason": "shared-kanban-disabled"})
        self.assertEqual(live.kanban_cards(self.home.root), {})


class AccountMetadataTests(_HomeCase):
    def test_account_metadata_never_opens_credential_store(self):
        with patch.object(accounts, "read_json", side_effect=AssertionError("credential read")):
            self.assertEqual(accounts._auth_pool(Path("/synthetic")), {})
        with patch.object(health, "_auth_pool", side_effect=AssertionError("credential read")):
            self.assertEqual(health.read_pools(Path("/synthetic")), {})

    def test_accounts_root_cannot_escape_the_active_profile(self):
        self.enroll()
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "other-root"
            outside.mkdir()
            with self.assertRaises(trial.TrialError) as caught:
                accounts.accounts_payload(items=[], root=outside)
            self.assertEqual(caught.exception.code, "monitor-scope-violation")
        payload = accounts.accounts_payload(items=[], root=self.home.root)
        self.assertTrue(payload["active_profile"])

    def test_allowed_homes_uses_the_active_label_not_the_caller_name(self):
        self.enroll()
        sibling = [("spoofed", self.home.root)]
        self.assertEqual(trial.allowed_homes(sibling), [("default", self.home.root)])

    def test_accounts_payload_declares_metadata_unavailable_and_writes_off(self):
        self.enroll()
        with patch.object(accounts, "read_json", side_effect=AssertionError("credential read")):
            payload = accounts.accounts_payload(items=[], now=time.time())
        self.assertEqual(payload["pools"], [])
        self.assertFalse(payload["metadata"]["available"])
        self.assertEqual(payload["metadata"]["reason"], "no-supported-sanitized-host-api")
        self.assertFalse(payload["capabilities"]["set_priority"])
        self.assertFalse(payload["capabilities"]["set_strategy"])

    def test_health_chains_do_not_claim_missing_credentials(self):
        self.home.write("config.yaml", ENABLED_CONFIG)
        h = health.health(common.profile_homes(), [])
        self.assertFalse(h["account_metadata"]["available"])
        chain = next(c for c in h["chains"] if c["profile"] == "default")
        self.assertEqual(chain["fallbacks"][0]["state"], "unknown")
        self.assertEqual(chain["fallbacks"][0]["reason"], "account_metadata_unavailable")
        self.assertNotIn("no_credentials", json.dumps(h))

    def test_account_writes_are_rejected_before_any_runner_or_state_read(self):
        with patch.object(accounts, "accounts_payload", side_effect=AssertionError("state read")):
            for fn, args in ((accounts.set_priority, ("anthropic", "id", 0)),
                             (accounts.set_strategy, ("anthropic", "round_robin"))):
                with self.assertRaises(accounts.AccountsError) as caught:
                    fn(*args)
                self.assertEqual(caught.exception.code, "trial-read-only")

    def test_set_budget_is_rejected_without_writing_prefs(self):
        payload = qm.set_budget(daily=20)
        self.assertEqual(payload["error_code"], "trial-read-only")
        self.assertFalse((self.home.root / "quota_monitor.json").exists())


class AgentToolsTests(_HomeCase):
    def test_tools_do_not_register_in_the_trial(self):
        class Ctx:
            def register_tool(self, **_kwargs):
                raise AssertionError("agent tools must not register")

        self.assertEqual(tools.register_tools(Ctx()), [])

    def test_direct_tool_calls_are_guarded(self):
        with patch.object(qm, "_routing_inputs", side_effect=AssertionError("data read")):
            for handler in (tools.get_quota_status, tools.get_model_health, tools.get_budget,
                            tools.get_running_counts, tools.recommend_model):
                payload = json.loads(handler({}))
                self.assertEqual(payload["error_code"], "trial-tools-disabled")
                self.assertNotIn("title", walk_keys(payload))


class UsageHonestyTests(_HomeCase):
    def test_usage_carries_estimate_basis_not_a_billing_claim(self):
        self.enroll()
        now = time.time()
        self.home.usage_row("s1", tokens=1000, cost=0.5, last_seen=now)
        rows = usage.usage(now - 3600, now + 60, [iso(now)[:10]])
        self.assertEqual(rows["basis"], "estimated")
        self.assertFalse(rows["billing"])
        self.assertIn("not billed", rows["estimate_warning"])

    def test_budget_disables_the_month_end_projection(self):
        self.enroll()
        now = time.time()
        self.home.usage_row("s1", tokens=1000, cost=0.5, last_seen=now)
        b = qm.budget_payload()
        self.assertIsNone(b["month_end_projection"])
        self.assertFalse(b["projection_available"])
        self.assertEqual(b["basis"], "estimated-api-price-equivalent")
        self.assertFalse(b["billing"])
        self.assertIn("estimate_warning", b)


if __name__ == "__main__":
    unittest.main()
