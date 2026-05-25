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


if __name__ == "__main__":
    unittest.main()
