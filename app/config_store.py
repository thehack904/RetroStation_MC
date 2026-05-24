from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "config.db"

DEFAULT_CONFIG: Dict[str, Any] = {
    "playlist_source": str(BASE_DIR / "sample_data" / "channels.m3u"),
    "xmltv_source": str(BASE_DIR / "sample_data" / "xmltv.xml"),
    "theme": "classic_blue",
    "resolution": "1280x720",
    "fps": 15,
    "segment_seconds": 6,
    "page_seconds": 12,
    "visible_rows": 8,
    "guide_minutes": 90,
    "channel_group": "",
    "title": "Guide Channel",
    "timezone": "local",
    "output_format": "both",
    "transition": "scroll",
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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
        with self._connect() as conn:
            for key, value in merged.items():
                conn.execute(
                    "REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            conn.commit()

    def add_event(self, created_at: str, level: str, category: str, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_events (created_at, level, category, message) VALUES (?, ?, ?, ?)",
                (created_at, level, category, message),
            )
            conn.commit()

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
