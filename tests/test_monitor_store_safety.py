"""Store file-safety: private DB, no symlink following, read paths never create state."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from monitor_fixtures import Home, load

trial = load("quota_monitor.trial")
store = load("quota_monitor.store")

ENABLED = "plugins:\n  entries:\n    quota:\n      settings:\n        monitorEnabled: true\n"


class StoreSafetyTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(profiles=("work",))
        self.home.write("config.yaml", ENABLED)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = self.home.root / store.DB_NAME

    def tearDown(self):
        self.home.close()
        self.tmp.cleanup()

    def test_new_database_is_private_and_parent_is_not_world_writable(self):
        store.kv_set("k", 1, db=self.db)
        self.assertTrue(self.db.exists())
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777 & 0o022, 0)

    def test_arbitrary_database_paths_cannot_escape_active_profile(self):
        sibling = self.home.homes["work"] / store.DB_NAME
        outside = Path(self.tmp.name) / "outside.db"
        for path in (sibling, outside):
            with self.subTest(path=path):
                with mock.patch.object(store.sqlite3, "connect", side_effect=AssertionError("sqlite opened")) as connect:
                    with self.assertRaises(store.trial.TrialError) as caught:
                        store.kv_set("k", 1, db=path)
                self.assertEqual(caught.exception.code, "monitor-scope-violation")
                connect.assert_not_called()
                self.assertFalse(path.exists())

    def test_read_does_not_create_database_or_follow_absent_path(self):
        self.assertIsNone(store.kv_get("k", db=self.db))
        self.assertEqual(store.history_rows(0, time.time(), db=self.db), [])
        self.assertFalse(self.db.exists())

    def test_symlinked_database_is_refused_and_target_untouched(self):
        target = Path(self.tmp.name) / "target.db"
        sqlite3.connect(str(target)).close()
        before = target.read_bytes()
        self.db.symlink_to(target)
        with self.assertRaises(sqlite3.Error):
            store.kv_set("k", 1, db=self.db)
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue(self.db.is_symlink())

    def test_hostile_sidecar_symlink_is_not_chmodded_through(self):
        store.kv_set("k", 1, db=self.db)
        outside = Path(self.tmp.name) / "outside"
        outside.write_text("x")
        os.chmod(outside, 0o644)
        wal = Path(str(self.db) + "-wal")
        if wal.exists():
            wal.unlink()
        wal.symlink_to(outside)
        # Opening/using the DB must refuse the hostile sidecar, not chmod through it.
        with self.assertRaises(sqlite3.Error):
            store.kv_set("k", 2, db=self.db)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o644)

    def test_concurrent_writer_and_reader_do_not_lock_or_leak_permissions(self):
        errors = []

        def writer():
            try:
                for i in range(40):
                    store.kv_set("k", i, db=self.db)
                    store.append_readings([("p", "w", float(i), None)], now=time.time(), db=self.db)
            except Exception as e:  # noqa: BLE001 - surfaced below
                errors.append(e)

        def reader():
            try:
                for _ in range(40):
                    store.kv_get("k", db=self.db)
                    store.history_rows(0, time.time(), db=self.db)
                    store.rate_samples(db=self.db)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
