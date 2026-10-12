"""History tests never read live credentials or write the real profile home."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
pkg = types.ModuleType("quota_history_test")
pkg.__path__ = [str(ROOT)]
sys.modules["quota_history_test"] = pkg
history = importlib.import_module("quota_history_test.quota_history")


def snapshot(stamp="2026-01-01T00:00:00+00:00", used=20, reset="2026-01-08T00:00:00+00:00", reason=None):
    return {"fetched_at": stamp, "providers": {"grok": {
        "label": "grok", "history_id": "a" * 64, "unavailable_reason": reason,
        "windows": [{"label": "week", "used_percent": used, "reset_at": reset}],
    }}}


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.sqlite3"
        self.patch = mock.patch.object(history, "_path", return_value=self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_idempotent_samples(self):
        history.record_snapshot(snapshot())
        history.record_snapshot(snapshot())
        self.assertEqual(len(history.read_history()), 1)

    def test_errors_preserved_as_errors_not_zero(self):
        history.record_snapshot(snapshot(reason="timeout"))
        row = history.read_history()[0]
        self.assertEqual(row["status"], "timeout")
        self.assertIsNone(row["used_percent"])

    def test_secret_fields_not_persisted(self):
        sample = snapshot()
        sample["providers"]["grok"].update(label="Bearer secret-value", access_token="secret-value", raw={"secret": "secret-value"}, details=["secret-value"])
        history.record_snapshot(sample)
        self.assertNotIn("secret-value", json.dumps(history.read_history()))
        self.assertNotIn(b"secret-value", self.path.read_bytes())

    def test_concurrent_writers_commit_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(history.record_snapshot, [snapshot()] * 8))
        self.assertEqual(len(history.read_history()), 1)

    def test_retention_drops_only_old_samples(self):
        history.record_snapshot(snapshot())
        history.record_snapshot(snapshot("2026-04-10T00:00:00+00:00"))
        self.assertEqual(len(history.read_history(days=90)), 1)

    def test_daily_delta_does_not_bridge_errors_reset_or_long_gaps(self):
        history.record_snapshot(snapshot())
        history.record_snapshot(snapshot("2026-01-01T01:00:00+00:00", used=23))
        history.record_snapshot(snapshot("2026-01-01T02:00:00+00:00", reason="timeout"))
        history.record_snapshot(snapshot("2026-01-01T03:00:00+00:00", used=40))
        history.record_snapshot(snapshot("2026-01-01T04:00:00+00:00", used=2, reset="2026-01-09T00:00:00+00:00"))
        history.record_snapshot(snapshot("2026-01-01T08:00:00+00:00", used=20, reset="2026-01-09T00:00:00+00:00"))
        self.assertEqual([r["observed_percentage_points"] for r in history.daily_changes(history.read_history())], [3])

    def test_unknown_identities_are_not_joined(self):
        for stamp in ("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00"):
            sample = snapshot(stamp)
            del sample["providers"]["grok"]["history_id"]
            history.record_snapshot(sample)
        self.assertEqual(len(history.read_history()), 2)
        self.assertEqual(history.daily_changes(history.read_history()), [])

    def test_storage_failure_is_not_reported_as_success(self):
        with mock.patch.object(history, "_connect", side_effect=OSError("unwritable")):
            with self.assertRaises(OSError):
                history.record_snapshot(snapshot())

    def test_symlink_history_path_is_refused(self):
        target = Path(self.tmp.name) / "unrelated"
        target.write_text("preserve me")
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            history.record_snapshot(snapshot())
        self.assertEqual(target.read_text(), "preserve me")

    def test_collect_cli_is_quiet_and_durable(self):
        import argparse
        import contextlib
        import io
        constants = types.ModuleType("hermes_constants")
        setattr(constants, "get_hermes_home", lambda: self.path.parent)
        with mock.patch.dict(sys.modules, {"hermes_constants": constants}):
            commands = importlib.import_module("quota_history_test.commands")
        parser = argparse.ArgumentParser()
        commands.setup_argparse(parser)
        output = io.StringIO()
        with mock.patch.object(commands, "refresh_quota_cache", return_value=snapshot()), contextlib.redirect_stdout(output):
            commands._handle_cli(parser.parse_args(["collect"]))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(history.read_history()), 1)
        with mock.patch.object(history, "record_snapshot", side_effect=OSError("unwritable")), \
             mock.patch.object(commands, "refresh_quota_cache", return_value=snapshot()):
            with self.assertRaises(OSError):
                commands._handle_cli(parser.parse_args(["collect"]))

    def test_account_and_window_separation(self):
        sample = snapshot()
        sample["providers"]["openai-codex"] = {"accounts": [
            {"label": "one", "history_id": "b" * 64, "windows": [{"label": "week", "used_percent": 20}]},
            {"label": "two", "history_id": "c" * 64, "windows": [{"label": "week", "used_percent": 80}]},
        ]}
        history.record_snapshot(sample)
        self.assertEqual(len(history.read_history()), 3)
        self.assertEqual(len({r["account"] for r in history.read_history()}), 3)

if __name__ == "__main__":
    unittest.main()
