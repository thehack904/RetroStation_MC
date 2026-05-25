from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "config.db"
SQLITE_TIMEOUT_SECONDS = 30
LOCKED_WRITE_RETRIES = 5
LOCKED_WRITE_RETRY_BASE_SECONDS = 0.05
MAX_APP_EVENTS_DB_BYTES = 500 * 1024 * 1024  # 500 MB
APP_EVENTS_KEEP_FRACTION_ON_TRIM = 0.25
APP_EVENTS_MIN_KEEP_ROWS = 1000

DEFAULT_CONFIG: Dict[str, Any] = {
    "playlist_source": str(BASE_DIR / "sample_data" / "channels.m3u"),
    "xmltv_source": str(BASE_DIR / "sample_data" / "xmltv.xml"),
    "theme": "retrostation_mc",
    "resolution": "1280x720",
    "fps": 15,
    "segment_seconds": 6,
    "page_seconds": 12,
    "visible_rows": 8,
    "guide_minutes": 90,
    "channel_group": "",
    "title": "Guide Channel",
    "timezone": "local",
    "browser_timezone": "",
    "output_format": "both",
    "transition": "scroll",
    "guide_logo_mode": "default",  # "default" | "custom" | "disabled"
    "guide_logo_custom_file": "",
    # Background music settings
    "music_mode": "none",        # "none" | "single" | "playlist"
    "music_loop": False,         # loop the audio
    "music_single_file": "",     # selected filename for single mode
    "music_playlist_files": [],  # ordered list of filenames for playlist mode
    # Diagnostics / HLS live-edge tuning
    "diag_delay_segments": 2,
    "diag_min_buffer_secs": 18,
    "diag_min_buffer_segments": 3,
    "diag_standby_window_segments": 3,
    "diag_log_tail_lines": 120,
}


class ConfigStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._enforce_event_storage_limit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=SQLITE_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_locked_error(self, exc: sqlite3.OperationalError) -> bool:
        error_code = getattr(exc, "sqlite_errorcode", None)
        if error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return True
        message = str(exc).lower()
        return "database is locked" in message or "database table is locked" in message

    def _run_write(self, writer: Callable[[sqlite3.Connection], None]) -> None:
        for attempt in range(LOCKED_WRITE_RETRIES):
            try:
                with self._connect() as conn:
                    writer(conn)
                    conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if not self._is_locked_error(exc) or attempt == LOCKED_WRITE_RETRIES - 1:
                    raise
                time.sleep(LOCKED_WRITE_RETRY_BASE_SECONDS * (2 ** attempt))

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            conn.commit()

        existing = self.get_config()
        if not existing:
            self.save_config(DEFAULT_CONFIG)

    def _event_db_size_bytes(self) -> int:
        try:
            return int(self.db_path.stat().st_size)
        except OSError:
            return 0

    def _enforce_event_storage_limit(self) -> None:
        if self._event_db_size_bytes() <= MAX_APP_EVENTS_DB_BYTES:
            return
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM app_events").fetchone()
            total = int(row["n"]) if row else 0
            keep_rows = max(APP_EVENTS_MIN_KEEP_ROWS, int(total * APP_EVENTS_KEEP_FRACTION_ON_TRIM))
            if keep_rows < total:
                conn.execute(
                    """
                    DELETE FROM app_events
                    WHERE id NOT IN (
                        SELECT id FROM app_events ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (keep_rows,),
                )
                conn.commit()
        # VACUUM must run outside any active write transaction; use a fresh
        # connection after the DELETE commit so SQLite can reclaim disk pages.
        with self._connect() as conn:
            conn.execute("VACUUM")
            conn.commit()

    def get_config(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        config: Dict[str, Any] = {}
        for row in rows:
            try:
                config[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                config[row["key"]] = row["value"]
        return config

    def save_config(self, config: Dict[str, Any]) -> None:
        existing = self.get_config()
        merged = {**DEFAULT_CONFIG, **existing, **config}

        def _write(conn: sqlite3.Connection) -> None:
            for key, value in merged.items():
                conn.execute(
                    "REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )

        self._run_write(_write)

    def add_event(self, created_at: str, level: str, category: str, message: str) -> None:
        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO app_events (created_at, level, category, message) VALUES (?, ?, ?, ?)",
                (created_at, level, category, message),
            )

        self._run_write(_write)
        self._enforce_event_storage_limit()

    def get_recent_events(self, limit: int = 100):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, level, category, message FROM app_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_events(self, limit: int | None = None, offset: int = 0):
        query = "SELECT created_at, level, category, message FROM app_events ORDER BY id DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params = (limit, max(0, offset))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_events(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM app_events").fetchone()
        return int(row["n"]) if row else 0
