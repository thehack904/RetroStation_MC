from __future__ import annotations

import sys
from datetime import datetime, timezone

from .config_store import ConfigStore


class AppLogger:
    def __init__(self, store: ConfigStore):
        self.store = store

    def log(self, level: str, category: str, message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store.add_event(now, level.upper(), category, message)
        # Also print to stdout so log lines appear alongside Flask's access log.
        print(f"[{now}] {level.upper():7s} [{category}] {message}", file=sys.stdout, flush=True)

    def info(self, category: str, message: str) -> None:
        self.log("INFO", category, message)

    def warning(self, category: str, message: str) -> None:
        self.log("WARNING", category, message)

    def error(self, category: str, message: str) -> None:
        self.log("ERROR", category, message)
