from __future__ import annotations

import sys
from datetime import datetime, timezone

from .config_store import ConfigStore


class AppLogger:
    def __init__(self, store: ConfigStore):
        self.store = store

    def log(self, level: str, category: str, message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rendered = f"[{now}] {level.upper():7s} [{category}] {message}"
        # stdout/journal logging must never depend on SQLite being writable.
        # Print first so the original event is preserved even if the event DB is
        # temporarily unavailable (for example during resource pressure).
        print(rendered, file=sys.stdout, flush=True)
        try:
            self.store.add_event(now, level.upper(), category, message)
        except Exception as exc:
            # Logging a logging/database failure back through AppLogger would
            # recurse. Emit one direct stderr line and let the caller continue.
            print(
                f"[{now}] WARNING [logging] event database write failed: "
                f"{exc.__class__.__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    def info(self, category: str, message: str) -> None:
        self.log("INFO", category, message)

    def warning(self, category: str, message: str) -> None:
        self.log("WARNING", category, message)

    def error(self, category: str, message: str) -> None:
        self.log("ERROR", category, message)
