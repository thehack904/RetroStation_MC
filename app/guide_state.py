from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "guide_state.json"
THEMES_DIR = BASE_DIR / "app" / "themes"


def load_theme(theme_name: str) -> dict[str, Any]:
    theme_path = THEMES_DIR / theme_name / "theme.json"
    return json.loads(theme_path.read_text(encoding="utf-8"))


def build_state(config: dict, channels: list[dict], programmes: dict[str, list[dict]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    guide_start = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0)
    horizon = guide_start + timedelta(minutes=int(config.get("guide_minutes", 90)))
    group_filter = (config.get("channel_group") or "").strip()

    filtered_channels = []
    for channel in channels:
        if group_filter and channel.get("group", "") != group_filter:
            continue
        channel_id = channel["id"]
        current_programs = []
        for item in programmes.get(channel_id, []):
            item_start = datetime.fromisoformat(item["start"])
            item_stop = datetime.fromisoformat(item["stop"])
            if item_stop <= guide_start or item_start >= horizon:
                continue
            current_programs.append(item)
        if not current_programs:
            current_programs = [
                {
                    "title": "No guide data",
                    "desc": "Fallback placeholder block",
                    "start": guide_start.isoformat(),
                    "stop": horizon.isoformat(),
                }
            ]
        filtered_channels.append(
            {
                **channel,
                "programs": current_programs[:8],
            }
        )

    visible_rows = max(3, int(config.get("visible_rows", 8)))
    pages = [
        filtered_channels[i : i + visible_rows]
        for i in range(0, max(len(filtered_channels), 1), visible_rows)
    ] or [[]]

    state = {
        "generated_at": now.isoformat(),
        "theme": config.get("theme", "retrostation_mc"),
        "title": config.get("title", "Guide Channel"),
        "display": {
            "resolution": config.get("resolution", "1280x720"),
            "fps": int(config.get("fps", 15)),
            "page_seconds": int(config.get("page_seconds", 12)),
            "visible_rows": visible_rows,
            "guide_minutes": int(config.get("guide_minutes", 90)),
            "transition": config.get("transition", "scroll"),
            "timezone": config.get("timezone", "local"),
            "browser_timezone": config.get("browser_timezone", ""),
        },
        "time_window": {
            "start": guide_start.isoformat(),
            "end": horizon.isoformat(),
        },
        "theme_data": load_theme(config.get("theme", "retrostation_mc")),
        "pages": pages,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))
