from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from typing import Any

from .guide_preview import calculate_preview_layout, effective_preview_aspect_ratio, normalize_preview_aspect_mode, normalize_preview_audio_mode, normalize_preview_source_type

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "guide_state.json"
SECONDARY_STATE_PATH = BASE_DIR / "data" / "guide_state_secondary.json"
THEMES_DIR = BASE_DIR / "app" / "themes"


def load_theme(theme_name: str) -> dict[str, Any]:
    theme_path = THEMES_DIR / theme_name / "theme.json"
    return json.loads(theme_path.read_text(encoding="utf-8"))




def _secondary_ui_scale(resolution: str) -> float:
    """Scale Guide chrome proportionally for native secondary SD rasters.

    1280x720 is the theme design baseline. Using the smaller of width/height
    ratios prevents a 480-line output from merely cropping a 720p-sized UI.
    """
    try:
        width, height = [int(part) for part in str(resolution).lower().split("x", 1)]
    except (TypeError, ValueError):
        return 1.0
    return max(0.50, min(1.0, min(width / 1280.0, height / 720.0)))


def _scaled_secondary_theme(theme_data: dict[str, Any], resolution: str) -> tuple[dict[str, Any], float]:
    scale = _secondary_ui_scale(resolution)
    scaled = json.loads(json.dumps(theme_data))
    layout = scaled.setdefault("layout", {})
    for key in ("header_height", "footer_height", "channel_column_width", "row_height"):
        if key in layout:
            layout[key] = max(2, int(round(float(layout[key]) * scale)))
    if "min_pixels_per_minute" in layout:
        layout["min_pixels_per_minute"] = max(2.0, float(layout["min_pixels_per_minute"]) * scale)
    layout["_ui_scale"] = scale
    return scaled, scale

def build_state(config: dict, channels: list[dict], programmes: dict[str, list[dict]], *, output_path: Path = STATE_PATH) -> dict[str, Any]:
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

    theme_name = config.get("theme", "retrostation_mc")
    theme_data = load_theme(theme_name)
    ui_scale = 1.0
    if output_path == SECONDARY_STATE_PATH:
        theme_data, ui_scale = _scaled_secondary_theme(theme_data, str(config.get("resolution", "720x480")))
    preview_enabled = bool(config.get("guide_preview_enabled", False))
    configured_visible_rows = max(3, int(config.get("visible_rows", 8)))
    preview_layout = calculate_preview_layout(
        str(config.get("resolution", "1280x720")),
        str(config.get("aspect_ratio", "16:9")),
        theme_data.get("layout", {}),
        effective_preview_aspect_ratio(config),
    )
    visible_rows = configured_visible_rows
    if preview_enabled:
        # Do not paginate by more rows than the reduced guide viewport can
        # physically display. Otherwise rows beyond the viewport would be
        # skipped when the next page is calculated.
        visible_rows = max(1, min(configured_visible_rows, int(preview_layout["max_rows"])))

    pages = [
        filtered_channels[i : i + visible_rows]
        for i in range(0, max(len(filtered_channels), 1), visible_rows)
    ] or [[]]

    state = {
        "generated_at": now.isoformat(),
        "theme": theme_name,
        "title": config.get("title", "Guide Channel"),
        "display": {
            "resolution": config.get("resolution", "1280x720"),
            "ui_scale": ui_scale,
            "fps": int(config.get("fps", 15)),
            "page_seconds": int(config.get("page_seconds", 12)),
            "visible_rows": visible_rows,
            "configured_visible_rows": configured_visible_rows,
            "guide_minutes": int(config.get("guide_minutes", 90)),
            "transition": config.get("transition", "scroll"),
            "timezone": config.get("timezone", "local"),
            "browser_timezone": config.get("browser_timezone", ""),
            "preview_enabled": preview_enabled,
            "preview_source_type": normalize_preview_source_type(config.get("guide_preview_source_type")),
            "preview_audio_mode": normalize_preview_audio_mode(config.get("guide_preview_audio_mode")),
            "preview_aspect_mode": normalize_preview_aspect_mode(config.get("guide_preview_aspect_mode")),
            "preview_effective_aspect_ratio": effective_preview_aspect_ratio(config),
            "preview_layout": preview_layout,
            "guide_message_enabled": bool(config.get("guide_message_enabled", False)),
            "guide_message_text": str(config.get("guide_message_text", "") or ""),
            "guide_message_interval_seconds": max(3, min(60, int(config.get("guide_message_interval_seconds", 8) or 8))),
        },
        "time_window": {
            "start": guide_start.isoformat(),
            "end": horizon.isoformat(),
        },
        "theme_data": theme_data,
        "pages": pages,
    }
    output_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def patch_display_state(updates: dict[str, Any]) -> bool:
    """Patch display-only Guide state without rebuilding playlist/XMLTV data.

    Apply live controls to both the primary and optional native secondary state
    so Guide Message changes remain synchronized across both outputs.
    """
    patched = False
    for state_path in (STATE_PATH, SECONDARY_STATE_PATH):
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            display = state.setdefault("display", {})
            display.update(updates)
            tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp_path.replace(state_path)
            patched = True
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return patched


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))
