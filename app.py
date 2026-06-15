from __future__ import annotations

import atexit
import csv
import io
import json
import logging
import math
import traceback
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import requests as _requests

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, send_from_directory, url_for
from flask import request
from werkzeug.utils import secure_filename

from app.config_store import ConfigStore, DEFAULT_CONFIG
from app.ffmpeg_profiles import normalize_hardware_acceleration_mode
from app.hls_playlist import trim_playlist_for_delayed_live_edge
from app.manager import (
    GuideManager, WeatherChannelManager,
    STANDBY_SEGMENT, STATIC_SEGMENT, STANDBY_DURATION_SECS, MUSIC_DIR, WEATHER_MUSIC_DIR,
    WEATHER_PLAYLIST,
)
from app.weather_radar import (
    CONUS_FALLBACK_BBOX,
    DEFAULT_HEIGHT as WEATHER_RADAR_DEFAULT_HEIGHT,
    DEFAULT_RADIUS_MILES as WEATHER_RADAR_DEFAULT_RADIUS_MILES,
    DEFAULT_REFRESH_SECONDS as WEATHER_RADAR_REFRESH_SECONDS,
    DEFAULT_WIDTH as WEATHER_RADAR_DEFAULT_WIDTH,
    build_noaa_radar_url,
    create_or_update_weather_region,
    refresh_radar_if_stale,
)

BASE_DIR = Path(__file__).resolve().parent
THEMES_DIR = BASE_DIR / "app" / "themes"
OUTPUT_DIR = BASE_DIR / "output"
GUIDE_LOGO_DIR = BASE_DIR / "data" / "guide_logo"
WEATHER_LOGO_DIR = BASE_DIR / "data" / "weather_logo"
STANDBY_PATTERN_DIR = BASE_DIR / "data" / "standby_patterns"
GUIDE_DELAY_SEGMENTS = 2
GUIDE_MIN_BUFFER_SECS = 18.0
GUIDE_MIN_BUFFER_SEGMENTS = 3
GUIDE_STANDBY_WINDOW_SEGMENTS = 3
GUIDE_MIN_VISIBLE_SEGMENTS = 3

ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"}
MAX_MUSIC_FILE_BYTES = 100 * 1024 * 1024  # 100 MB
ALLOWED_GUIDE_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
DEFAULT_GUIDE_LOGO_EXTENSION_ORDER = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg")
MAX_GUIDE_LOGO_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_WEATHER_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
DEFAULT_WEATHER_LOGO_EXTENSION_ORDER = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg")
ALLOWED_STANDBY_PATTERN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_STANDBY_PATTERN_BYTES = 10 * 1024 * 1024  # 10 MB

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.secret_key = "retro-guide-poc-local-only"
app.config["SESSION_COOKIE_NAME"] = "retro_guide_session"
app.config["MAX_CONTENT_LENGTH"] = MAX_MUSIC_FILE_BYTES

store = ConfigStore()
manager = GuideManager(store)
manager.start()
atexit.register(manager.stop)

# weather_manager is initialized later in this module after the weather helper
# functions (_build_weather_payload etc.) have been defined.  Route handlers
# reference this module-level name at *call* time (after full module load) so
# late assignment is safe.
weather_manager: "WeatherChannelManager | None" = None


def _error_label(exc: Exception) -> str:
    """Return a user-safe non-empty error label for exceptions."""
    return str(exc).strip() or exc.__class__.__name__


def _log_route_exception(route: str, action: str, phase: str, exc: Exception, **context) -> None:
    """Emit structured route-failure diagnostics and traceback details.

    Parameters describe where the failure occurred:
    * route: HTTP route path (for example ``/config``)
    * action: logical action from the request context
    * phase: failure stage inside the route flow
    * context: optional key/value fields to append for troubleshooting
    """
    parts = [
        f"route={route}",
        f"action={action}",
        f"phase={phase}",
        f"error_type={exc.__class__.__name__}",
        f"error={_error_label(exc)!r}",
    ]
    parts.extend(f"{key}={value!r}" for key, value in context.items() if value not in (None, ""))
    manager.logger.error("http", " ".join(parts))
    manager.logger.error("http.traceback", traceback.format_exc().strip())


def _coerce_guide_logo_mode(value: str | None) -> str:
    mode = (value or DEFAULT_CONFIG["guide_logo_mode"]).strip().lower()
    if mode not in ("default", "custom", "disabled"):
        return "default"
    return mode


def _coerce_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _list_standby_pattern_files() -> list[str]:
    if not STANDBY_PATTERN_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in STANDBY_PATTERN_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_STANDBY_PATTERN_EXTENSIONS
    )


def _list_audio_files(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        p.name
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_AUDIO_EXTENSIONS
    )


def coerce_form(form) -> dict:
    weather_channel_values = form.getlist("weather_channel_enabled") if hasattr(form, "getlist") else [form.get("weather_channel_enabled")]
    cfg = {
        "playlist_source": form.get("playlist_source", DEFAULT_CONFIG["playlist_source"]).strip(),
        "xmltv_source": form.get("xmltv_source", DEFAULT_CONFIG["xmltv_source"]).strip(),
        "theme": form.get("theme", DEFAULT_CONFIG["theme"]).strip(),
        "title": form.get("title", DEFAULT_CONFIG["title"]).strip(),
        "resolution": form.get("resolution", DEFAULT_CONFIG["resolution"]).strip(),
        "hardware_acceleration_mode": normalize_hardware_acceleration_mode(
            form.get("hardware_acceleration_mode")
        ),
        "fps": int(form.get("fps", DEFAULT_CONFIG["fps"])),
        "segment_seconds": int(form.get("segment_seconds", DEFAULT_CONFIG["segment_seconds"])),
        "page_seconds": int(form.get("page_seconds", DEFAULT_CONFIG["page_seconds"])),
        "visible_rows": int(form.get("visible_rows", DEFAULT_CONFIG["visible_rows"])),
        "guide_minutes": int(form.get("guide_minutes", DEFAULT_CONFIG["guide_minutes"])),
        "channel_group": form.get("channel_group", DEFAULT_CONFIG["channel_group"]).strip(),
        "timezone": form.get("timezone", DEFAULT_CONFIG["timezone"]).strip(),
        "browser_timezone": form.get("browser_timezone", DEFAULT_CONFIG["browser_timezone"]).strip(),
        "output_format": form.get("output_format", DEFAULT_CONFIG["output_format"]).strip(),
        "transition": form.get("transition", DEFAULT_CONFIG["transition"]).strip(),
        "guide_logo_mode": _coerce_guide_logo_mode(form.get("guide_logo_mode")),
        "weather_channel_enabled": any(_coerce_bool(value, False) for value in weather_channel_values),
        "standby_overlay_enabled": _coerce_bool(
            form.get("standby_overlay_enabled"),
            DEFAULT_CONFIG["standby_overlay_enabled"],
        ),
        "standby_overlay_opacity": _coerce_int(
            form.get("standby_overlay_opacity"),
            DEFAULT_CONFIG["standby_overlay_opacity"],
            min_value=0,
            max_value=100,
        ),
    }
    return cfg


def _coerce_int(value, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _coerce_time_str(value: str | None, default: str) -> str:
    """Return a validated HH:MM string or *default* if *value* is invalid."""
    raw = (value or "").strip()
    parts = raw.split(":")
    if len(parts) == 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    return default


def _is_off_air(cfg: dict, *, _now: datetime | None = None) -> bool:
    """Return True if the current local time falls within the configured off-air window.

    The window is defined by *off_air_start* and *off_air_end* as ``HH:MM``
    strings in local time.  The window can span midnight (e.g. 23:00–06:00).
    When start equals end the window is treated as empty (never off-air).

    The optional *_now* parameter is used in tests to inject a specific time.
    """
    if not _coerce_bool(cfg.get("off_air_enabled"), False):
        return False
    start_str = _coerce_time_str(cfg.get("off_air_start"), "00:00")
    end_str = _coerce_time_str(cfg.get("off_air_end"), "06:00")
    try:
        start_h, start_m = int(start_str[:2]), int(start_str[3:])
        end_h, end_m = int(end_str[:2]), int(end_str[3:])
    except (ValueError, IndexError):
        return False
    now = _now if _now is not None else datetime.now()
    current = now.hour * 60 + now.minute
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start == end:
        return False
    if start < end:
        # Daytime window, e.g. 02:00–06:00
        return start <= current < end
    # Overnight window, e.g. 23:00–06:00
    return current >= start or current < end


def _off_air_static_enabled(cfg: dict) -> bool:
    return _coerce_bool(cfg.get("off_air_static_enabled"), DEFAULT_CONFIG["off_air_static_enabled"])


def _resolve_standby_segment(cfg: dict, *, off_air_now: bool | None = None) -> Path:
    if off_air_now is None:
        off_air_now = _is_off_air(cfg)
    if off_air_now and _off_air_static_enabled(cfg):
        return STATIC_SEGMENT
    return STANDBY_SEGMENT


def _read_diag_settings(config: dict) -> dict:
    return {
        "delay_segments": _coerce_int(config.get("diag_delay_segments"), GUIDE_DELAY_SEGMENTS, min_value=1, max_value=120),
        "min_buffer_secs": _coerce_int(config.get("diag_min_buffer_secs"), int(GUIDE_MIN_BUFFER_SECS), min_value=1, max_value=900),
        "min_buffer_segments": _coerce_int(
            config.get("diag_min_buffer_segments"),
            GUIDE_MIN_BUFFER_SEGMENTS,
            min_value=1,
            max_value=300,
        ),
        "standby_window_segments": _coerce_int(
            config.get("diag_standby_window_segments"),
            GUIDE_STANDBY_WINDOW_SEGMENTS,
            min_value=1,
            max_value=20,
        ),
        "log_tail_lines": _coerce_int(config.get("diag_log_tail_lines"), 120, min_value=10, max_value=2000),
    }


@app.get("/")
def index():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    config["guide_logo_mode"] = _coerce_guide_logo_mode(config.get("guide_logo_mode"))
    config["standby_overlay_enabled"] = _coerce_bool(
        config.get("standby_overlay_enabled"),
        DEFAULT_CONFIG["standby_overlay_enabled"],
    )
    config["standby_overlay_opacity"] = _coerce_int(
        config.get("standby_overlay_opacity"),
        DEFAULT_CONFIG["standby_overlay_opacity"],
        min_value=0,
        max_value=100,
    )
    logo_filename = secure_filename(config.get("guide_logo_custom_file", "") or "")
    logo_path = GUIDE_LOGO_DIR / logo_filename if logo_filename else None
    if config["guide_logo_mode"] == "custom" and (logo_path is None or not logo_path.is_file()):
        config["guide_logo_mode"] = "default"
    themes = sorted([p.name for p in THEMES_DIR.iterdir() if p.is_dir()])
    theme_data_all: dict[str, dict] = {}
    theme_labels: dict[str, str] = {}
    for t in themes:
        try:
            raw = json.loads((THEMES_DIR / t / "theme.json").read_text(encoding="utf-8"))
            theme_data_all[t] = raw.get("colors", {})
            raw_name = raw.get("name")
            theme_labels[t] = raw_name if raw_name is not None else t
        except Exception:
            theme_data_all[t] = {}
            theme_labels[t] = t
    diag = _read_diag_settings(config)
    events = store.get_recent_events(limit=diag["log_tail_lines"])
    music_files = _list_audio_files(MUSIC_DIR)
    standby_pattern_files = _list_standby_pattern_files()
    standby_custom_file = secure_filename(config.get("standby_custom_file", "") or "")
    if standby_custom_file not in standby_pattern_files:
        standby_custom_file = ""
    # Normalize off-air config values for the template
    config["off_air_enabled"] = _coerce_bool(
        config.get("off_air_enabled"),
        DEFAULT_CONFIG["off_air_enabled"],
    )
    config["off_air_start"] = _coerce_time_str(
        config.get("off_air_start"),
        DEFAULT_CONFIG["off_air_start"],
    )
    config["off_air_end"] = _coerce_time_str(
        config.get("off_air_end"),
        DEFAULT_CONFIG["off_air_end"],
    )
    config["off_air_static_enabled"] = _off_air_static_enabled(config)
    config["hardware_acceleration_mode"] = normalize_hardware_acceleration_mode(
        config.get("hardware_acceleration_mode")
    )
    config["weather_channel_enabled"] = _coerce_bool(
        config.get("weather_channel_enabled"),
        DEFAULT_CONFIG["weather_channel_enabled"],
    )
    status = manager.status()
    status.setdefault(
        "hls_watchdog",
        {
            "healthy": True,
            "playlist_age_secs": None,
            "latest_segment": None,
            "latest_segment_age_secs": None,
            "warnings": [],
        },
    )
    status.setdefault(
        "gpu_capabilities",
        {
            "running_in_docker": False,
            "hardware_available": False,
            "device_detected_providers": [],
            "detected_hardware_providers": [],
            "docker_visible_providers": [],
            "ffmpeg_available": False,
            "ffmpeg_error": "",
            "ffmpeg_detected_encoders": [],
            "providers": {},
            "software_fallback": {
                "available": True,
                "label": "software",
                "reason": "Software fallback is always available (libx264).",
            },
            "active_path": {
                "provider": "software",
                "label": "Software fallback (libx264)",
                "codec": "libx264",
                "using_hardware": False,
                "reason": "Configured to always use software fallback (libx264).",
            },
            "message": "No hardware acceleration detected; software fallback is active.",
        },
    )
    return render_template(
        "index.html",
        config=config,
        status=status,
        events=events,
        events_total=store.count_events(),
        themes=themes,
        theme_labels=theme_labels,
        theme_data_all=theme_data_all,
        music_files=music_files,
        guide_logo_custom_file=logo_filename if logo_path and logo_path.is_file() else "",
        guide_logo_custom_url=url_for("guide_logo_file", filename=logo_filename) if logo_path and logo_path.is_file() else "",
        standby_pattern_files=standby_pattern_files,
        standby_custom_file=standby_custom_file,
        off_air_now=_is_off_air(config),
    )


@app.post("/config")
def save_config():
    action = request.form.get("action", "save")
    try:
        old_config = store.get_config()
        config = coerce_form(request.form)
        store.save_config(config)
        changed = [k for k in config if config[k] != old_config.get(k)]
        if changed:
            manager.logger.info("config", f"Config updated: {', '.join(changed)}")
    except Exception as exc:
        _log_route_exception(
            route="/config",
            action=action,
            phase="save_config",
            exc=exc,
            playlist_source=request.form.get("playlist_source", "").strip(),
            xmltv_source=request.form.get("xmltv_source", "").strip(),
        )
        flash(f"Failed to save configuration: {_error_label(exc)}", "error")
        return redirect(url_for("index"))

    is_first_start = None
    try:
        if action == "start_restart":
            # Admin explicitly requested start or restart.
            is_first_start = not manager.status().get("pipeline_active")
            msg = "Guide is Starting..." if is_first_start else "Guide is Restarting..."
            manager.refresh_state()
            manager.start_pipeline(message=msg)
            label = "started" if is_first_start else "restarted"
            flash(f"Configuration saved and guide {label}.", "success")
        else:
            # Save only – refresh guide state if the pipeline is already
            # running so non-encoding changes (theme, title, etc.) take
            # effect on the next rendered frame without a full restart.
            if manager.status()["pipeline_active"]:
                manager.refresh_state()
            flash("Configuration saved.", "success")
    except Exception as exc:
        _log_route_exception(
            route="/config",
            action=action,
            phase="start_restart" if action == "start_restart" else "post_save_refresh",
            exc=exc,
            is_first_start=is_first_start,
        )
        if action == "start_restart":
            flash(f"Configuration saved, but guide failed to start: {_error_label(exc)}", "error")
        else:
            flash(f"Configuration saved, but refresh failed: {_error_label(exc)}", "error")
    return redirect(url_for("index"))


@app.post("/refresh")
def refresh_now():
    try:
        manager.refresh_state()
        flash("Guide state refreshed.", "success")
    except Exception as exc:
        flash(f"Refresh failed: {exc}", "error")
    return redirect(url_for("index"))


@app.post("/stop")
def stop_guide():
    try:
        manager.stop_pipeline()
        flash("Guide stopped; standby mode active.", "success")
    except Exception as exc:
        flash(f"Stop failed: {exc}", "error")
    return redirect(url_for("index"))


@app.post("/restart")
def restart_pipeline():
    try:
        manager.restart_pipeline()
        flash("Pipeline restarted.", "success")
    except Exception as exc:
        _log_route_exception(route="/restart", action="restart", phase="restart_pipeline", exc=exc)
        flash(f"Pipeline restart failed: {_error_label(exc)}", "error")
    return redirect(url_for("index"))


@app.get("/status")
def status():
    return manager.status()


@app.post("/diagnostics/settings")
def diagnostics_settings():
    old_cfg = {**DEFAULT_CONFIG, **store.get_config()}
    updated_cfg = {
        **old_cfg,
        "diag_delay_segments": _coerce_int(request.form.get("diag_delay_segments"), old_cfg["diag_delay_segments"], min_value=1, max_value=120),
        "diag_min_buffer_secs": _coerce_int(request.form.get("diag_min_buffer_secs"), old_cfg["diag_min_buffer_secs"], min_value=1, max_value=900),
        "diag_min_buffer_segments": _coerce_int(
            request.form.get("diag_min_buffer_segments"),
            old_cfg["diag_min_buffer_segments"],
            min_value=1,
            max_value=300,
        ),
        "diag_standby_window_segments": _coerce_int(
            request.form.get("diag_standby_window_segments"),
            old_cfg["diag_standby_window_segments"],
            min_value=1,
            max_value=20,
        ),
        "diag_log_tail_lines": _coerce_int(
            request.form.get("diag_log_tail_lines"),
            old_cfg["diag_log_tail_lines"],
            min_value=10,
            max_value=2000,
        ),
    }
    store.save_config(updated_cfg)
    manager.logger.info(
        "config",
        "Diagnostics updated: "
        f"delay_segments={updated_cfg['diag_delay_segments']}, "
        f"min_buffer_secs={updated_cfg['diag_min_buffer_secs']}, "
        f"min_buffer_segments={updated_cfg['diag_min_buffer_segments']}, "
        f"standby_window_segments={updated_cfg['diag_standby_window_segments']}, "
        f"log_tail_lines={updated_cfg['diag_log_tail_lines']}",
    )
    if request.form.get("action") == "restart" and manager.status()["pipeline_active"]:
        manager.start_pipeline(message="Guide is Restarting...")
        flash("Diagnostics saved and pipeline restarted.", "success")
    else:
        flash("Diagnostics saved.", "success")
    return redirect(url_for("index") + "#tab-diagnostics")


@app.get("/logs")
def logs_api():
    limit = _coerce_int(request.args.get("limit"), 200, min_value=1, max_value=5000)
    offset = _coerce_int(request.args.get("offset"), 0, min_value=0, max_value=1_000_000)
    events = store.get_events(limit=limit, offset=offset)
    total = store.count_events()
    return jsonify(
        {
            "total": total,
            "count": len(events),
            "offset": offset,
            "limit": limit,
            "events": events,
        }
    )


@app.get("/logs/export")
def logs_export():
    fmt = (request.args.get("format") or "jsonl").strip().lower()
    events = store.get_events(limit=None)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["created_at", "level", "category", "message"])
        for event in events:
            writer.writerow([event["created_at"], event["level"], event["category"], event["message"]])
        data = output.getvalue()
        response = Response(data, mimetype="text/csv")
        response.headers["Content-Disposition"] = f'attachment; filename="retro-guide-events-{stamp}.csv"'
        return response

    lines = [json.dumps(event, separators=(",", ":")) for event in events]
    payload = "\n".join(lines) + "\n"
    response = Response(payload, mimetype="application/x-ndjson")
    response.headers["Content-Disposition"] = f'attachment; filename="retro-guide-events-{stamp}.jsonl"'
    return response


def _channel_logo_url(config: dict, base_url: str) -> str:
    mode = _coerce_guide_logo_mode(config.get("guide_logo_mode"))
    if mode == "disabled":
        return ""
    if mode == "custom":
        logo_filename = secure_filename(config.get("guide_logo_custom_file", "") or "")
        logo_path = GUIDE_LOGO_DIR / logo_filename if logo_filename else None
        if logo_path and logo_path.is_file():
            return f"{base_url}/guide-logo/{logo_filename}"
    default_logo_name = _default_guide_logo_name()
    if default_logo_name:
        return f"{base_url}/guide-logo/{default_logo_name}"
    return ""


def _default_guide_logo_name() -> str:
    preferred_names = [f"default{ext}" for ext in DEFAULT_GUIDE_LOGO_EXTENSION_ORDER]
    for name in preferred_names:
        path = GUIDE_LOGO_DIR / name
        if path.is_file():
            return name
    if not GUIDE_LOGO_DIR.is_dir():
        return ""
    for path in sorted(GUIDE_LOGO_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_GUIDE_LOGO_EXTENSIONS:
            continue
        safe_name = secure_filename(path.name)
        if safe_name != path.name:
            continue
        if path.stem.lower() == "custom":
            continue
        return safe_name
    return ""


def _default_weather_logo_name() -> str:
    preferred_names = [f"default{ext}" for ext in DEFAULT_WEATHER_LOGO_EXTENSION_ORDER]
    for name in preferred_names:
        path = WEATHER_LOGO_DIR / name
        if path.is_file():
            return name
    if not WEATHER_LOGO_DIR.is_dir():
        return ""
    for path in sorted(WEATHER_LOGO_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_WEATHER_LOGO_EXTENSIONS:
            continue
        safe_name = secure_filename(path.name)
        if safe_name != path.name:
            continue
        return safe_name
    return ""


def _weather_logo_url(config: dict, base_url: str) -> str:
    if not _coerce_bool(config.get("weather_logo_enabled"), DEFAULT_CONFIG["weather_logo_enabled"]):
        return ""
    logo_name = _default_weather_logo_name()
    if logo_name:
        return f"{base_url}/weather-logo/{logo_name}"
    return ""


def _build_channel_m3u_content(channel_name: str, stream_url: str, xmltv_url: str, logo_url: str = "") -> str:
    """Return M3U playlist content for the virtual guide channel."""
    logo_attr = f' tvg-logo="{logo_url}"' if logo_url else ""
    return (
        f'#EXTM3U url-tvg="{xmltv_url}" x-tvg-url="{xmltv_url}"\n'
        f'#EXTINF:-1 tvg-id="retro-guide-channel" tvg-name="{channel_name}"'
        f"{logo_attr}"
        f' tvg-chno="1"'
        f' group-title="Virtual Channels"'
        f' tvc-stream-vcodec="h264" tvc-stream-acodec="aac"'
        f',{channel_name}\n'
        f"{stream_url}\n"
    )


def _sanitize_m3u_text(value: str | None, default: str) -> str:
    raw = (value or default).strip() or default
    # Extended M3U attribute values are double-quoted but do not consistently
    # support backslash escaping across IPTV clients, so normalize any
    # user-provided double quotes to single quotes.
    # Commas are also removed because many IPTV clients split EXTINF lines on
    # the first comma, which causes attribute text to bleed into the displayed
    # channel name when the location or title contains "City, State" style text.
    return raw.replace("\r", "").replace("\n", "").replace('"', "'").replace(",", "")


def _sanitize_xmltv_text(value: str | None, default: str) -> str:
    raw = (value or default).strip() or default
    return raw.replace("\r", "").replace("\n", "")


def _weather_channel_display_name(config: dict) -> str:
    location = _sanitize_xmltv_text(config.get("weather_location_name"), "").strip()
    return f"Weather Channel - {location}" if location else "Weather Channel"


def _build_virtual_channel_entries(config: dict, base_url: str) -> list[dict]:
    entries = [
        {
            "id": "retro-guide-channel",
            "name": _sanitize_xmltv_text(config.get("title"), "Channel Guide"),
            "stream_url": base_url + "/hls/master.m3u8",
            "logo_url": _channel_logo_url(config, base_url),
            "channel_number": 1,
            "description": "Retro-style TV guide channel.",
        }
    ]
    if _coerce_bool(config.get("weather_channel_enabled"), DEFAULT_CONFIG["weather_channel_enabled"]):
        entries.append(
            {
                "id": "retro-weather-channel",
                "name": _weather_channel_display_name(config),
                "stream_url": base_url + "/hls/weather.m3u8",
                "logo_url": _weather_logo_url(config, base_url),
                "channel_number": 2,
                "description": "Retro-style local weather channel.",
            }
        )
    return entries


def _build_channels_m3u_content(channels: list[dict], xmltv_url: str) -> str:
    lines = [f'#EXTM3U url-tvg="{xmltv_url}" x-tvg-url="{xmltv_url}"']
    for channel in channels:
        logo_url = channel.get("logo_url") or ""
        logo_attr = f' tvg-logo="{logo_url}"' if logo_url else ""
        channel_name = _sanitize_m3u_text(channel.get("name"), "Virtual Channel")
        lines.append(
            f'#EXTINF:-1 tvg-id="{channel["id"]}" tvg-name="{channel_name}"'
            f"{logo_attr}"
            f' tvg-chno="{channel["channel_number"]}"'
            f' group-title="Virtual Channels"'
            f' tvc-stream-vcodec="h264" tvc-stream-acodec="aac"'
            f",{channel_name}"
        )
        lines.append(str(channel["stream_url"]))
    return "\n".join(lines) + "\n"


@app.get("/channel.m3u")
def channel_playlist():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    base_url = request.host_url.rstrip("/")
    xmltv_url = base_url + "/channel.xmltv"
    content = _build_channels_m3u_content(_build_virtual_channel_entries(config, base_url), xmltv_url)
    resp = Response(content, mimetype="application/x-mpegURL")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/channel.m3u8")
def channel_playlist_m3u8():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    base_url = request.host_url.rstrip("/")
    xmltv_url = base_url + "/channel.xmltv"
    content = _build_channels_m3u_content(_build_virtual_channel_entries(config, base_url), xmltv_url)
    resp = Response(content, mimetype="application/vnd.apple.mpegurl")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def _build_xmltv_content(channel_name: str) -> str:
    """Generate a simple XMLTV EPG for the virtual guide channel.

    Emits 4-hour programme blocks covering 7 days (6 blocks/day × 7 days =
    42 entries).  Compared with many short 30-minute entries, a single
    4-hour block per slot produces a wide, easy-to-read bar in IPTV client
    EPG grids and better represents the continuous nature of the guide
    channel.  Blocks start at the current 4-hour UTC boundary so the current
    moment always falls within the first entry.
    """
    now = datetime.now(timezone.utc)
    # Align to the current 4-hour boundary and cover 7 days.
    slot_start = now.replace(minute=0, second=0, microsecond=0)
    slot_start = slot_start.replace(hour=(slot_start.hour // 4) * 4)
    total_slots = 6 * 7  # 7 days × 6 four-hour slots/day

    tv = ET.Element("tv", {"generator-info-name": "retro-guide-poc"})
    channel_el = ET.SubElement(tv, "channel", {"id": "retro-guide-channel"})
    ET.SubElement(channel_el, "display-name").text = channel_name

    for i in range(total_slots):
        slot_end = slot_start + timedelta(hours=4)
        prog = ET.SubElement(
            tv,
            "programme",
            {
                "start": slot_start.strftime("%Y%m%d%H%M%S +0000"),
                "stop": slot_end.strftime("%Y%m%d%H%M%S +0000"),
                "channel": "retro-guide-channel",
            },
        )
        ET.SubElement(prog, "title").text = channel_name
        ET.SubElement(prog, "desc").text = "Retro-style TV guide channel."
        slot_start = slot_end

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode")


def _build_channels_xmltv_content(channels: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    slot_start = now.replace(minute=0, second=0, microsecond=0)
    slot_start = slot_start.replace(hour=(slot_start.hour // 4) * 4)
    total_slots = 6 * 7

    tv = ET.Element("tv", {"generator-info-name": "retro-guide-poc"})
    for channel in channels:
        channel_el = ET.SubElement(tv, "channel", {"id": channel["id"]})
        ET.SubElement(channel_el, "display-name").text = _sanitize_xmltv_text(channel.get("name"), "Virtual Channel")

    for channel in channels:
        entry_start = slot_start
        for _ in range(total_slots):
            slot_end = entry_start + timedelta(hours=4)
            prog = ET.SubElement(
                tv,
                "programme",
                {
                    "start": entry_start.strftime("%Y%m%d%H%M%S +0000"),
                    "stop": slot_end.strftime("%Y%m%d%H%M%S +0000"),
                    "channel": channel["id"],
                },
            )
            ET.SubElement(prog, "title").text = _sanitize_xmltv_text(channel.get("name"), "Virtual Channel")
            ET.SubElement(prog, "desc").text = _sanitize_xmltv_text(channel.get("description"), "Virtual channel.")
            entry_start = slot_end

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode")


@app.get("/channel.xmltv")
def channel_xmltv():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    content = _build_channels_xmltv_content(_build_virtual_channel_entries(config, request.host_url.rstrip("/")))
    resp = Response(content, mimetype="application/xml")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/hls/master.m3u8")
def hls_master_playlist():
    """Serve a HLS master/multivariant playlist that dynamically switches
    between the standby and live media playlists.

    While the guide pipeline is starting up (or stopped), the variant points to
    ``/hls/standby.m3u8``.  Once the guide has enough buffer to play smoothly,
    the variant switches to ``/hls/live.m3u8``.  A ``?v=N`` cache-busting query
    parameter changes with every standby↔live transition so clients that
    re-fetch this playlist see a distinct URL and are forced to reload.

    IPTV apps such as RetroIPTVGuide expect the two-level HLS hierarchy
    (master → media → segments) rather than a bare media playlist.  This
    endpoint generates a master playlist in the same format used by ErsatzTV.
    """
    config = store.get_config()
    resolution = (config.get("resolution") or "1280x720").strip()
    base_url = request.host_url.rstrip("/")

    # status() updates the stream version counter on state transitions so we
    # always get a fresh version here.
    st = manager.status()
    version = st.get("stream_version", 0)
    cfg = {**DEFAULT_CONFIG, **config}
    if _is_off_air(cfg):
        # Off-air window: always serve the standby (static) stream.
        media_url = f"{base_url}/hls/standby.m3u8?v={version}"
    elif st.get("guide_buffered"):
        media_url = f"{base_url}/hls/live.m3u8?v={version}"
    else:
        media_url = f"{base_url}/hls/standby.m3u8?v={version}"

    try:
        width_str, height_str = resolution.lower().split("x")
        width, height = int(width_str), int(height_str)
    except (ValueError, AttributeError):
        width, height = 1280, 720

    # Scale bandwidth estimate proportionally to pixel count vs. 720p baseline.
    bandwidth = int((width * height / (1280 * 720)) * 4_000_000)

    content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height}'
        f',CODECS="avc1.4D4028,mp4a.40.2"\n'
        f"{media_url}\n"
    )
    response = Response(content, mimetype="application/vnd.apple.mpegurl")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range"
    return response


@app.get("/hls/guide.m3u8")
def hls_guide_playlist():
    """Backward-compatible unified HLS media playlist endpoint.

    Clients that load this URL directly (e.g. admin previews that bookmarked the
    old URL before the master/standby/live split) continue to receive a valid
    playlist.  New clients should use ``/hls/master.m3u8`` which dynamically
    points to either ``/hls/standby.m3u8`` or ``/hls/live.m3u8``.
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
    guide_path = OUTPUT_DIR / "guide.m3u8"
    if (
        guide_path.exists()
        and manager.is_guide_buffered(
            min_secs=float(diag["min_buffer_secs"]),
            min_segments=diag["min_buffer_segments"],
        )
        and manager.status()["pipeline_active"]
    ):
        return _make_live_playlist_response(diag)

    # guide.m3u8 not yet ready – serve the standby playlist if the segment exists.
    if not _resolve_standby_segment(cfg).exists():
        abort(404)
    return _make_standby_playlist_response(diag, cfg)


def _make_standby_playlist_response(diag: dict, cfg: dict, *, off_air_now: bool | None = None) -> Response:
    """Build and return a synthetic standby media playlist response.

    Extracted so both ``/hls/guide.m3u8`` (backward compat) and the dedicated
    ``/hls/standby.m3u8`` endpoint can share the same logic without duplication.
    """
    _sdur = STANDBY_DURATION_SECS
    segment_name = _resolve_standby_segment(cfg, off_air_now=off_air_now).name
    try:
        _live_seg_secs = max(1, int(store.get_config().get("segment_seconds", 6)))
    except (TypeError, ValueError):
        _live_seg_secs = 6
    seq = int(time.time()) // _live_seg_secs
    _window = diag["standby_window_segments"]
    _seg_entries = "".join(
        f"#EXT-X-DISCONTINUITY\n#EXTINF:{float(_sdur):.3f},\n{segment_name}?s={seq + i}\n"
        for i in range(_window)
    )
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f"#EXT-X-TARGETDURATION:{_sdur}\n"
        f"#EXT-X-MEDIA-SEQUENCE:{seq}\n"
        f"#EXT-X-DISCONTINUITY-SEQUENCE:{seq}\n"
        + _seg_entries
    )
    response = Response(playlist, mimetype="application/vnd.apple.mpegurl")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range"
    return response


def _make_live_playlist_response(diag: dict) -> Response:
    """Read, sanitize, and return the real ffmpeg guide playlist as a response.

    Extracted so both ``/hls/guide.m3u8`` (backward compat) and the dedicated
    ``/hls/live.m3u8`` endpoint can share the same logic.
    Raises ``werkzeug.exceptions.NotFound`` (abort 404) on read errors.
    """
    guide_path = OUTPUT_DIR / "guide.m3u8"
    try:
        playlist_text = guide_path.read_text(encoding="utf-8")
        playlist_text = trim_playlist_for_delayed_live_edge(
            playlist_text,
            delay_segments=diag["delay_segments"],
            min_visible_segments=GUIDE_MIN_VISIBLE_SEGMENTS,
        )
        lines = playlist_text.splitlines()
        lines = [line for line in lines if not line.startswith("#EXT-X-PROGRAM-DATE-TIME:")]
        playlist_text = "\n".join(lines) + "\n"
    except OSError:
        abort(404)
    response = Response(playlist_text, mimetype="application/vnd.apple.mpegurl")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range"
    return response


@app.get("/hls/standby.m3u8")
def hls_standby_playlist():
    """Serve the synthetic standby media playlist, or 404 when the guide is live.

    While the guide pipeline is starting up (or stopped), this endpoint returns
    the looping ``standby.ts`` playlist so IPTV clients have something to
    display.  Once the guide has enough buffer to play smoothly, this endpoint
    returns **404** so that clients which were polling it get a network error,
    prompting them to re-fetch ``/hls/master.m3u8`` and discover the new
    ``/hls/live.m3u8`` variant URL.

    During the configured off-air window the standby playlist is always served
    regardless of whether the live guide is buffered, so viewers see static
    instead of the guide.
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
    guide_path = OUTPUT_DIR / "guide.m3u8"
    # During the off-air window always serve standby; skip the live-readiness
    # check so we don't return 404 while the guide pipeline is running.
    off_air_now = _is_off_air(cfg)
    if not off_air_now:
        # 404 when the real guide is ready — forces clients to reload master.m3u8.
        if (
            guide_path.exists()
            and manager.is_guide_buffered(
                min_secs=float(diag["min_buffer_secs"]),
                min_segments=diag["min_buffer_segments"],
            )
            and manager.status()["pipeline_active"]
        ):
            abort(404)
    if not _resolve_standby_segment(cfg, off_air_now=off_air_now).exists():
        abort(404)
    return _make_standby_playlist_response(diag, cfg, off_air_now=off_air_now)


@app.get("/hls/live.m3u8")
def hls_live_playlist():
    """Serve the real guide media playlist, or 404 when the guide is not ready.

    This endpoint is the "live" variant that ``/hls/master.m3u8`` points to
    once the guide has enough buffer.  It returns 404 while the pipeline is
    still starting (or is stopped) so that clients that somehow reach this URL
    early get a clean error rather than a partial playlist.  It also returns
    404 during the configured off-air window so that clients re-fetch the
    master playlist and discover the standby variant.
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
    if _is_off_air(cfg):
        abort(404)
    guide_path = OUTPUT_DIR / "guide.m3u8"
    if not (
        guide_path.exists()
        and manager.is_guide_buffered(
            min_secs=float(diag["min_buffer_secs"]),
            min_segments=diag["min_buffer_segments"],
        )
        and manager.status()["pipeline_active"]
    ):
        abort(404)
    return _make_live_playlist_response(diag)


@app.get("/hls/weather.m3u8")
def hls_weather_playlist():
    """Weather virtual channel HLS entrypoint.

    Serves the live HLS media playlist produced by the WeatherChannelManager
    pipeline (``output/weather.m3u8``) once it has buffered enough segments.
    Falls back to the standby playlist while the weather pipeline is starting
    or when the weather channel is disabled.
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)

    # Serve the live weather stream when the pipeline has buffered segments.
    if (
        weather_manager is not None
        and weather_manager.status()["pipeline_active"]
        and weather_manager.is_weather_buffered()
        and WEATHER_PLAYLIST.exists()
    ):
        try:
            playlist_text = WEATHER_PLAYLIST.read_text(encoding="utf-8")
            lines = playlist_text.splitlines()
            lines = [line for line in lines if not line.startswith("#EXT-X-PROGRAM-DATE-TIME:")]
            playlist_text = "\n".join(lines) + "\n"
        except OSError:
            abort(404)
        response = Response(playlist_text, mimetype="application/vnd.apple.mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Range"
        return response

    # Fall back to the standby/test-pattern playlist while warming up.
    off_air_now = _is_off_air(cfg)
    segment = _resolve_standby_segment(cfg, off_air_now=off_air_now)
    if not segment.exists():
        abort(404)
    return _make_standby_playlist_response(diag, cfg, off_air_now=off_air_now)


@app.get("/hls/<path:filename>")
def hls_file(filename: str):
    response = send_from_directory(OUTPUT_DIR, filename)
    if filename.endswith(".m3u8"):
        response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif filename.endswith(".ts"):
        response.headers["Content-Type"] = "video/MP2T"
        response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range"
    return response


@app.route("/hls/master.m3u8", methods=["OPTIONS"])
@app.route("/hls/<path:filename>", methods=["OPTIONS"])
def hls_preflight(**_kwargs):
    response = Response("", status=204)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


def _validate_logo_file_content(path: Path, ext: str) -> bool:
    try:
        header = path.read_bytes()[:1024]
    except OSError:
        return False
    if ext == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if ext == ".svg":
        try:
            text = header.decode("utf-8", errors="ignore").lower()
        except UnicodeDecodeError:
            return False
        return "<svg" in text
    return False


@app.get("/guide-logo/<path:filename>")
def guide_logo_file(filename: str):
    safe_name = secure_filename(filename)
    if not safe_name:
        abort(404)
    response = send_from_directory(GUIDE_LOGO_DIR, safe_name)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.post("/guide-logo/upload")
def guide_logo_upload():
    file = request.files.get("guide_logo_file")
    if file is None or not file.filename:
        flash("No logo file selected.", "error")
        return redirect(url_for("index") + "#tab-guide-icon")
    name = secure_filename(file.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_GUIDE_LOGO_EXTENSIONS:
        flash(
            f"Unsupported logo format. Allowed: {', '.join(sorted(ALLOWED_GUIDE_LOGO_EXTENSIONS))}",
            "error",
        )
        return redirect(url_for("index") + "#tab-guide-icon")
    GUIDE_LOGO_DIR.mkdir(parents=True, exist_ok=True)
    current_cfg = {**DEFAULT_CONFIG, **store.get_config()}
    current_name = secure_filename(current_cfg.get("guide_logo_custom_file", "") or "")
    if current_name:
        (GUIDE_LOGO_DIR / current_name).unlink(missing_ok=True)
    # Keep one active custom icon file so M3U logo URLs remain stable.
    final_name = f"custom{ext}"
    dest = GUIDE_LOGO_DIR / final_name
    try:
        file.save(str(dest))
        if dest.stat().st_size > MAX_GUIDE_LOGO_BYTES:
            dest.unlink(missing_ok=True)
            flash("Logo file is too large. Maximum size is 5 MB.", "error")
            return redirect(url_for("index") + "#tab-guide-icon")
        if not _validate_logo_file_content(dest, ext):
            dest.unlink(missing_ok=True)
            flash("Uploaded file is not a recognized image format.", "error")
            return redirect(url_for("index") + "#tab-guide-icon")
        store.save_config(
            {
                **current_cfg,
                "guide_logo_mode": "custom",
                "guide_logo_custom_file": final_name,
            }
        )
        flash("Guide logo uploaded. M3U export now uses this custom logo.", "success")
    except OSError as exc:
        flash(f"Could not save logo file: {exc}", "error")
    return redirect(url_for("index") + "#tab-guide-icon")


@app.post("/guide-logo/remove")
def guide_logo_remove():
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    logo_name = secure_filename(cfg.get("guide_logo_custom_file", "") or "")
    if logo_name:
        (GUIDE_LOGO_DIR / logo_name).unlink(missing_ok=True)
    store.save_config(
        {
            **cfg,
            "guide_logo_mode": "default",
            "guide_logo_custom_file": "",
        }
    )
    flash("Custom guide logo removed. Default logo is active.", "success")
    return redirect(url_for("index") + "#tab-guide-icon")


@app.get("/weather-logo/<path:filename>")
def weather_logo_file(filename: str):
    safe_name = secure_filename(filename)
    if not safe_name:
        abort(404)
    response = send_from_directory(WEATHER_LOGO_DIR, safe_name)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/standby-pattern/<path:filename>")
def standby_pattern_file(filename: str):
    safe_name = secure_filename(filename)
    if not safe_name:
        abort(404)
    response = send_from_directory(STANDBY_PATTERN_DIR, safe_name)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.post("/standby-pattern/upload")
def standby_pattern_upload():
    file = request.files.get("standby_pattern_file")
    if file is None or not file.filename:
        flash("No standby pattern file selected.", "error")
        return redirect(url_for("index") + "#tab-standby-pattern")
    name = secure_filename(file.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_STANDBY_PATTERN_EXTENSIONS:
        flash(
            "Unsupported standby pattern format. "
            f"Allowed: {', '.join(sorted(ALLOWED_STANDBY_PATTERN_EXTENSIONS))}",
            "error",
        )
        return redirect(url_for("index") + "#tab-standby-pattern")

    STANDBY_PATTERN_DIR.mkdir(parents=True, exist_ok=True)
    final_name = name
    dest = STANDBY_PATTERN_DIR / final_name
    if dest.exists():
        # For odd filenames like ".png", fallback to a stable stem.
        stem = Path(name).stem or "pattern"
        counter = 1
        while dest.exists():
            final_name = f"{stem}-{counter}{ext}"
            dest = STANDBY_PATTERN_DIR / final_name
            counter += 1
    try:
        file.save(str(dest))
        if dest.stat().st_size > MAX_STANDBY_PATTERN_BYTES:
            dest.unlink(missing_ok=True)
            flash(
                f"Standby pattern file is too large. Maximum size is "
                f"{MAX_STANDBY_PATTERN_BYTES // (1024 * 1024)} MB.",
                "error",
            )
            return redirect(url_for("index") + "#tab-standby-pattern")
        if not manager._is_valid_image_file(dest):
            dest.unlink(missing_ok=True)
            flash("Uploaded standby pattern is not a recognized image format.", "error")
            return redirect(url_for("index") + "#tab-standby-pattern")
        cfg = {**DEFAULT_CONFIG, **store.get_config()}
        store.save_config({**cfg, "standby_custom_file": final_name})
        manager._generate_standby_segment(cfg.get("title", DEFAULT_CONFIG["title"]))
        flash("Standby pattern uploaded and selected.", "success")
    except OSError as exc:
        flash(f"Could not save standby pattern file: {exc}", "error")
    return redirect(url_for("index") + "#tab-standby-pattern")


@app.post("/standby-pattern/select")
def standby_pattern_select():
    filename = secure_filename(request.form.get("standby_pattern_file", "") or "")
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    if not filename:
        store.save_config({**cfg, "standby_custom_file": ""})
        manager._generate_standby_segment(cfg.get("title", DEFAULT_CONFIG["title"]))
        flash("Using original generated standby pattern.", "success")
        return redirect(url_for("index") + "#tab-standby-pattern")
    candidate = STANDBY_PATTERN_DIR / filename
    if not candidate.is_file():
        flash("Selected standby pattern file was not found.", "error")
        return redirect(url_for("index") + "#tab-standby-pattern")

    store.save_config({**cfg, "standby_custom_file": filename})
    manager._generate_standby_segment(cfg.get("title", DEFAULT_CONFIG["title"]))
    flash("Standby pattern updated.", "success")
    return redirect(url_for("index") + "#tab-standby-pattern")


@app.post("/standby-pattern/select-default")
def standby_pattern_select_default():
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    store.save_config({**cfg, "standby_custom_file": ""})
    manager._generate_standby_segment(cfg.get("title", DEFAULT_CONFIG["title"]))
    flash("Using original generated standby pattern.", "success")
    return redirect(url_for("index") + "#tab-standby-pattern")


@app.post("/standby-pattern/remove")
def standby_pattern_remove():
    filename = secure_filename(request.form.get("standby_pattern_file", "") or "")
    if not filename:
        flash("No standby pattern selected for removal.", "error")
        return redirect(url_for("index") + "#tab-standby-pattern")
    candidate = STANDBY_PATTERN_DIR / filename
    candidate.unlink(missing_ok=True)

    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    update = {**cfg}
    if secure_filename(cfg.get("standby_custom_file", "") or "") == filename:
        update["standby_custom_file"] = ""
        manager._generate_standby_segment(cfg.get("title", DEFAULT_CONFIG["title"]))
        flash("Standby pattern removed. Using original generated pattern.", "success")
    else:
        flash("Standby pattern removed.", "success")
    store.save_config(update)
    return redirect(url_for("index") + "#tab-standby-pattern")


@app.post("/standby-pattern/settings")
def standby_pattern_settings():
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    overlay_values = request.form.getlist("standby_overlay_enabled")
    if overlay_values:
        overlay_enabled = any(_coerce_bool(value, False) for value in overlay_values)
    else:
        overlay_enabled = cfg.get("standby_overlay_enabled", DEFAULT_CONFIG["standby_overlay_enabled"])
    opacity = _coerce_int(
        request.form.get("standby_overlay_opacity"),
        cfg.get("standby_overlay_opacity", DEFAULT_CONFIG["standby_overlay_opacity"]),
        min_value=0,
        max_value=100,
    )
    store.save_config(
        {
            **cfg,
            "standby_overlay_enabled": overlay_enabled,
            "standby_overlay_opacity": opacity,
        }
    )
    manager._generate_standby_segment(cfg.get("title", DEFAULT_CONFIG["title"]))
    flash("Standby overlay settings updated.", "success")
    return redirect(url_for("index") + "#tab-standby-pattern")


@app.post("/off-air/settings")
def off_air_settings():
    """Save the off-air schedule configuration."""
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    off_air_values = request.form.getlist("off_air_enabled")
    if off_air_values:
        off_air_enabled = any(_coerce_bool(value, False) for value in off_air_values)
    else:
        off_air_enabled = cfg.get("off_air_enabled", DEFAULT_CONFIG["off_air_enabled"])
    off_air_start = _coerce_time_str(request.form.get("off_air_start"), cfg.get("off_air_start", DEFAULT_CONFIG["off_air_start"]))
    off_air_end = _coerce_time_str(request.form.get("off_air_end"), cfg.get("off_air_end", DEFAULT_CONFIG["off_air_end"]))
    off_air_static_values = request.form.getlist("off_air_static_enabled")
    if off_air_static_values:
        off_air_static_enabled = any(_coerce_bool(value, False) for value in off_air_static_values)
    else:
        off_air_static_enabled = cfg.get("off_air_static_enabled", DEFAULT_CONFIG["off_air_static_enabled"])
    store.save_config(
        {
            **cfg,
            "off_air_enabled": off_air_enabled,
            "off_air_start": off_air_start,
            "off_air_end": off_air_end,
            "off_air_static_enabled": off_air_static_enabled,
        }
    )
    manager.logger.info(
        "config",
        f"Off-air schedule updated: enabled={off_air_enabled}, "
        f"start={off_air_start}, end={off_air_end}, static={off_air_static_enabled}",
    )
    flash("Off-air schedule saved.", "success")
    return redirect(url_for("index") + "#tab-off-air")


# ---------------------------------------------------------------------------
# Music management endpoints
# ---------------------------------------------------------------------------

# Magic bytes that identify common audio container/codec formats.
# We read the first 12 bytes and test each entry: (offset, bytes_to_match).
# A file passes if ANY entry matches.
_AUDIO_MAGIC: list[tuple[int, bytes]] = [
    (0, b"ID3"),                        # MP3 with ID3v2 tag
    (0, b"\xff\xfb"),                   # MP3 sync word (MPEG1 layer3 128kbps)
    (0, b"\xff\xfa"),                   # MP3 sync word variant
    (0, b"\xff\xf3"),                   # MP3 sync word variant
    (0, b"\xff\xf2"),                   # MP3 sync word variant
    (0, b"fLaC"),                       # FLAC
    (0, b"RIFF"),                       # WAV (RIFF container)
    (0, b"OggS"),                       # OGG / Vorbis / Opus
    (0, b"\xff\xf1"),                   # AAC ADTS (MPEG-2)
    (0, b"\xff\xf9"),                   # AAC ADTS (MPEG-4)
    (4, b"ftyp"),                       # M4A / MP4 / AAC inside ISO container
]


def _is_audio_file(path: Path) -> bool:
    """Return True if *path* starts with a recognised audio magic signature."""
    try:
        header = path.read_bytes()[:16]
    except OSError:
        return False
    for offset, magic in _AUDIO_MAGIC:
        if header[offset: offset + len(magic)] == magic:
            return True
    return False


def _save_uploaded_audio_files(files, destination_dir: Path) -> tuple[list[str], list[str], list[str]]:
    saved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for f in files:
        if not f.filename:
            continue
        name = secure_filename(f.filename)
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            skipped.append(f.filename)
            continue
        dest = destination_dir / name
        try:
            f.save(str(dest))
            if not _is_audio_file(dest):
                dest.unlink(missing_ok=True)
                errors.append(f"Rejected {name}: file does not appear to be a valid audio file.")
                continue
            saved.append(name)
        except OSError as exc:
            errors.append(f"Could not save {name}: {exc}")
    return saved, skipped, errors


@app.post("/music/upload")
def music_upload():
    """Upload one or more audio files to the music library."""
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        flash("No files selected.", "error")
        return redirect(url_for("index") + "#music-section")

    saved, skipped, errors = _save_uploaded_audio_files(files, MUSIC_DIR)
    for error in errors:
        flash(error, "error")

    if saved:
        flash(f"Uploaded: {', '.join(saved)}", "success")
    if skipped:
        flash(
            f"Skipped (unsupported format): {', '.join(skipped)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}",
            "error",
        )
    return redirect(url_for("index") + "#music-section")


@app.post("/virtual-channels/weather/music/upload")
def weather_music_upload():
    """Upload one or more audio files for Weather Channel background music."""
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        flash("No files selected.", "error")
        return redirect(url_for("virtual_channels_page"))

    saved, skipped, errors = _save_uploaded_audio_files(files, WEATHER_MUSIC_DIR)
    for error in errors:
        flash(error, "error")
    if saved:
        flash(f"Uploaded weather music: {', '.join(saved)}", "success")
    if skipped:
        flash(
            f"Skipped weather music (unsupported format): {', '.join(skipped)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}",
            "error",
        )
    return redirect(url_for("virtual_channels_page"))


@app.post("/music/delete/<filename>")
def music_delete(filename: str):
    """Delete an uploaded audio file from the music library."""
    safe_name = secure_filename(filename)
    dest = MUSIC_DIR / safe_name
    if not dest.exists() or not dest.is_file():
        flash(f"File not found: {safe_name}", "error")
        return redirect(url_for("index") + "#music-section")
    try:
        dest.unlink()
        # Remove the file from config if it was selected.
        cfg = store.get_config()
        changed = False
        if cfg.get("music_single_file") == safe_name:
            cfg["music_single_file"] = ""
            changed = True
        pl = cfg.get("music_playlist_files", [])
        if safe_name in pl:
            cfg["music_playlist_files"] = [x for x in pl if x != safe_name]
            changed = True
        if changed:
            store.save_config(cfg)
        flash(f"Deleted: {safe_name}", "success")
    except OSError as exc:
        flash(f"Could not delete {safe_name}: {exc}", "error")
    return redirect(url_for("index") + "#music-section")


@app.post("/virtual-channels/weather/music/delete/<filename>")
def weather_music_delete(filename: str):
    """Delete an uploaded Weather Channel music file."""
    safe_name = secure_filename(filename)
    dest = WEATHER_MUSIC_DIR / safe_name
    if not dest.exists() or not dest.is_file():
        flash(f"Weather music file not found: {safe_name}", "error")
        return redirect(url_for("virtual_channels_page"))
    try:
        dest.unlink()
        cfg = store.get_config()
        changed = False
        if cfg.get("weather_music_single_file") == safe_name:
            cfg["weather_music_single_file"] = ""
            changed = True
        pl = cfg.get("weather_music_playlist_files", [])
        if safe_name in pl:
            cfg["weather_music_playlist_files"] = [x for x in pl if x != safe_name]
            changed = True
        if changed:
            store.save_config(cfg)
        flash(f"Deleted weather music: {safe_name}", "success")
    except OSError as exc:
        flash(f"Could not delete weather music {safe_name}: {exc}", "error")
    return redirect(url_for("virtual_channels_page"))


@app.post("/music/settings")
def music_settings():
    """Save background-music mode, loop setting, and file selection."""
    music_mode = request.form.get("music_mode", "none").strip()
    if music_mode not in ("none", "single", "playlist"):
        music_mode = "none"

    music_loop = request.form.get("music_loop") == "1"
    music_single_file = request.form.get("music_single_file", "").strip()
    # Playlist order comes from repeated hidden inputs named music_playlist_files
    music_playlist_files = request.form.getlist("music_playlist_files")
    # Sanitize all filenames
    music_single_file = secure_filename(music_single_file) if music_single_file else ""
    music_playlist_files = [secure_filename(f) for f in music_playlist_files if f]

    old_cfg = store.get_config()
    new_music_cfg = {
        **old_cfg,
        "music_mode": music_mode,
        "music_loop": music_loop,
        "music_single_file": music_single_file,
        "music_playlist_files": music_playlist_files,
    }
    store.save_config(new_music_cfg)
    manager.logger.info(
        "config",
        f"Music settings updated: mode={music_mode}, loop={music_loop}, "
        f"single={music_single_file!r}, playlist={music_playlist_files}",
    )

    action = request.form.get("action", "save")
    if action == "restart" and manager.status()["pipeline_active"]:
        manager.start_pipeline(message="Guide is Restarting...")
        flash("Music settings saved and pipeline restarted.", "success")
    else:
        flash("Music settings saved. Restart the pipeline to apply.", "success")

    return redirect(url_for("index") + "#music-section")


# ─────────────────────────────────────────────────────────────────────────────
#  Virtual Channels — Weather Channel
# ─────────────────────────────────────────────────────────────────────────────

_WEATHER_CONFIG_KEYS = (
    "lat", "lon", "location_name", "units",
    "seconds_per_segment", "bg_condition_override",
)
_WEATHER_SECONDS_PER_SEGMENT_DEFAULT = 300  # 5 minutes
_WEATHER_SEGMENT_LABELS = ("current", "forecast", "radar", "alerts", "extended")

_WEATHER_BG_VALID_CONDITIONS = (
    "", "sunny", "partly_cloudy", "cloudy", "rain", "drizzle",
    "showers", "snow", "thunderstorm", "foggy", "windy",
)

# WMO weather interpretation codes → (human label, icon key)
_WMO_MAP: dict[int, tuple[str, str]] = {
    0:  ("Sunny",           "sunny"),
    1:  ("Mostly Clear",    "sunny"),
    2:  ("Partly Cloudy",   "partly_cloudy"),
    3:  ("Overcast",        "cloudy"),
    45: ("Foggy",           "foggy"),
    48: ("Icy Fog",         "foggy"),
    51: ("Light Drizzle",   "drizzle"),
    53: ("Drizzle",         "drizzle"),
    55: ("Heavy Drizzle",   "drizzle"),
    61: ("Light Rain",      "rain"),
    63: ("Rain",            "rain"),
    65: ("Heavy Rain",      "rain"),
    71: ("Light Snow",      "snow"),
    73: ("Snow",            "snow"),
    75: ("Heavy Snow",      "snow"),
    77: ("Snow Grains",     "snow"),
    80: ("Showers",         "showers"),
    81: ("Showers",         "showers"),
    82: ("Heavy Showers",   "showers"),
    85: ("Snow Showers",    "snow"),
    86: ("Heavy Snow Shwr", "snow"),
    95: ("T-Storms",        "thunderstorm"),
    96: ("T-Storms",        "thunderstorm"),
    99: ("T-Storms",        "thunderstorm"),
}

_NIGHT_ICON_MAP: dict[str, str] = {
    "sunny":         "partly_cloudy_night",
    "partly_cloudy": "partly_cloudy_night",
    "cloudy":        "cloudy_night",
}

_WIND_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

_WINDY_BG_THRESHOLD_MPH = 25
_WINDY_BG_THRESHOLD_KMH = 40

_WEATHER_RADAR_IMAGE_ENDPOINT = "/weather-radar/current.png"


def _wmo_label(code: int) -> str:
    return _WMO_MAP.get(code, ("Unknown", "cloudy"))[0]


def _wmo_icon(code: int) -> str:
    return _WMO_MAP.get(code, ("Unknown", "cloudy"))[1]


def _to_night_icon(day_icon: str) -> str:
    return _NIGHT_ICON_MAP.get(day_icon, day_icon)


def _wind_dir(degrees: float) -> str:
    try:
        return _WIND_DIRS[round(float(degrees) / 22.5) % 16]
    except Exception:
        return ""


def _build_radar_url(lat: str, lon: str) -> str:
    try:
        bbox = CONUS_FALLBACK_BBOX
        if lat and lon:
            flat = float(lat)
            flon = float(lon)
            miles_per_degree_lat = 69.0
            miles_per_degree_lon = max(1e-6, 69.0 * math.cos(math.radians(flat)))
            aspect = WEATHER_RADAR_DEFAULT_WIDTH / WEATHER_RADAR_DEFAULT_HEIGHT
            lat_delta = WEATHER_RADAR_DEFAULT_RADIUS_MILES / miles_per_degree_lat
            lon_delta = (WEATHER_RADAR_DEFAULT_RADIUS_MILES * aspect) / miles_per_degree_lon
            bbox = [
                round(flon - lon_delta, 5),
                round(flat - lat_delta, 5),
                round(flon + lon_delta, 5),
                round(flat + lat_delta, 5),
            ]
        return build_noaa_radar_url(
            {
                "bbox": bbox,
                "width": WEATHER_RADAR_DEFAULT_WIDTH,
                "height": WEATHER_RADAR_DEFAULT_HEIGHT,
            }
        )
    except Exception:
        return build_noaa_radar_url(
            {
                "bbox": CONUS_FALLBACK_BBOX,
                "width": WEATHER_RADAR_DEFAULT_WIDTH,
                "height": WEATHER_RADAR_DEFAULT_HEIGHT,
            }
        )


def _weather_region_zip(configured_name: str) -> str:
    if not configured_name:
        return ""
    for token in configured_name.replace(",", " ").split():
        if token.isdigit() and len(token) == 5:
            return token
    return ""


def _weather_radar_assets(cfg: dict, updated_str: str) -> tuple[str, str]:
    lat = str(cfg.get("lat", "")).strip()
    lon = str(cfg.get("lon", "")).strip()
    location_name = str(cfg.get("location_name", "")).strip()
    if not lat or not lon:
        return _WEATHER_RADAR_IMAGE_ENDPOINT, ""

    region = create_or_update_weather_region(
        lat=lat,
        lon=lon,
        location_name=location_name or "Local Weather",
        zip_code=_weather_region_zip(location_name),
        radius_miles=WEATHER_RADAR_DEFAULT_RADIUS_MILES,
        width=WEATHER_RADAR_DEFAULT_WIDTH,
        height=WEATHER_RADAR_DEFAULT_HEIGHT,
        refresh_seconds=WEATHER_RADAR_REFRESH_SECONDS,
    )
    radar_path = refresh_radar_if_stale(region)
    cache_bust_token = int(time.time())
    return f"{_WEATHER_RADAR_IMAGE_ENDPOINT}?v={cache_bust_token}", str(radar_path)


def _get_weather_config() -> dict:
    """Return weather configuration from the config store."""
    cfg = store.get_config()
    return {
        "lat":                    cfg.get("weather_lat", ""),
        "lon":                    cfg.get("weather_lon", ""),
        "location_name":          cfg.get("weather_location_name", ""),
        "units":                  cfg.get("weather_units", "F"),
        "seconds_per_segment":    str(cfg.get("weather_seconds_per_segment",
                                              str(_WEATHER_SECONDS_PER_SEGMENT_DEFAULT))),
        "bg_condition_override":  cfg.get("weather_bg_condition_override", ""),
        "enabled":                cfg.get("weather_channel_enabled", False),
        "logo_enabled":           _coerce_bool(cfg.get("weather_logo_enabled"),
                                               DEFAULT_CONFIG["weather_logo_enabled"]),
        "music_mode":             cfg.get("weather_music_mode", DEFAULT_CONFIG["weather_music_mode"]),
        "music_loop":             _coerce_bool(cfg.get("weather_music_loop"), DEFAULT_CONFIG["weather_music_loop"]),
        "music_single_file":      cfg.get("weather_music_single_file", DEFAULT_CONFIG["weather_music_single_file"]),
        "music_playlist_files":   cfg.get("weather_music_playlist_files", DEFAULT_CONFIG["weather_music_playlist_files"]),
    }


def _save_weather_config(config_dict: dict) -> None:
    """Validate and persist weather configuration via the config store."""
    cleaned: dict = {}
    for key in _WEATHER_CONFIG_KEYS:
        val = str(config_dict.get(key, "")).strip()
        if key in ("lat", "lon") and val:
            try:
                float(val)
            except ValueError:
                raise ValueError(f"Invalid value for {key}: {val!r}. Must be a number.")
        if key == "units" and val not in ("F", "C", ""):
            raise ValueError(f"Invalid units: {val!r}. Must be 'F' or 'C'.")
        if key == "bg_condition_override" and val not in _WEATHER_BG_VALID_CONDITIONS:
            val = ""
        if key == "seconds_per_segment" and val:
            try:
                sps = int(val)
            except ValueError:
                raise ValueError(f"Invalid seconds_per_segment: {val!r}.")
            if not (30 <= sps <= 600):
                raise ValueError(f"seconds_per_segment must be between 30 and 600, got {sps}.")
        cleaned[key] = val

    # Map from weather config dict keys to config store keys
    store_update = {
        "weather_lat":                    cleaned.get("lat", ""),
        "weather_lon":                    cleaned.get("lon", ""),
        "weather_location_name":          cleaned.get("location_name", ""),
        "weather_units":                  cleaned.get("units", "F"),
        "weather_seconds_per_segment":    cleaned.get("seconds_per_segment",
                                                       str(_WEATHER_SECONDS_PER_SEGMENT_DEFAULT)),
        "weather_bg_condition_override":  cleaned.get("bg_condition_override", ""),
    }
    enabled = config_dict.get("enabled")
    if enabled is not None:
        store_update["weather_channel_enabled"] = bool(enabled)
    logo_enabled = config_dict.get("logo_enabled")
    if logo_enabled is not None:
        store_update["weather_logo_enabled"] = bool(logo_enabled)

    cfg = store.get_config()
    store.save_config({**cfg, **store_update})


def _fetch_nws_alerts(lat: str, lon: str) -> list[dict]:
    """Fetch active NWS weather alerts (US only). Returns empty list on any failure."""
    try:
        url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
        resp = _requests.get(url, timeout=10, headers={
            "User-Agent": "RetroStation-MC/1.0",
            "Accept": "application/geo+json",
        })
        resp.raise_for_status()
        features = resp.json().get("features", [])
        alerts = []
        for feat in features:
            props = feat.get("properties", {})
            alerts.append({
                "event":       props.get("event", ""),
                "headline":    props.get("headline", ""),
                "description": props.get("description", ""),
                "severity":    props.get("severity", "Unknown"),
                "urgency":     props.get("urgency", "Unknown"),
                "certainty":   props.get("certainty", "Unknown"),
                "onset":       props.get("onset", ""),
                "expires":     props.get("expires", ""),
            })
        return alerts
    except Exception:
        logging.exception("_fetch_nws_alerts failed for lat=%s lon=%s", lat, lon)
        return []


def _fetch_open_meteo(lat: str, lon: str, units: str) -> dict | None:
    """Fetch current + hourly + daily weather from open-meteo. Returns dict or None."""
    temp_unit = "fahrenheit" if units != "C" else "celsius"
    wind_unit = "mph" if units != "C" else "kmh"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "weather_code,wind_speed_10m,wind_direction_10m"
        "&hourly=temperature_2m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,sunrise,sunset"
        f"&temperature_unit={temp_unit}&wind_speed_unit={wind_unit}"
        "&forecast_days=14&timezone=auto"
    )
    try:
        resp = _requests.get(url, timeout=10, headers={"User-Agent": "RetroStation-MC/1.0"})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logging.exception("_fetch_open_meteo failed for lat=%s lon=%s", lat, lon)
        return None


def _build_weather_payload(cfg: dict) -> dict:
    """Build the full weather payload from open-meteo data (or stub when unconfigured)."""
    now_utc = datetime.now(timezone.utc)
    updated_str = now_utc.isoformat()
    lat = cfg.get("lat", "")
    lon = cfg.get("lon", "")
    location_name = cfg.get("location_name") or "Local Weather"
    units = cfg.get("units") or "F"
    bg_override = cfg.get("bg_condition_override", "").strip()

    raw = None
    nws_alerts: list = []
    radar_url = _WEATHER_RADAR_IMAGE_ENDPOINT
    radar_image_path = ""
    radar_source_url = _build_radar_url(lat, lon)
    if lat and lon:
        try:
            radar_url, radar_image_path = _weather_radar_assets(cfg, updated_str)
        except Exception:
            logging.exception("Weather radar cache refresh failed for lat=%s lon=%s", lat, lon)
    if lat and lon:
        raw = _fetch_open_meteo(lat, lon, units)
        nws_alerts = _fetch_nws_alerts(lat, lon)

    if raw:
        cur = raw.get("current", {})
        cur_vars = raw.get("current_units", {})
        hourly = raw.get("hourly", {})
        daily = raw.get("daily", {})

        temp = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        humidity = cur.get("relative_humidity_2m")
        wcode = cur.get("weather_code", 0)
        wind_spd = cur.get("wind_speed_10m")
        wind_deg = cur.get("wind_direction_10m", 0)
        wind_str = (
            f"{_wind_dir(wind_deg)} {round(wind_spd)} {cur_vars.get('wind_speed_10m', 'mph')}"
            if wind_spd is not None else ""
        )

        now_info = {
            "temp": round(temp) if temp is not None else None,
            "condition": _wmo_label(wcode),
            "humidity": round(humidity) if humidity is not None else None,
            "wind": wind_str,
            "feels_like": round(feels) if feels is not None else None,
            "icon": _wmo_icon(wcode),
        }

        h_times = hourly.get("time", [])
        h_temps = hourly.get("temperature_2m", [])
        h_wcodes = hourly.get("weather_code", [])
        today_str = now_utc.strftime("%Y-%m-%d")

        def _period_avg(start_h: int, end_h: int) -> tuple[int | None, int]:
            temps_l: list[float] = []
            codes_l: list[int] = []
            for i, t in enumerate(h_times):
                if t.startswith(today_str):
                    try:
                        h = int(t[11:13])
                    except Exception:
                        continue
                    if start_h <= h < end_h:
                        if i < len(h_temps) and h_temps[i] is not None:
                            temps_l.append(h_temps[i])
                        if i < len(h_wcodes) and h_wcodes[i] is not None:
                            codes_l.append(h_wcodes[i])
            avg_t = round(sum(temps_l) / len(temps_l)) if temps_l else None
            dominant = max(set(codes_l), key=codes_l.count) if codes_l else 0
            return avg_t, dominant

        m_temp, m_code = _period_avg(6, 12)
        a_temp, a_code = _period_avg(12, 18)
        e_temp, e_code = _period_avg(18, 23)

        today_forecast = [
            {"label": "MORNING",   "temp": m_temp, "condition": _wmo_label(m_code), "icon": _wmo_icon(m_code)},
            {"label": "AFTERNOON", "temp": a_temp, "condition": _wmo_label(a_code), "icon": _wmo_icon(a_code)},
            {"label": "EVENING",   "temp": e_temp, "condition": _wmo_label(e_code), "icon": _to_night_icon(_wmo_icon(e_code))},
        ]

        d_times  = daily.get("time", [])
        d_maxes  = daily.get("temperature_2m_max", [])
        d_mins   = daily.get("temperature_2m_min", [])
        d_wcodes = daily.get("weather_code", [])
        extended = []
        five_day = []
        for i in range(min(14, len(d_times))):
            try:
                day_obj = date.fromisoformat(d_times[i])
                dow = "TODAY" if i == 0 else day_obj.strftime("%a").upper()
                mmdd = day_obj.strftime("%m/%d")
            except Exception:
                dow = "TODAY" if i == 0 else d_times[i][-5:]
                mmdd = d_times[i][-5:] if isinstance(d_times[i], str) and len(d_times[i]) >= 5 else ""
            hi  = round(d_maxes[i])  if i < len(d_maxes)  and d_maxes[i]  is not None else None
            lo  = round(d_mins[i])   if i < len(d_mins)   and d_mins[i]   is not None else None
            wc  = d_wcodes[i]        if i < len(d_wcodes)                              else 0
            if i < 5:
                five_day.append({"dow": dow, "hi": hi, "lo": lo,
                                 "date": mmdd, "condition": _wmo_label(wc), "icon": _wmo_icon(wc)})
            if i > 0:
                extended.append({"dow": dow, "hi": hi, "lo": lo,
                                  "date": mmdd, "condition": _wmo_label(wc), "icon": _wmo_icon(wc)})

        ticker: list[str] = []
        if nws_alerts:
            ticker = [a["headline"] or a["event"] for a in nws_alerts if a["headline"] or a["event"]]
        else:
            if wcode in (95, 96, 99):
                ticker.append("Severe Thunderstorms Possible")
            if wcode in (71, 73, 75, 77, 85, 86):
                ticker.append("Winter Weather Advisory in Effect")

        windy_threshold = _WINDY_BG_THRESHOLD_KMH if units == "C" else _WINDY_BG_THRESHOLD_MPH
        auto_bg = now_info["icon"]
        if wind_spd is not None and wind_spd >= windy_threshold and auto_bg in ("sunny", "partly_cloudy", "cloudy"):
            auto_bg = "windy"
        bg_condition = bg_override if bg_override in _WEATHER_BG_VALID_CONDITIONS and bg_override else auto_bg

        return {
            "updated":              updated_str,
            "location":             location_name,
            "now":                  now_info,
            "today":                today_forecast,
            "extended":             extended,
            "five_day":             five_day,
            "ticker":               ticker,
            "alerts":               nws_alerts,
            "radar_url":            radar_url,
            "radar_image_path":     radar_image_path,
            "radar_source_url":     radar_source_url,
            "bg_condition":         bg_condition,
            "bg_condition_override": bg_override,
        }

    # Stub / demo data when no coordinates configured
    bg_condition = bg_override if bg_override in _WEATHER_BG_VALID_CONDITIONS and bg_override else "cloudy"
    return {
        "updated":  updated_str,
        "location": location_name,
        "now": {
            "temp": None, "condition": "Not Configured", "humidity": None,
            "wind": "", "feels_like": None, "icon": "cloudy",
        },
        "today": [
            {"label": "MORNING",   "temp": None, "condition": "--", "icon": "cloudy"},
            {"label": "AFTERNOON", "temp": None, "condition": "--", "icon": "cloudy"},
            {"label": "EVENING",   "temp": None, "condition": "--", "icon": "cloudy_night"},
        ],
        "extended":  [],
        "five_day":  [],
        "ticker":    [],
        "alerts":    [],
        "radar_url": radar_url,
        "radar_image_path": radar_image_path,
        "radar_source_url": radar_source_url,
        "bg_condition":          bg_condition,
        "bg_condition_override": bg_override,
    }


# ── Weather channel renderer data provider ────────────────────────────────────
# Initialise the WeatherChannelManager here (after all weather helper functions
# are defined) and register its lifecycle with atexit.

def _get_weather_data_for_renderer() -> dict | None:
    """Return current weather payload for the weather channel renderer."""
    try:
        cfg     = _get_weather_config()
        payload = _build_weather_payload(cfg)
        return payload
    except Exception:
        logging.exception("_get_weather_data_for_renderer failed")
        return None


weather_manager = WeatherChannelManager(store, data_fetcher=_get_weather_data_for_renderer)
weather_manager.start()
atexit.register(weather_manager.stop)


def _lookup_zip_city(postal_code: str, country_code: str = "us") -> dict:
    """Look up city, state, lat, lon for a postal code via Nominatim (no key required).

    Raises ValueError when the lookup fails or no results are found.
    """
    postal_code = (postal_code or "").strip()
    if not postal_code:
        raise ValueError("Postal code must not be empty.")
    if len(postal_code) > 20:
        raise ValueError("Postal code is too long.")
    country_code = (country_code or "us").strip().lower()[:2]

    _US_STATE_ABBR = {
        "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
        "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
        "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
        "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
        "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
        "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
        "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
        "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
        "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
        "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
        "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
        "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
    }

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "postalcode": postal_code,
        "countrycodes": country_code,
        "format": "json",
        "addressdetails": "1",
        "limit": "1",
    }
    headers = {
        "User-Agent": "RetroStation-MC/1.0 (weather zip lookup; no tracking)",
        "Accept-Language": "en",
    }
    try:
        resp = _requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        logging.warning("_lookup_zip_city: Nominatim request failed: %s", exc)
        raise ValueError("Postal code lookup service unavailable. Enter city details manually.") from exc

    if not results:
        raise ValueError(f'No results found for postal code "{postal_code}".')

    item = results[0]
    lat = round(float(item.get("lat", 0)), 4)
    lon = round(float(item.get("lon", 0)), 4)
    address = item.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
        or ""
    )
    state_raw = address.get("state", "")
    state = _US_STATE_ABBR.get(state_raw, state_raw)
    name = f"{city}, {state}" if city and state else city or state or postal_code
    return {"name": name, "state": state, "lat": lat, "lon": lon}


# ─── Virtual Channels Admin Page ─────────────────────────────────────────────

@app.get("/virtual-channels")
def virtual_channels_page():
    """Admin page for virtual channels configuration."""
    wx_cfg = _get_weather_config()
    weather_music_files = _list_audio_files(WEATHER_MUSIC_DIR)
    return render_template(
        "virtual_channels.html",
        weather=wx_cfg,
        weather_music_files=weather_music_files,
    )


@app.post("/virtual-channels/weather/config")
def virtual_channels_weather_config():
    """Save Weather Channel configuration."""
    try:
        enabled_vals = request.form.getlist("weather_channel_enabled")
        enabled = "1" in enabled_vals
        logo_enabled_vals = request.form.getlist("weather_logo_enabled")
        logo_enabled = "1" in logo_enabled_vals
        weather_music_mode = request.form.get("weather_music_mode", "none").strip()
        if weather_music_mode not in ("none", "single", "playlist"):
            weather_music_mode = "none"
        weather_music_loop = request.form.get("weather_music_loop") == "1"
        weather_music_single_file = secure_filename(request.form.get("weather_music_single_file", "").strip())
        weather_music_playlist_files = [
            secure_filename(name)
            for name in request.form.getlist("weather_music_playlist_files")
            if name
        ]
        available_files = set(_list_audio_files(WEATHER_MUSIC_DIR))
        if weather_music_single_file not in available_files:
            weather_music_single_file = ""
        weather_music_playlist_files = [name for name in weather_music_playlist_files if name in available_files]

        weather_cfg = {
            "lat":                   request.form.get("weather_lat", "").strip(),
            "lon":                   request.form.get("weather_lon", "").strip(),
            "location_name":         request.form.get("weather_location_name", "").strip(),
            "units":                 request.form.get("weather_units", "F").strip(),
            "seconds_per_segment":   request.form.get("weather_seconds_per_segment", "300").strip(),
            "bg_condition_override": "",
            "enabled":               enabled,
            "logo_enabled":          logo_enabled,
        }
        _save_weather_config(weather_cfg)
        cfg = store.get_config()
        cfg.update(
            {
                "weather_music_mode": weather_music_mode,
                "weather_music_loop": weather_music_loop,
                "weather_music_single_file": weather_music_single_file,
                "weather_music_playlist_files": weather_music_playlist_files,
            }
        )
        store.save_config(cfg)
        flash("Weather Channel settings saved.", "success")

        # Restart (or stop) the weather HLS pipeline to pick up the new config.
        if weather_manager is not None:
            if enabled:
                weather_manager.start_pipeline()
            else:
                weather_manager._stop_pipeline()  # noqa: SLF001
    except ValueError as exc:
        flash(f"Invalid weather settings: {exc}", "error")
    except Exception as exc:
        flash(f"Could not save weather settings: {exc}", "error")
    return redirect(url_for("virtual_channels_page"))


# ─── Weather Channel Display ──────────────────────────────────────────────────

@app.get("/weather")
def weather_page():
    """Weather Channel display page (the TV output)."""
    return render_template("weather.html")


@app.get("/weather-radar/current.png")
def weather_radar_current_image():
    """Serve the current composited weather radar image from local cache."""
    cfg = _get_weather_config()
    lat = str(cfg.get("lat", "")).strip()
    lon = str(cfg.get("lon", "")).strip()
    if not lat or not lon:
        abort(404)

    try:
        region = create_or_update_weather_region(
            lat=lat,
            lon=lon,
            location_name=str(cfg.get("location_name", "")).strip() or "Local Weather",
            zip_code=_weather_region_zip(str(cfg.get("location_name", ""))),
            radius_miles=WEATHER_RADAR_DEFAULT_RADIUS_MILES,
            width=WEATHER_RADAR_DEFAULT_WIDTH,
            height=WEATHER_RADAR_DEFAULT_HEIGHT,
            refresh_seconds=WEATHER_RADAR_REFRESH_SECONDS,
        )
        radar_path = refresh_radar_if_stale(region)
        response = send_from_directory(radar_path.parent, radar_path.name)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as exc:
        logging.exception("weather_radar_current_image failed: %s", exc)
        abort(404)


# ─── Weather API endpoints ────────────────────────────────────────────────────

@app.get("/api/weather")
def api_weather():
    """Weather overlay data endpoint.

    Returns current conditions, today's forecast, extended outlook, 5-day
    forecast, radar URL, and ticker text.

    The channel cycles through 5 segments wall-clock aligned:
      0 – Current Conditions
      1 – 5-Day Forecast
      2 – Regional Radar
      3 – Severe Weather Alerts
      4 – Extended Forecast (10-Day)
    """
    cfg = _get_weather_config()
    payload = _build_weather_payload(cfg)

    try:
        seconds_per_segment = max(
            30, min(600, int(cfg.get("seconds_per_segment") or _WEATHER_SECONDS_PER_SEGMENT_DEFAULT))
        )
    except (TypeError, ValueError):
        seconds_per_segment = _WEATHER_SECONDS_PER_SEGMENT_DEFAULT

    _cycle_seconds = 5 * seconds_per_segment
    _now_ts = datetime.now(timezone.utc).timestamp()
    _cycle_pos = _now_ts % _cycle_seconds
    segment = int(_cycle_pos / seconds_per_segment)
    ms_until_next = int((seconds_per_segment - (_cycle_pos % seconds_per_segment)) * 1000)

    payload["segment"] = segment
    payload["segment_label"] = _WEATHER_SEGMENT_LABELS[segment]
    payload["seconds_per_segment"] = seconds_per_segment
    payload["ms_until_next"] = ms_until_next
    return jsonify(payload)


@app.route("/api/weather/bg_override", methods=["GET", "POST", "DELETE"])
def api_weather_bg_override():
    """Admin endpoint to get/set/clear the animated-background condition override.

    GET    → {"condition": "<current override or ''>"}
    POST   → body JSON {"condition": "<value>"}  sets override; "" or "auto" clears it.
    DELETE → clears the override.
    """
    if request.method == "GET":
        cfg = _get_weather_config()
        return jsonify({"condition": cfg.get("bg_condition_override", "")})

    if request.method == "DELETE":
        try:
            wcfg = _get_weather_config()
            wcfg["bg_condition_override"] = ""
            _save_weather_config(wcfg)
            return jsonify({"ok": True, "condition": ""})
        except Exception as exc:
            logging.exception("api_weather_bg_override DELETE failed: %s", exc)
            return jsonify({"ok": False, "error": "Internal server error"}), 500

    # POST
    data = request.get_json(silent=True) or {}
    condition = str(data.get("condition", "")).strip()
    if condition == "auto":
        condition = ""
    if condition not in _WEATHER_BG_VALID_CONDITIONS:
        return jsonify({"ok": False, "error": f"Invalid condition: {condition!r}"}), 400
    try:
        wcfg = _get_weather_config()
        wcfg["bg_condition_override"] = condition
        _save_weather_config(wcfg)
        return jsonify({"ok": True, "condition": condition})
    except Exception as exc:
        logging.exception("api_weather_bg_override POST failed: %s", exc)
        return jsonify({"ok": False, "error": "Internal server error"}), 500


@app.get("/api/weather/zip-lookup")
def api_weather_zip_lookup():
    """US zip code → lat/lon/city lookup (Nominatim, no API key required).

    Query params:
      zip     — 5-digit US postal code (required)
    Returns JSON: {ok: true, name, state, lat, lon} or {ok: false, error}.
    """
    postal_code = request.args.get("zip", "").strip()
    if not postal_code:
        return jsonify({"ok": False, "error": "zip parameter is required"}), 400
    try:
        info = _lookup_zip_city(postal_code)
        return jsonify({"ok": True, **info})
    except ValueError as exc:
        # All ValueErrors from _lookup_zip_city are controlled, user-safe messages.
        msg = exc.args[0] if exc.args else "Invalid postal code or lookup failed."
        return jsonify({"ok": False, "error": msg}), 404
    except Exception as exc:
        logging.exception("api_weather_zip_lookup failed: %s", exc)
        return jsonify({"ok": False, "error": "Internal server error"}), 500


if __name__ == "__main__":
    host = __import__("os").environ.get("RETROGUIDE_HOST", "0.0.0.0")
    port = int(__import__("os").environ.get("RETROGUIDE_PORT", "8787"))
    app.run(host=host, port=port, debug=False)
