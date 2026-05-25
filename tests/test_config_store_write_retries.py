from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call
from unittest.mock import patch

from app.config_store import (
    ConfigStore,
    LOCKED_WRITE_RETRIES,
    LOCKED_WRITE_RETRY_BASE_SECONDS,
    SQLITE_TIMEOUT_SECONDS,
)


class ConfigStoreWriteRetriesTests(unittest.TestCase):
    def test_connect_sets_busy_timeout_from_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.db")
            with store._connect() as conn:
                timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout_ms, int(SQLITE_TIMEOUT_SECONDS * 1000))

    def test_run_write_retries_transient_locked_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.db")
            calls = {"count": 0}

            def writer(conn: sqlite3.Connection) -> None:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise sqlite3.OperationalError("database is locked")
                conn.execute("CREATE TABLE IF NOT EXISTS retry_probe (id INTEGER PRIMARY KEY)")

            with patch("app.config_store.time.sleep") as sleep:
                store._run_write(writer)

        self.assertEqual(calls["count"], 2)
        sleep.assert_called_once_with(LOCKED_WRITE_RETRY_BASE_SECONDS)

    def test_run_write_stops_after_max_locked_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.db")
            calls = {"count": 0}

            def writer(_conn: sqlite3.Connection) -> None:
                calls["count"] += 1
                raise sqlite3.OperationalError("database is locked")

            with patch("app.config_store.time.sleep") as sleep:
                with self.assertRaises(sqlite3.OperationalError):
                    store._run_write(writer)

        self.assertEqual(calls["count"], LOCKED_WRITE_RETRIES)
        self.assertEqual(sleep.call_count, LOCKED_WRITE_RETRIES - 1)
        expected = [call(LOCKED_WRITE_RETRY_BASE_SECONDS * (2 ** attempt)) for attempt in range(LOCKED_WRITE_RETRIES - 1)]
        sleep.assert_has_calls(expected)

    def test_enforce_event_storage_limit_trims_oldest_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.db")
            for idx in range(20):
                store.add_event(f"2026-01-01T00:00:{idx:02d}+00:00", "INFO", "test", f"event-{idx}")

            with patch("app.config_store.MAX_APP_EVENTS_DB_BYTES", 1):
                with patch("app.config_store.APP_EVENTS_KEEP_FRACTION_ON_TRIM", 0.25):
                    with patch("app.config_store.APP_EVENTS_MIN_KEEP_ROWS", 2):
                        with patch.object(store, "_event_db_size_bytes", return_value=10_000):
                            store._enforce_event_storage_limit()

            events = store.get_events()
            self.assertEqual(len(events), 5)
            self.assertEqual(events[0]["message"], "event-19")
            self.assertEqual(events[-1]["message"], "event-15")

    def test_init_enforces_event_storage_limit_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "config.db"
            store = ConfigStore(db_path)
            for idx in range(12):
                store.add_event(f"2026-01-01T00:01:{idx:02d}+00:00", "INFO", "test", f"boot-{idx}")

            with patch("app.config_store.MAX_APP_EVENTS_DB_BYTES", 1):
                with patch("app.config_store.APP_EVENTS_KEEP_FRACTION_ON_TRIM", 0.25):
                    with patch("app.config_store.APP_EVENTS_MIN_KEEP_ROWS", 1):
                        with patch("app.config_store.ConfigStore._event_db_size_bytes", return_value=10_000):
                            restarted = ConfigStore(db_path)

            self.assertEqual(restarted.count_events(), 3)


if __name__ == "__main__":
    unittest.main()
