from __future__ import annotations

import atexit
import copy
import csv
import hashlib
import io
import json
import logging
import math
import posixpath
import shutil
import subprocess
import threading
import traceback
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from urllib.parse import urlparse

import requests as _requests

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, send_from_directory, stream_with_context, url_for
from flask import request
from werkzeug.utils import secure_filename

from app.config_store import ConfigStore, DEFAULT_CONFIG
from app.ffmpeg_profiles import normalize_hardware_acceleration_mode
from app.hls_playlist import trim_playlist_for_delayed_live_edge
from app.guide_state import patch_display_state
from app.guide_preview import (
    detect_preview_aspect_ratio, effective_preview_aspect_ratio, is_http_url, known_preview_aspect_ratio,
    normalize_preview_aspect_mode, normalize_preview_audio_mode, normalize_preview_source_type, parse_preview_m3u,
    preview_source_cache_key, resolve_preview_source,
)
from app.hdhomerun_discovery import HDHomeRunDiscoveryService, normalize_or_generate_device_id
from app.manager import (
    GuideManager, WeatherChannelManager, TrafficChannelManager, NewsChannelManager,
    STANDBY_SEGMENT, STATIC_SEGMENT, STANDBY_DURATION_SECS, MUSIC_DIR, WEATHER_MUSIC_DIR,
    WEATHER_PLAYLIST, TRAFFIC_PLAYLIST, NEWS_PLAYLIST, _build_audio_ffmpeg_args,
)
from app.m3u_parser import parse_m3u
from app.source_fetch import read_text_or_file
from app.traffic_channel import (
    _TRAFFIC_DEMO_CITIES_SEED,
    build_traffic_payload as _build_traffic_payload,
    city_slug as _traffic_city_slug,
    seed_cities as _traffic_seed_cities,
    get_road_geojson as _get_traffic_road_geojson,
    BASEMAP_DIR as TRAFFIC_BASEMAP_DIR,
    CHANNEL_DISCLAIMER as TRAFFIC_DISCLAIMER,
    prewarm_basemaps as _prewarm_traffic_basemaps,
    prewarm_roads_cache as _prewarm_traffic_roads,
    ensure_basemap as _ensure_traffic_basemap,
)
from app.news_channel import build_news_payload, validate_feed_urls
from app.channel_mix import normalize_channel_mix_config, get_active_channel_mix_slot, get_active_available_channel, ChannelMixHLSState
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
TRAFFIC_LOGO_DIR = BASE_DIR / "data" / "traffic_logo"
NEWS_LOGO_DIR = BASE_DIR / "data" / "news_logo"
CHANNEL_MIX_LOGO_DIR = BASE_DIR / "data" / "channel_mix_logo"
STANDBY_PATTERN_DIR = BASE_DIR / "data" / "standby_patterns"
GUIDE_PREVIEW_DIR = BASE_DIR / "data" / "guide_preview"
GUIDE_DELAY_SEGMENTS = 2
GUIDE_MIN_BUFFER_SECS = 18.0
GUIDE_MIN_BUFFER_SEGMENTS = 3
GUIDE_STANDBY_WINDOW_SEGMENTS = 3
GUIDE_MIN_VISIBLE_SEGMENTS = 3
HDHOMERUN_TUNER_COUNT = 2
CHANNEL_MIX_LOCAL_PLAYLIST = "channel-mix.m3u8"
CHANNEL_MIX_SOURCE_PLAYLIST = "channel-mix-source.m3u8"
CHANNEL_MIX_REFRESH_SECONDS = 1.0
HDHOMERUN_FEATURE_AVAILABLE = False  # backend retained but hidden/disabled for v1.4.0

ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"}
MAX_MUSIC_FILE_BYTES = 100 * 1024 * 1024  # 100 MB per file
MAX_MUSIC_REQUEST_BYTES = 1024 * 1024 * 1024  # 1 GB per multi-file upload request
ALLOWED_GUIDE_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
DEFAULT_GUIDE_LOGO_EXTENSION_ORDER = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg")
MAX_GUIDE_LOGO_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_WEATHER_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
DEFAULT_WEATHER_LOGO_EXTENSION_ORDER = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg")
DEFAULT_VIRTUAL_LOGO_EXTENSION_ORDER = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg")
ALLOWED_VIRTUAL_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
ALLOWED_STANDBY_PATTERN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_STANDBY_PATTERN_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_GUIDE_PREVIEW_EXTENSIONS = {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".mpeg", ".mpg", ".ts"}
MAX_GUIDE_PREVIEW_BYTES = 1024 * 1024 * 1024  # 1 GB; also bounded by MAX_CONTENT_LENGTH

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.secret_key = "retro-guide-poc-local-only"
app.config["SESSION_COOKIE_NAME"] = "retro_guide_session"
app.config["MAX_CONTENT_LENGTH"] = MAX_MUSIC_REQUEST_BYTES

GUIDE_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

store = ConfigStore()
if not HDHOMERUN_FEATURE_AVAILABLE and store.get_config().get("hdhomerun_enabled"):
    store.save_config({"hdhomerun_enabled": False, "hdhomerun_rebroadcast_channels": []})
manager = GuideManager(store)
manager.start()
atexit.register(manager.stop)

# weather_manager is initialized later in this module after the weather helper
# functions (_build_weather_payload etc.) have been defined.  Route handlers
# reference this module-level name at *call* time (after full module load) so
# late assignment is safe.
weather_manager: "WeatherChannelManager | None" = None
traffic_manager: "TrafficChannelManager | None" = None
news_manager: "NewsChannelManager | None" = None

VIRTUAL_GUIDE_CHANNEL_ID   = "rsmc-guide"
VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID = "rsmc-guide-sd"
VIRTUAL_WEATHER_CHANNEL_ID = "rsmc-weather"
VIRTUAL_TRAFFIC_CHANNEL_ID = "rsmc-traffic"
VIRTUAL_NEWS_CHANNEL_ID    = "rsmc-news"
VIRTUAL_CHANNEL_MIX_ID      = "rsmc-channel-mix"


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


def _normalize_aspect_ratio(value: str | None) -> str:
    """Return a validated aspect ratio string; defaults to ``'16:9'``."""
    if value and value.strip() in ("16:9", "4:3"):
        return value.strip()
    return DEFAULT_CONFIG["aspect_ratio"]


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


def _normalize_music_selection(prefix: str, form) -> dict:
    """Normalize per-channel music settings against the shared MUSIC_DIR library."""
    mode = str(form.get(f"{prefix}music_mode", "none") or "none").strip().lower()
    if mode not in {"none", "single", "playlist", "all"}:
        mode = "none"
    loop = form.get(f"{prefix}music_loop") == "1"
    available = set(_list_audio_files(MUSIC_DIR))
    single = secure_filename(str(form.get(f"{prefix}music_single_file", "") or "").strip())
    if single not in available:
        single = ""
    selected = [secure_filename(name) for name in form.getlist(f"{prefix}music_playlist_files") if name]
    selected = [name for name in selected if name in available]
    return {
        f"{prefix}music_mode": mode,
        f"{prefix}music_loop": loop,
        f"{prefix}music_single_file": single,
        f"{prefix}music_playlist_files": selected,
    }


def _music_view(config: dict, prefix: str) -> dict:
    return {
        "music_mode": config.get(f"{prefix}music_mode", "none"),
        "music_loop": _coerce_bool(config.get(f"{prefix}music_loop"), False),
        "music_single_file": config.get(f"{prefix}music_single_file", ""),
        "music_playlist_files": config.get(f"{prefix}music_playlist_files", []) or [],
    }


def _migrate_legacy_weather_music() -> None:
    """Copy legacy per-weather audio into the shared library without overwriting files."""
    if not WEATHER_MUSIC_DIR.is_dir():
        return
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for src in WEATHER_MUSIC_DIR.iterdir():
        if not src.is_file() or src.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
            continue
        dst = MUSIC_DIR / src.name
        if not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


_migrate_legacy_weather_music()


def coerce_form(form) -> dict:
    hdhomerun_values = form.getlist("hdhomerun_enabled") if hasattr(form, "getlist") else [form.get("hdhomerun_enabled")]
    virtual_channel_values = form.getlist("virtual_channels_export_enabled") if hasattr(form, "getlist") else [form.get("virtual_channels_export_enabled")]
    secondary_values = form.getlist("guide_secondary_enabled") if hasattr(form, "getlist") else [form.get("guide_secondary_enabled")]
    hdhomerun_rebroadcast_values = form.getlist("hdhomerun_rebroadcast_channels") if hasattr(form, "getlist") else []
    cfg = {
        "playlist_source": form.get("playlist_source", DEFAULT_CONFIG["playlist_source"]).strip(),
        "xmltv_source": form.get("xmltv_source", DEFAULT_CONFIG["xmltv_source"]).strip(),
        "theme": form.get("theme", DEFAULT_CONFIG["theme"]).strip(),
        "title": form.get("title", DEFAULT_CONFIG["title"]).strip(),
        "resolution": form.get("resolution", DEFAULT_CONFIG["resolution"]).strip(),
        "guide_secondary_enabled": any(_coerce_bool(value, False) for value in secondary_values),
        "guide_secondary_resolution": (form.get("guide_secondary_resolution", DEFAULT_CONFIG["guide_secondary_resolution"]) or DEFAULT_CONFIG["guide_secondary_resolution"]).strip(),
        "hardware_acceleration_mode": normalize_hardware_acceleration_mode(
            form.get("hardware_acceleration_mode")
        ),
        "aspect_ratio": _normalize_aspect_ratio(form.get("aspect_ratio")),
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
        # Export selection is independent from each virtual channel's enabled state.
        # This checkbox controls whether enabled optional virtual channels appear
        # in channel.m3u/channel.m3u8/channel.xmltv; it never enables/disables them.
        "virtual_channels_export_enabled": any(_coerce_bool(value, False) for value in virtual_channel_values),
        "hdhomerun_enabled": False if not HDHOMERUN_FEATURE_AVAILABLE else any(_coerce_bool(value, False) for value in hdhomerun_values),
        "hdhomerun_rebroadcast_channels": [str(value).strip() for value in hdhomerun_rebroadcast_values if str(value).strip()],
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
    config["guide_preview_enabled"] = _coerce_bool(
        config.get("guide_preview_enabled"), DEFAULT_CONFIG["guide_preview_enabled"]
    )
    config["guide_preview_source_type"] = normalize_preview_source_type(
        config.get("guide_preview_source_type")
    )
    config["guide_preview_audio_mode"] = normalize_preview_audio_mode(
        config.get("guide_preview_audio_mode")
    )
    config["guide_preview_aspect_mode"] = normalize_preview_aspect_mode(
        config.get("guide_preview_aspect_mode")
    )
    config["guide_preview_effective_aspect_ratio"] = effective_preview_aspect_ratio(config)
    config["guide_message_enabled"] = _coerce_bool(
        config.get("guide_message_enabled"), DEFAULT_CONFIG["guide_message_enabled"]
    )
    config["guide_message_text"] = str(config.get("guide_message_text", "") or "")
    try:
        config["guide_message_interval_seconds"] = max(3, min(60, int(config.get("guide_message_interval_seconds", 8))))
    except (TypeError, ValueError):
        config["guide_message_interval_seconds"] = DEFAULT_CONFIG["guide_message_interval_seconds"]
    preview_filename = secure_filename(str(config.get("guide_preview_file", "") or ""))
    if preview_filename and not (GUIDE_PREVIEW_DIR / preview_filename).is_file():
        preview_filename = ""
    config["guide_preview_file"] = preview_filename
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
        hdhomerun_source_channels=_source_playlist_channels_for_admin(config) if HDHOMERUN_FEATURE_AVAILABLE else [],
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

def _default_virtual_logo_name(logo_dir: Path) -> str:
    """Return a TiViMate-friendly raster logo when available, preferring PNG over SVG."""
    for ext in DEFAULT_VIRTUAL_LOGO_EXTENSION_ORDER:
        path = logo_dir / f"default{ext}"
        if path.is_file():
            return path.name
    if not logo_dir.is_dir():
        return ""
    for path in sorted(logo_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_VIRTUAL_LOGO_EXTENSIONS:
            continue
        safe_name = secure_filename(path.name)
        if safe_name == path.name:
            return safe_name
    return ""


def _virtual_logo_url(base_url: str, route_name: str, logo_dir: Path) -> str:
    logo_name = _default_virtual_logo_name(logo_dir)
    return f"{base_url}/{route_name}/{logo_name}" if logo_name else ""


def _build_channel_m3u_content(channel_name: str, stream_url: str, xmltv_url: str, logo_url: str = "") -> str:
    """Return M3U playlist content for the virtual guide channel."""
    logo_attr = f' tvg-logo="{logo_url}"' if logo_url else ""
    return (
        f'#EXTM3U url-tvg="{xmltv_url}" x-tvg-url="{xmltv_url}"\n'
        f'#EXTINF:-1 tvg-id="{VIRTUAL_GUIDE_CHANNEL_ID}" tvg-name="{channel_name}"'
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
    guide_name = _sanitize_xmltv_text(config.get("title"), "Channel Guide")
    secondary_enabled = _coerce_bool(
        config.get("guide_secondary_enabled"),
        DEFAULT_CONFIG.get("guide_secondary_enabled", False),
    )
    entries = [
        {
            "id": VIRTUAL_GUIDE_CHANNEL_ID,
            "name": f"{guide_name} HD" if secondary_enabled else guide_name,
            "stream_url": base_url + "/hls/master.m3u8",
            "logo_url": _channel_logo_url(config, base_url),
            "channel_number": 1,
            "description": "Retro-style TV guide channel.",
        }
    ]
    if secondary_enabled:
        entries.append(
            {
                "id": VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID,
                "name": f"{guide_name} SD",
                "stream_url": base_url + "/hls/guide-secondary.m3u8",
                "logo_url": _channel_logo_url(config, base_url),
                "channel_number": "1.1",
                "description": "Secondary lower-resolution Retro-style TV guide channel.",
            }
        )
    if _coerce_bool(config.get("weather_channel_enabled"), DEFAULT_CONFIG["weather_channel_enabled"]):
        entries.append(
            {
                "id": VIRTUAL_WEATHER_CHANNEL_ID,
                "name": _weather_channel_display_name(config),
                "stream_url": base_url + "/hls/weather.m3u8",
                "logo_url": _weather_logo_url(config, base_url),
                "channel_number": 2,
                "description": "Retro-style local weather channel.",
            }
        )
    if _coerce_bool(config.get("traffic_channel_enabled"), DEFAULT_CONFIG["traffic_channel_enabled"]):
        entries.append(
            {
                "id": VIRTUAL_TRAFFIC_CHANNEL_ID,
                "name": "Simulated Traffic",
                "stream_url": base_url + "/hls/traffic.m3u8",
                "logo_url": _virtual_logo_url(base_url, "traffic-logo", TRAFFIC_LOGO_DIR),
                "channel_number": 3,
                "description": "Retro-style simulated traffic channel. All conditions are synthetic.",
            }
        )
    if _coerce_bool(config.get("news_channel_enabled"), DEFAULT_CONFIG["news_channel_enabled"]):
        entries.append(
            {
                "id": VIRTUAL_NEWS_CHANNEL_ID,
                "name": "News Now",
                "stream_url": base_url + "/hls/news.m3u8",
                "logo_url": _virtual_logo_url(base_url, "news-logo", NEWS_LOGO_DIR),
                "channel_number": 4,
                "description": "Generated RSS/Atom headline channel.",
            }
        )
    if _coerce_bool(config.get("channel_mix_enabled"), DEFAULT_CONFIG["channel_mix_enabled"]):
        entries.append(
            {
                "id": VIRTUAL_CHANNEL_MIX_ID,
                "name": _sanitize_xmltv_text(config.get("channel_mix_name"), "Channel Mix"),
                "stream_url": base_url + "/hls/channel-mix.m3u8",
                "logo_url": _virtual_logo_url(base_url, "channel-mix-logo", CHANNEL_MIX_LOGO_DIR),
                "channel_number": 5,
                "description": "Wall-clock mix of selected RSMC virtual channels.",
            }
        )
    return entries



def _build_exported_virtual_channel_entries(config: dict, base_url: str) -> list[dict]:
    """Return playlist/XMLTV entries without changing channel runtime state.

    The Guide is always exported. Optional generated channels are included only
    when the main-page export checkbox is enabled, and then only if each channel
    is independently enabled on the Virtual Channels page.
    """
    entries = _build_virtual_channel_entries(config, base_url)
    if _coerce_bool(config.get("virtual_channels_export_enabled"), DEFAULT_CONFIG["virtual_channels_export_enabled"]):
        return entries
    return [
        entry
        for entry in entries
        if entry.get("id") in {VIRTUAL_GUIDE_CHANNEL_ID, VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID}
    ]

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
            f',{channel_name}'
        )
        lines.append(str(channel["stream_url"]))
    return "\n".join(lines) + "\n"


def _hdhomerun_enabled(config: dict) -> bool:
    if not HDHOMERUN_FEATURE_AVAILABLE:
        return False
    return _coerce_bool(config.get("hdhomerun_enabled"), DEFAULT_CONFIG["hdhomerun_enabled"])


def _require_hdhomerun_enabled(config: dict) -> None:
    if not _hdhomerun_enabled(config):
        abort(404)


def _hdhomerun_device_id(config: dict) -> str:
    raw = str(config.get("hdhomerun_device_id") or "").strip()
    device_id = normalize_or_generate_device_id(raw)
    if device_id != raw.upper():
        store.save_config({"hdhomerun_device_id": device_id})
    return device_id


def _hdhomerun_discovery_enabled() -> bool:
    config = {**DEFAULT_CONFIG, **store.get_config()}
    return _hdhomerun_enabled(config)


def _hdhomerun_discovery_device_id() -> str:
    config = {**DEFAULT_CONFIG, **store.get_config()}
    return _hdhomerun_device_id(config)


def _source_channel_key(channel: dict, index: int) -> str:
    """Return a stable opaque key for an imported source-playlist channel.

    The key intentionally excludes the stream URL so regenerated HLS URLs or
    query tokens do not silently clear an administrator's rebroadcast choice.
    """
    identity = "\x1f".join(
        str(channel.get(field) or "").strip()
        for field in ("id", "number", "name", "group")
    )
    if not identity.replace("\x1f", ""):
        identity = f"source-index:{index}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _source_playlist_channels(config: dict) -> list[dict]:
    playlist_source = str(config.get("playlist_source") or DEFAULT_CONFIG["playlist_source"]).strip()
    return parse_m3u(playlist_source)


def _hdhomerun_rebroadcast_selection(config: dict) -> set[str]:
    raw = config.get("hdhomerun_rebroadcast_channels", DEFAULT_CONFIG["hdhomerun_rebroadcast_channels"])
    if not isinstance(raw, list):
        return set()
    return {str(value).strip() for value in raw if str(value).strip()}


def _source_playlist_channels_for_admin(config: dict) -> list[dict]:
    """Return source channels annotated for the HDHomeRun admin selector."""
    selected = _hdhomerun_rebroadcast_selection(config)
    try:
        channels = _source_playlist_channels(config)
    except Exception as exc:
        manager.logger.warning("hdhomerun", f"Unable to load source playlist for rebroadcast selector: {_error_label(exc)}")
        return []
    annotated = []
    for index, channel in enumerate(channels):
        item = dict(channel)
        item["rebroadcast_key"] = _source_channel_key(channel, index)
        item["rebroadcast_selected"] = item["rebroadcast_key"] in selected
        item["source_index"] = index
        annotated.append(item)
    return annotated


def _hdhomerun_channels(config: dict, base_url: str) -> list[dict]:
    """Build the effective HDHomeRun lineup.

    RSMC-owned virtual channels are included automatically. Imported source
    playlist channels are excluded unless explicitly selected for rebroadcast.
    This keeps RSMC complementary to upstream playout systems such as ErsatzTV.
    """
    channels: list[dict] = []
    for virtual in _build_virtual_channel_entries(config, base_url):
        stream_url = virtual["stream_url"]
        # Plex/HDHomeRun tuning is most reliable when every RSMC-owned channel
        # resolves directly to a media playlist.  The Guide's public M3U may use
        # master.m3u8 for ordinary HLS clients, but the tuner remux should not
        # change playlist type or depend on variant selection.
        if virtual["id"] == VIRTUAL_GUIDE_CHANNEL_ID:
            stream_url = base_url + "/hls/guide.m3u8"
        elif virtual["id"] == VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID:
            stream_url = base_url + "/hls/guide-secondary.m3u8"
        channels.append(
            {
                "id": virtual["id"],
                "name": virtual["name"],
                "number": str(virtual["channel_number"]),
                "stream_url": stream_url,
                "source_kind": "rsmc",
            }
        )

    selected = _hdhomerun_rebroadcast_selection(config)
    if not selected:
        return channels

    try:
        source_channels = _source_playlist_channels(config)
    except Exception as exc:
        manager.logger.warning("hdhomerun", f"Unable to load selected source channels: {_error_label(exc)}")
        return channels

    for index, channel in enumerate(source_channels):
        if _source_channel_key(channel, index) not in selected:
            continue
        item = dict(channel)
        item["source_kind"] = "source"
        item["source_index"] = index
        channels.append(item)
    return channels


def _is_hdhomerun_stream_url_allowed(stream_url: str) -> bool:
    parsed = urlparse(stream_url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _build_hdhomerun_lineup(channels: list[dict], base_url: str) -> list[dict]:
    lineup: list[dict] = []
    for index, channel in enumerate(channels):
        if not _is_hdhomerun_stream_url_allowed(str(channel.get("stream_url") or "")):
            continue
        lineup.append(
            {
                "GuideNumber": str(channel.get("number") or index + 1),
                "GuideName": _sanitize_xmltv_text(channel.get("name"), f"Channel {index + 1}"),
                "URL": f"{base_url}/hdhr/channel/{index}",
            }
        )
    return lineup


@app.get("/channel.m3u")
def channel_playlist():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    base_url = request.host_url.rstrip("/")
    xmltv_url = base_url + "/channel.xmltv"
    content = _build_channels_m3u_content(_build_exported_virtual_channel_entries(config, base_url), xmltv_url)
    resp = Response(content, mimetype="application/x-mpegURL")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/channel.m3u8")
def channel_playlist_m3u8():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    base_url = request.host_url.rstrip("/")
    xmltv_url = base_url + "/channel.xmltv"
    content = _build_channels_m3u_content(_build_exported_virtual_channel_entries(config, base_url), xmltv_url)
    resp = Response(content, mimetype="application/vnd.apple.mpegurl")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/discover.json")
def hdhomerun_discover():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    _require_hdhomerun_enabled(config)
    base_url = request.host_url.rstrip("/")
    resp = jsonify(
        {
            "DeviceAuth": "",
            "DeviceID": _hdhomerun_device_id(config),
            "FirmwareName": "hdhomeruntc_atsc",
            "FirmwareVersion": "20260809",
            "FriendlyName": "RetroStation MC",
            "BaseURL": base_url,
            "LineupURL": f"{base_url}/lineup.json",
            "Manufacturer": "RetroStation MC",
            "ManufacturerURL": "",
            "ModelNumber": "HDTC-2US",
            "TunerCount": HDHOMERUN_TUNER_COUNT,
        }
    )
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/device.xml")
def hdhomerun_device_xml():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    _require_hdhomerun_enabled(config)
    base_url = request.host_url.rstrip("/")
    device_id = _hdhomerun_device_id(config)
    content = (
        '<root xmlns="urn:schemas-upnp-org:device-1-0">'
        f"<URLBase>{xml_escape(base_url)}</URLBase>"
        "<specVersion><major>1</major><minor>0</minor></specVersion>"
        "<device>"
        "<deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>"
        "<friendlyName>RetroStation MC</friendlyName>"
        "<manufacturer>RetroStation MC</manufacturer>"
        "<modelName>HDTC-2US</modelName>"
        "<modelNumber>HDTC-2US</modelNumber>"
        f"<serialNumber>{xml_escape(device_id)}</serialNumber>"
        f"<UDN>uuid:{xml_escape(device_id)}</UDN>"
        "</device>"
        "</root>"
    )
    resp = Response(content, mimetype="application/xml")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/lineup_status.json")
def hdhomerun_lineup_status():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    _require_hdhomerun_enabled(config)
    resp = jsonify(
        {
            "ScanInProgress": 0,
            "ScanPossible": 1,
            "Source": "Cable",
            "SourceList": ["Cable"],
        }
    )
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/lineup.json")
def hdhomerun_lineup():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    _require_hdhomerun_enabled(config)
    base_url = request.host_url.rstrip("/")
    resp = jsonify(_build_hdhomerun_lineup(_hdhomerun_channels(config, base_url), base_url))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def _build_hdhomerun_xmltv_content(channels: list[dict], config: dict) -> str:
    """Build XMLTV that exactly mirrors the active HDHomeRun lineup.

    RSMC-owned virtual channels get generated placeholder programme blocks.
    Selected imported channels reuse channel/programme records from the configured
    upstream XMLTV source when their tvg-id matches. If upstream guide data is
    unavailable, a valid fallback channel/programme record is generated so DVR
    clients can still map the tuner channel.
    """
    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "RetroStation MC",
            "source-info-name": "RetroStation MC HDHomeRun",
        },
    )

    upstream_channels: dict[str, ET.Element] = {}
    upstream_programmes: dict[str, list[ET.Element]] = {}
    xmltv_source = str(config.get("xmltv_source") or "").strip()
    if xmltv_source:
        try:
            source_root = ET.fromstring(read_text_or_file(xmltv_source, timeout=20))
            for elem in source_root.findall("channel"):
                channel_id = str(elem.attrib.get("id") or "").strip()
                if channel_id:
                    upstream_channels[channel_id] = elem
            for elem in source_root.findall("programme"):
                channel_id = str(elem.attrib.get("channel") or "").strip()
                if channel_id:
                    upstream_programmes.setdefault(channel_id, []).append(elem)
        except Exception as exc:
            manager.logger.warning(
                "hdhomerun",
                f"Unable to load upstream XMLTV for HDHomeRun guide export: {_error_label(exc)}",
            )

    now = datetime.now(timezone.utc)
    slot_start = now.replace(minute=0, second=0, microsecond=0)
    slot_start = slot_start.replace(hour=(slot_start.hour // 4) * 4)
    total_slots = 6 * 7

    fallback_channels: list[dict] = []
    for index, channel in enumerate(channels):
        channel_id = _sanitize_xmltv_text(channel.get("id"), f"rsmc-hdhr-{index + 1}")
        name = _sanitize_xmltv_text(channel.get("name"), f"Channel {index + 1}")
        number = str(channel.get("number") or channel.get("channel_number") or index + 1)
        source_kind = str(channel.get("source_kind") or "rsmc")

        if source_kind == "source" and channel_id in upstream_channels:
            channel_el = copy.deepcopy(upstream_channels[channel_id])
            # Ensure the tuner-facing name and logo remain usable even if the
            # upstream XMLTV record is sparse.
            if channel_el.find("display-name") is None:
                ET.SubElement(channel_el, "display-name").text = name
            logo_url = str(channel.get("logo") or channel.get("logo_url") or "").strip()
            if logo_url and channel_el.find("icon") is None:
                ET.SubElement(channel_el, "icon", {"src": logo_url})
            tv.append(channel_el)
            for prog in upstream_programmes.get(channel_id, []):
                tv.append(copy.deepcopy(prog))
            if upstream_programmes.get(channel_id):
                continue
        else:
            channel_el = ET.SubElement(tv, "channel", {"id": channel_id})
            ET.SubElement(channel_el, "display-name").text = name
            # A second display-name containing the guide number improves mapping
            # in clients that inspect XMLTV names while matching tuner channels.
            ET.SubElement(channel_el, "display-name").text = f"{number} {name}"
            logo_url = str(channel.get("logo_url") or channel.get("logo") or "").strip()
            if logo_url:
                ET.SubElement(channel_el, "icon", {"src": logo_url})

        fallback_channels.append(
            {
                "id": channel_id,
                "name": name,
                "description": _sanitize_xmltv_text(
                    channel.get("description"),
                    "RetroStation MC HDHomeRun channel.",
                ),
            }
        )

    for channel in fallback_channels:
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
            ET.SubElement(prog, "title").text = channel["name"]
            ET.SubElement(prog, "desc").text = channel["description"]
            entry_start = slot_end

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode")


@app.get("/hdhr/guide.xml")
@app.get("/hdhr/xmltv.xml")
def hdhomerun_xmltv():
    config = {**DEFAULT_CONFIG, **store.get_config()}
    _require_hdhomerun_enabled(config)
    base_url = request.host_url.rstrip("/")
    content = _build_hdhomerun_xmltv_content(_hdhomerun_channels(config, base_url), config)
    resp = Response(content, mimetype="application/xml")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def _hdhomerun_local_playlist_for_channel(channel: dict) -> Path | None:
    """Return the local HLS media playlist for an RSMC-owned virtual channel.

    Plex tunes RSMC through a long-lived MPEG-TS response.  Feeding the tuner
    FFmpeg from RSMC's own Flask HLS URLs causes FFmpeg to make a new HTTP
    request back into the same Flask process for every playlist reload and TS
    segment.  Under sustained Plex playback those loopback requests can exhaust
    the Flask process file-descriptor limit.

    RSMC owns these playlists already, so the tuner remux should read them from
    disk directly.  Imported/rebroadcast source channels continue to use their
    configured HTTP(S) URL.
    """
    if str(channel.get("source_kind") or "") != "rsmc":
        return None
    playlist_by_id = {
        VIRTUAL_GUIDE_CHANNEL_ID: "guide.m3u8",
        VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID: "guide-secondary.m3u8",
        VIRTUAL_WEATHER_CHANNEL_ID: "weather.m3u8",
        VIRTUAL_TRAFFIC_CHANNEL_ID: "traffic.m3u8",
        VIRTUAL_NEWS_CHANNEL_ID: "news.m3u8",
        VIRTUAL_CHANNEL_MIX_ID: CHANNEL_MIX_LOCAL_PLAYLIST,
    }
    filename = playlist_by_id.get(str(channel.get("id") or ""))
    return (OUTPUT_DIR / filename) if filename else None


def _build_hdhomerun_ffmpeg_command(stream_url: str, *, local_hls: bool = False) -> list[str]:
    """Return the proven low-latency HLS/HTTP -> MPEG-TS tuner remux command.

    Plex treats the lineup URL as a live tuner and expects transport-stream bytes
    promptly.  Do not synchronously probe the source before starting FFmpeg: the
    probe delay can exceed Plex's tune startup window.  This intentionally matches
    the remux behavior that was used before the v1.4.0 virtual-channel migrations.
    """
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-fflags", "+genpts",
    ]
    # RSMC-owned HLS playlists are local files. Without real-time pacing FFmpeg
    # drains the playlist's existing live window as fast as possible before it
    # reaches the live edge. Plex treats the endpoint as a hardware tuner and
    # expects transport-stream bytes at approximately real-time cadence.
    if local_hls:
        command.extend(["-re", "-live_start_index", "-1"])
    command.extend([
        "-i", stream_url,
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c", "copy",
        "-f", "mpegts",
        "pipe:1",
    ])
    return command


def _hdhomerun_mpegts_chunks(stream_url: str, *, local_hls: bool = False):
    """Yield one HDHomeRun-compatible MPEG-TS stream and reap FFmpeg on exit.

    Keep this lifecycle deliberately minimal. Plex holds tuner responses open for
    long periods and may reconnect while changing channels.  The original RSMC
    HDHomeRun implementation used DEVNULL for FFmpeg stderr and no per-session
    helper thread; preserving that model avoids accumulating pipe/thread
    resources during long-running Plex sessions.
    """
    command = _build_hdhomerun_ffmpeg_command(stream_url, local_hls=local_hls)
    manager.logger.info("hdhomerun", f"Opening tuner stream: {stream_url}")
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except (OSError, ValueError) as exc:
        manager.logger.error("hdhomerun", f"Unable to start FFmpeg tuner remux: {_error_label(exc)}")
        return

    try:
        if proc.stdout is None:
            return
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    except (BrokenPipeError, GeneratorExit, OSError):
        # Client disconnects are normal when a channel is stopped or changed.
        pass
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        manager.logger.info("hdhomerun", f"Closed tuner stream: {stream_url}")


@app.route("/hdhr/channel/<int:channel_index>", methods=["GET", "HEAD"])
def hdhomerun_channel_tune(channel_index: int):
    config = {**DEFAULT_CONFIG, **store.get_config()}
    _require_hdhomerun_enabled(config)
    base_url = request.host_url.rstrip("/")
    channels = _hdhomerun_channels(config, base_url)
    if channel_index < 0 or channel_index >= len(channels):
        abort(404)
    channel = channels[channel_index]
    stream_url = str(channel.get("stream_url") or "").strip()
    local_playlist = _hdhomerun_local_playlist_for_channel(channel)
    if local_playlist is None and not _is_hdhomerun_stream_url_allowed(stream_url):
        abort(404)

    tuner_source = "source-rebroadcast"
    if local_playlist is not None:
        tuner_source = "hls-local"

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
        "X-RSMC-Tuner-Source": tuner_source,
    }
    if request.method == "HEAD":
        return Response(status=200, mimetype="video/mp2t", headers=headers)

    tuner_input = stream_url
    if local_playlist is not None:
        if channel.get("id") == VIRTUAL_CHANNEL_MIX_ID:
            _refresh_channel_mix_local_playlist()
        if not local_playlist.is_file():
            manager.logger.warning("hdhomerun", f"Local tuner playlist is not ready: {local_playlist}")
            abort(503)
        tuner_input = str(local_playlist)

    return Response(
        stream_with_context(_hdhomerun_mpegts_chunks(tuner_input, local_hls=(local_playlist is not None))),
        mimetype="video/mp2t",
        headers=headers,
        direct_passthrough=True,
    )


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
    channel_el = ET.SubElement(tv, "channel", {"id": VIRTUAL_GUIDE_CHANNEL_ID})
    ET.SubElement(channel_el, "display-name").text = channel_name

    for i in range(total_slots):
        slot_end = slot_start + timedelta(hours=4)
        prog = ET.SubElement(
            tv,
            "programme",
            {
                "start": slot_start.strftime("%Y%m%d%H%M%S +0000"),
                "stop": slot_end.strftime("%Y%m%d%H%M%S +0000"),
                "channel": VIRTUAL_GUIDE_CHANNEL_ID,
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
        logo_url = str(channel.get("logo_url") or "").strip()
        if logo_url:
            ET.SubElement(channel_el, "icon", {"src": logo_url})

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
    content = _build_channels_xmltv_content(_build_exported_virtual_channel_entries(config, request.host_url.rstrip("/")))
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



@app.get("/hls/traffic.m3u8")
def hls_traffic_playlist():
    """Simulated Traffic virtual-channel HLS entrypoint."""
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
    if (
        traffic_manager is not None
        and traffic_manager.status()["pipeline_active"]
        and traffic_manager.is_traffic_buffered()
        and TRAFFIC_PLAYLIST.exists()
    ):
        try:
            playlist_text = TRAFFIC_PLAYLIST.read_text(encoding="utf-8")
            lines = [line for line in playlist_text.splitlines() if not line.startswith("#EXT-X-PROGRAM-DATE-TIME:")]
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
    off_air_now = _is_off_air(cfg)
    segment = _resolve_standby_segment(cfg, off_air_now=off_air_now)
    if not segment.exists():
        abort(404)
    return _make_standby_playlist_response(diag, cfg, off_air_now=off_air_now)


@app.get("/hls/news.m3u8")
def hls_news_playlist():
    """News Now virtual-channel HLS entrypoint."""
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
    if news_manager is not None and news_manager.status()["pipeline_active"] and news_manager.is_news_buffered() and NEWS_PLAYLIST.exists():
        try:
            text = NEWS_PLAYLIST.read_text(encoding="utf-8")
            text = "\n".join(line for line in text.splitlines() if not line.startswith("#EXT-X-PROGRAM-DATE-TIME:")) + "\n"
        except OSError:
            abort(404)
        response = Response(text, mimetype="application/vnd.apple.mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    off_air_now = _is_off_air(cfg)
    segment = _resolve_standby_segment(cfg, off_air_now=off_air_now)
    if not segment.exists(): abort(404)
    return _make_standby_playlist_response(diag, cfg, off_air_now=off_air_now)


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

def _serve_virtual_logo(logo_dir: Path, filename: str):
    safe_name = secure_filename(filename)
    if not safe_name:
        abort(404)
    response = send_from_directory(logo_dir, safe_name)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/traffic-logo/<path:filename>")
def traffic_logo_file(filename: str):
    return _serve_virtual_logo(TRAFFIC_LOGO_DIR, filename)


@app.get("/news-logo/<path:filename>")
def news_logo_file(filename: str):
    return _serve_virtual_logo(NEWS_LOGO_DIR, filename)


@app.get("/channel-mix-logo/<path:filename>")
def channel_mix_logo_file(filename: str):
    return _serve_virtual_logo(CHANNEL_MIX_LOGO_DIR, filename)


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
        with path.open("rb") as fh:
            header = fh.read(16)
    except OSError:
        return False
    for offset, magic in _AUDIO_MAGIC:
        if header[offset: offset + len(magic)] == magic:
            return True
    return False


def _save_uploaded_audio_files(files, destination_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Save audio uploads into *destination_dir* with per-file validation.

    Flask's MAX_CONTENT_LENGTH applies to the entire multipart request, so the
    application enforces the advertised 100 MB limit here for each individual
    file instead.
    """
    saved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return saved, skipped, [f"Music library is not writable: {exc}"]

    for f in files:
        if not f.filename:
            continue
        original_name = f.filename
        name = secure_filename(original_name)
        if not name:
            errors.append(f"Could not use filename: {original_name}")
            continue
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            skipped.append(original_name)
            continue
        dest = destination_dir / name
        try:
            f.save(str(dest))
            size = dest.stat().st_size
            if size > MAX_MUSIC_FILE_BYTES:
                dest.unlink(missing_ok=True)
                errors.append(
                    f"Rejected {name}: {size / (1024 * 1024):.1f} MB exceeds the 100 MB per-file limit."
                )
                continue
            if size == 0:
                dest.unlink(missing_ok=True)
                errors.append(f"Rejected {name}: uploaded file is empty.")
                continue
            if not _is_audio_file(dest):
                dest.unlink(missing_ok=True)
                errors.append(f"Rejected {name}: file does not appear to be a valid audio file.")
                continue
            saved.append(name)
        except OSError as exc:
            dest.unlink(missing_ok=True)
            errors.append(f"Could not save {name}: {exc}")
    return saved, skipped, errors


def _schedule_guide_preview_aspect_detection(*, restart_when_detected: bool = False) -> None:
    """Detect Auto preview DAR in the background without delaying Guide startup."""
    cfg = store.get_config()
    if normalize_preview_aspect_mode(cfg.get("guide_preview_aspect_mode")) != "auto":
        return
    source_key = preview_source_cache_key(cfg)
    if not source_key or source_key in {"file:", "url:"}:
        return
    source = resolve_preview_source(cfg, BASE_DIR)
    if not source:
        return

    known = known_preview_aspect_ratio(cfg)

    def _worker() -> None:
        detected = known or detect_preview_aspect_ratio(source, timeout_seconds=3.0)
        latest = store.get_config()
        if normalize_preview_aspect_mode(latest.get("guide_preview_aspect_mode")) != "auto":
            return
        if preview_source_cache_key(latest) != source_key:
            return
        if detected not in {"16:9", "4:3"}:
            manager.logger.info("config", f"Guide preview Auto aspect detection unavailable for {source_key}; retaining 16:9 fallback")
            return
        old_effective = effective_preview_aspect_ratio(latest)
        store.save_config({
            **latest,
            "guide_preview_detected_aspect_ratio": detected,
            "guide_preview_detected_source_key": source_key,
        })
        manager.logger.info("config", f"Guide preview Auto aspect detected: {detected} ({source_key})")
        manager.refresh_state()
        if restart_when_detected and old_effective != detected and manager.status().get("pipeline_active"):
            manager.logger.info("config", "Restarting Guide pipeline once to apply detected preview aspect ratio")
            manager.restart_pipeline()

    threading.Thread(target=_worker, daemon=True, name="guide-preview-aspect-detect").start()


@app.post("/guide-preview/settings")
def guide_preview_settings():
    """Save Guide Channel preview-video settings."""
    cfg = store.get_config()
    enabled_values = request.form.getlist("guide_preview_enabled")
    enabled = any(_coerce_bool(value, False) for value in enabled_values)
    source_type = normalize_preview_source_type(request.form.get("guide_preview_source_type"))
    audio_mode = normalize_preview_audio_mode(request.form.get("guide_preview_audio_mode"))
    aspect_mode = normalize_preview_aspect_mode(request.form.get("guide_preview_aspect_mode"))
    message_enabled_values = request.form.getlist("guide_message_enabled")
    message_enabled = any(_coerce_bool(value, False) for value in message_enabled_values)
    message_text = str(request.form.get("guide_message_text", "") or "").replace("\r\n", "\n").replace("\r", "\n")
    # Bound stored text so malformed/accidental huge submissions cannot inflate guide_state.json.
    message_text = message_text[:12000]
    try:
        message_interval = max(3, min(60, int(request.form.get("guide_message_interval_seconds", 8))))
    except (TypeError, ValueError):
        message_interval = 8
    preview_url = str(request.form.get("guide_preview_url", "") or "").strip()
    if preview_url and not is_http_url(preview_url):
        flash("Preview URL must use http:// or https://.", "error")
        return redirect(url_for("index") + "#tab-guide-preview")

    preview_channel_url = str(request.form.get("guide_preview_url_channel", "") or "").strip()
    preview_channel_name = str(request.form.get("guide_preview_url_channel_name", "") or "").strip()
    if preview_channel_url and not is_http_url(preview_channel_url):
        flash("Selected preview channel URL must use http:// or https://.", "error")
        return redirect(url_for("index") + "#tab-guide-preview")

    proposed_source = {
        **cfg,
        "guide_preview_source_type": source_type,
        "guide_preview_url": preview_url,
        "guide_preview_url_channel": preview_channel_url if source_type == "url" else cfg.get("guide_preview_url_channel", ""),
        "guide_preview_url_channel_name": preview_channel_name if source_type == "url" else cfg.get("guide_preview_url_channel_name", ""),
    }
    source_changed = preview_source_cache_key(proposed_source) != preview_source_cache_key(cfg)
    cached_ratio = cfg.get("guide_preview_detected_aspect_ratio", "")
    cached_source_key = cfg.get("guide_preview_detected_source_key", "")
    if source_changed:
        cached_ratio = ""
        cached_source_key = ""
    # RSMC already knows the render aspect of Weather/Traffic/News virtual
    # channels, so Auto can use it immediately without spawning ffprobe.
    known_ratio = known_preview_aspect_ratio(proposed_source) if aspect_mode == "auto" else None
    if known_ratio in {"16:9", "4:3"}:
        cached_ratio = known_ratio
        cached_source_key = preview_source_cache_key(proposed_source)

    update = {
        **cfg,
        "guide_preview_enabled": enabled,
        "guide_preview_source_type": source_type,
        "guide_preview_url": preview_url,
        "guide_preview_url_channel": preview_channel_url if source_type == "url" else cfg.get("guide_preview_url_channel", ""),
        "guide_preview_url_channel_name": preview_channel_name if source_type == "url" else cfg.get("guide_preview_url_channel_name", ""),
        "guide_preview_audio_mode": audio_mode,
        "guide_preview_aspect_mode": aspect_mode,
        "guide_preview_detected_aspect_ratio": cached_ratio,
        "guide_preview_detected_source_key": cached_source_key,
        "guide_message_enabled": message_enabled,
        "guide_message_text": message_text,
        "guide_message_interval_seconds": message_interval,
    }
    store.save_config(update)
    manager.logger.info(
        "config",
        f"Guide preview settings updated: enabled={enabled}, source_type={source_type}, audio={audio_mode}, aspect={aspect_mode}, message_enabled={message_enabled}, message_interval={message_interval}s",
    )

    action = request.form.get("action", "save")
    if action == "restart":
        manager.refresh_state()
        if manager.status()["pipeline_active"]:
            manager.restart_pipeline()
            flash("Guide preview settings saved and pipeline restarted.", "success")
        else:
            manager.start_pipeline(message="Guide is Starting...")
            flash("Guide preview settings saved and guide started.", "success")
    else:
        manager.refresh_state()
        if manager.status()["pipeline_active"]:
            flash("Guide preview settings saved. Restart the pipeline to apply video/audio source changes.", "success")
        else:
            flash("Guide preview settings saved.", "success")
    _schedule_guide_preview_aspect_detection(restart_when_detected=(action == "restart"))
    return redirect(url_for("index") + "#tab-guide-preview")


@app.post("/guide-message/settings")
def guide_message_settings():
    """Save Guide Message settings and apply them to a running Guide live."""
    cfg = store.get_config()
    enabled_values = request.form.getlist("guide_message_enabled")
    message_enabled = any(_coerce_bool(value, False) for value in enabled_values)
    message_text = str(request.form.get("guide_message_text", "") or "").replace("\r\n", "\n").replace("\r", "\n")
    message_text = message_text[:12000]
    try:
        message_interval = max(3, min(60, int(request.form.get("guide_message_interval_seconds", 8))))
    except (TypeError, ValueError):
        message_interval = 8

    store.save_config({
        **cfg,
        "guide_message_enabled": message_enabled,
        "guide_message_text": message_text,
        "guide_message_interval_seconds": message_interval,
    })

    live_applied = patch_display_state({
        "guide_message_enabled": message_enabled,
        "guide_message_text": message_text,
        "guide_message_interval_seconds": message_interval,
    })
    manager.logger.info(
        "config",
        f"Guide Message updated live: enabled={message_enabled}, interval={message_interval}s, state_patched={live_applied}",
    )

    # If no state file exists yet, build one so the settings are ready for the
    # next start.  Do not restart an already-running Guide pipeline.
    if not live_applied and not manager.status().get("pipeline_active"):
        try:
            manager.refresh_state()
        except Exception as exc:
            manager.logger.warning("config", f"Guide Message saved but Guide state rebuild was unavailable: {exc}")

    if live_applied and manager.status().get("pipeline_active"):
        flash("Guide Message saved and updated live. No Guide restart required.", "success")
    else:
        flash("Guide Message saved. It will be used when the Guide is running.", "success")
    return redirect(url_for("index") + "#tab-guide-preview")


@app.post("/guide-preview/url-channels")
def guide_preview_url_channels():
    """Fetch an M3U URL and return selectable channels for Guide Preview."""
    payload = request.get_json(silent=True) or {}
    playlist_url = str(payload.get("url", "") or "").strip()
    if not is_http_url(playlist_url):
        return jsonify({"ok": False, "error": "URL must use http:// or https://."}), 400

    try:
        response = _requests.get(
            playlist_url,
            timeout=(5, 15),
            headers={"User-Agent": "RetroStation-MC/1.4 GuidePreview"},
            stream=True,
        )
        response.raise_for_status()
        # Cap the response while reading it so a malformed/huge URL cannot be
        # fully buffered in memory before the size limit is checked.
        max_bytes = 8 * 1024 * 1024
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_bytes:
                response.close()
                return jsonify({"ok": False, "error": "Playlist is larger than the 8 MB Guide Preview limit."}), 413
        text = bytes(content).decode(response.encoding or "utf-8-sig", errors="replace")
        channels = parse_preview_m3u(text, playlist_url)
    except _requests.RequestException as exc:
        manager.logger.warning("config", f"Guide preview playlist fetch failed for {playlist_url}: {exc}")
        return jsonify({"ok": False, "error": f"Could not load playlist: {exc}"}), 502

    if not channels:
        return jsonify({
            "ok": True,
            "playlist": False,
            "channels": [],
            "message": "No M3U channel entries were found. This URL can still be used as a direct stream URL.",
        })

    return jsonify({"ok": True, "playlist": True, "channels": channels, "count": len(channels)})


@app.post("/guide-preview/upload")
def guide_preview_upload():
    """Upload and select a local Guide Channel preview video."""
    file = request.files.get("guide_preview_file")
    if file is None or not file.filename:
        flash("No preview video selected.", "error")
        return redirect(url_for("index") + "#tab-guide-preview")

    filename = secure_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if not filename or ext not in ALLOWED_GUIDE_PREVIEW_EXTENSIONS:
        flash(
            "Unsupported preview video format. Allowed: " + ", ".join(sorted(ALLOWED_GUIDE_PREVIEW_EXTENSIONS)),
            "error",
        )
        return redirect(url_for("index") + "#tab-guide-preview")

    dest = GUIDE_PREVIEW_DIR / filename
    tmp = GUIDE_PREVIEW_DIR / f".{filename}.upload"
    try:
        file.save(tmp)
        if tmp.stat().st_size > MAX_GUIDE_PREVIEW_BYTES:
            tmp.unlink(missing_ok=True)
            flash("Preview video is too large (1 GB maximum).", "error")
            return redirect(url_for("index") + "#tab-guide-preview")
        tmp.replace(dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        flash(f"Could not save preview video: {exc}", "error")
        return redirect(url_for("index") + "#tab-guide-preview")

    cfg = store.get_config()
    old_name = secure_filename(str(cfg.get("guide_preview_file", "") or ""))
    store.save_config({
        **cfg,
        "guide_preview_file": filename,
        "guide_preview_source_type": "file",
        "guide_preview_detected_aspect_ratio": "",
        "guide_preview_detected_source_key": "",
    })
    if old_name and old_name != filename:
        try:
            (GUIDE_PREVIEW_DIR / old_name).unlink(missing_ok=True)
        except OSError:
            pass
    manager.logger.info("config", f"Guide preview video uploaded: {filename}")
    _schedule_guide_preview_aspect_detection(restart_when_detected=False)
    flash(f"Preview video uploaded and selected: {filename}. Restart the pipeline to apply.", "success")
    return redirect(url_for("index") + "#tab-guide-preview")


@app.post("/guide-preview/remove")
def guide_preview_remove():
    """Remove the selected local Guide Channel preview video."""
    cfg = store.get_config()
    filename = secure_filename(str(cfg.get("guide_preview_file", "") or ""))
    if filename:
        try:
            (GUIDE_PREVIEW_DIR / filename).unlink(missing_ok=True)
        except OSError as exc:
            flash(f"Could not remove preview video: {exc}", "error")
            return redirect(url_for("index") + "#tab-guide-preview")
    store.save_config({
        **cfg,
        "guide_preview_file": "",
        "guide_preview_detected_aspect_ratio": "",
        "guide_preview_detected_source_key": "",
    })
    flash("Local preview video removed. Restart the pipeline to apply.", "success")
    return redirect(url_for("index") + "#tab-guide-preview")


@app.post("/music/upload")
def music_upload():
    """Upload one or more audio files to the music library."""
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        flash("No files selected.", "error")
        return redirect(url_for("index") + "#tab-music")

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
    return redirect(url_for("index") + "#tab-music")


@app.post("/virtual-channels/weather/music/upload")
def weather_music_upload():
    """Upload one or more audio files for Weather Channel background music."""
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        flash("No files selected.", "error")
        return redirect(url_for("virtual_channels_page"))

    saved, skipped, errors = _save_uploaded_audio_files(files, MUSIC_DIR)
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
        return redirect(url_for("index") + "#tab-music")
    try:
        dest.unlink()
        # Remove the file from every channel selection in the shared library.
        cfg = store.get_config()
        changed = False
        for prefix in ("", "weather_", "traffic_", "news_"):
            single_key = f"{prefix}music_single_file"
            list_key = f"{prefix}music_playlist_files"
            if cfg.get(single_key) == safe_name:
                cfg[single_key] = ""
                changed = True
            pl = cfg.get(list_key, []) or []
            if safe_name in pl:
                cfg[list_key] = [x for x in pl if x != safe_name]
                changed = True
        if changed:
            store.save_config(cfg)
        flash(f"Deleted: {safe_name}", "success")
    except OSError as exc:
        flash(f"Could not delete {safe_name}: {exc}", "error")
    return redirect(url_for("index") + "#tab-music")


@app.post("/virtual-channels/weather/music/delete/<filename>")
def weather_music_delete(filename: str):
    """Delete an uploaded Weather Channel music file."""
    safe_name = secure_filename(filename)
    dest = MUSIC_DIR / safe_name
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
    if music_mode not in ("none", "single", "playlist", "all"):
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

    return redirect(url_for("index") + "#tab-music")


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
        "aspect_ratio":           _normalize_aspect_ratio(cfg.get("weather_aspect_ratio",
                                                                   DEFAULT_CONFIG["weather_aspect_ratio"])),
        "resolution":             cfg.get("weather_resolution", DEFAULT_CONFIG["weather_resolution"]),
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


# ─── Traffic Channel helpers ─────────────────────────────────────────────────

def _get_traffic_config() -> dict:
    """Return simulated traffic channel configuration from the config store."""
    cfg = store.get_config()
    return {
        "enabled":          _coerce_bool(cfg.get("traffic_channel_enabled"),
                                         DEFAULT_CONFIG["traffic_channel_enabled"]),
        "aspect_ratio":     _normalize_aspect_ratio(cfg.get("traffic_aspect_ratio",
                                                              DEFAULT_CONFIG["traffic_aspect_ratio"])),
        "resolution":       cfg.get("traffic_resolution", DEFAULT_CONFIG["traffic_resolution"]),
        "rotation_mode":    str(cfg.get("traffic_rotation_mode",
                                        DEFAULT_CONFIG["traffic_rotation_mode"])),
        "rotation_seconds": int(cfg.get("traffic_rotation_seconds",
                                        DEFAULT_CONFIG["traffic_rotation_seconds"]) or 120),
        "pack_size":        int(cfg.get("traffic_pack_size",
                                        DEFAULT_CONFIG["traffic_pack_size"]) or 10),
        "pack":             cfg.get("traffic_pack", DEFAULT_CONFIG["traffic_pack"]) or [],
        **_music_view(cfg, "traffic_"),
    }


def _save_traffic_config(cfg: dict) -> None:
    """Validate and persist traffic channel configuration."""
    allowed_modes = ("admin_rotation", "random_pack")
    mode = str(cfg.get("rotation_mode", "admin_rotation"))
    if mode not in allowed_modes:
        raise ValueError(f"Invalid rotation_mode: {mode!r}.")
    try:
        rotation_seconds = int(cfg.get("rotation_seconds", 120))
        if not (30 <= rotation_seconds <= 3600):
            raise ValueError("rotation_seconds must be between 30 and 3600.")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid rotation_seconds: {exc}") from exc
    try:
        pack_size = int(cfg.get("pack_size", 10))
        if not (1 <= pack_size <= 50):
            raise ValueError("pack_size must be between 1 and 50.")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid pack_size: {exc}") from exc

    existing = store.get_config()
    existing.update(
        {
            "traffic_channel_enabled":  bool(cfg.get("enabled", False)),
            "traffic_aspect_ratio":      _normalize_aspect_ratio(cfg.get("aspect_ratio")),
            "traffic_resolution":        str(cfg.get("resolution", DEFAULT_CONFIG["traffic_resolution"])),
            "traffic_rotation_mode":    mode,
            "traffic_rotation_seconds": rotation_seconds,
            "traffic_pack_size":        pack_size,
        }
    )
    store.save_config(existing)


def _get_traffic_cities() -> list[dict]:
    """Return the current city list, seeding from defaults if not yet configured."""
    cfg    = store.get_config()
    cities = cfg.get("traffic_cities", []) or []
    if not cities:
        cities = _traffic_seed_cities()
        existing = store.get_config()
        existing["traffic_cities"] = cities
        store.save_config(existing)
    return cities


def _save_traffic_cities(cities: list[dict]) -> None:
    """Persist the city list to the config store."""
    existing = store.get_config()
    existing["traffic_cities"] = cities
    store.save_config(existing)


# ─── News Now RSS/Atom Channel ───────────────────────────────────────────────

def _get_news_feed_urls() -> list[str]:
    raw = store.get_config().get("news_feed_urls", DEFAULT_CONFIG["news_feed_urls"])
    if not isinstance(raw, list):
        # backward-compatible single-feed config if encountered during development
        raw = [raw] if raw else []
    try:
        return validate_feed_urls(raw)
    except ValueError:
        return []

def get_news_feed_urls() -> list[str]:
    return _get_news_feed_urls()

def save_news_feed_urls(urls) -> None:
    store.save_config({"news_feed_urls": validate_feed_urls(urls)})

def get_news_feed_url() -> str:
    urls = get_news_feed_urls(); return urls[0] if urls else ""

def save_news_feed_url(url) -> None:
    save_news_feed_urls([url])

def _get_news_config() -> dict:
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    return {"enabled": bool(cfg.get("news_channel_enabled")), "aspect_ratio": cfg.get("news_aspect_ratio","16:9"), "resolution": cfg.get("news_resolution","1280x720"), "feed_urls": _get_news_feed_urls(), **_music_view(cfg, "news_")}

def _build_news_state() -> dict:
    return build_news_payload(_get_news_feed_urls())

@app.get("/api/news")
def api_news():
    return jsonify(_build_news_state())

@app.get("/news")
def news_page():
    return render_template("news.html")

@app.post("/virtual-channels/news/config")
def virtual_channels_news_config():
    try:
        enabled = "1" in request.form.getlist("news_channel_enabled")
        aspect = _normalize_aspect_ratio(request.form.get("news_aspect_ratio"))
        resolution = request.form.get("news_resolution", DEFAULT_CONFIG["news_resolution"]).strip()
        if resolution not in {"1280x720","1920x1080","960x720","1440x1080"}: resolution = DEFAULT_CONFIG["news_resolution"]
        feeds = [request.form.get(f"news_feed_url_{i}", "").strip() for i in range(1,7)]
        feeds = validate_feed_urls(feeds)
        music_cfg = _normalize_music_selection("news_", request.form)
        store.save_config({"news_channel_enabled": enabled, "news_aspect_ratio": aspect, "news_resolution": resolution, "news_feed_urls": feeds, **music_cfg})
        flash("News Now settings saved.", "success")
        if news_manager is not None:
            if enabled: news_manager.start_pipeline()
            else: news_manager._stop_pipeline()
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash(f"Could not save News Now settings: {exc}", "error")
    return redirect(url_for("virtual_channels_page"))


# ─── Channel Mix ──────────────────────────────────────────────────────────────

def _channel_mix_registry(config: dict) -> dict[str, dict]:
    """Common registry of RSMC-owned channels eligible for Channel Mix."""
    return {
        VIRTUAL_GUIDE_CHANNEL_ID: {
            "name": _sanitize_xmltv_text(config.get("title"), "Channel Guide"),
            "playlist": "guide.m3u8", "enabled": True, "buffered": manager.is_guide_buffered,
        },
        VIRTUAL_WEATHER_CHANNEL_ID: {
            "name": _weather_channel_display_name(config), "playlist": "weather.m3u8",
            "enabled": _coerce_bool(config.get("weather_channel_enabled"), False),
            "buffered": lambda: bool(weather_manager and weather_manager.is_weather_buffered()),
        },
        VIRTUAL_TRAFFIC_CHANNEL_ID: {
            "name": "Simulated Traffic", "playlist": "traffic.m3u8",
            "enabled": _coerce_bool(config.get("traffic_channel_enabled"), False),
            "buffered": lambda: bool(traffic_manager and traffic_manager.is_traffic_buffered()),
        },
        VIRTUAL_NEWS_CHANNEL_ID: {
            "name": "News Now", "playlist": "news.m3u8",
            "enabled": _coerce_bool(config.get("news_channel_enabled"), False),
            "buffered": lambda: bool(news_manager and news_manager.is_news_buffered()),
        },
    }


def _get_channel_mix_config(config: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or store.get_config())}
    registry = _channel_mix_registry(cfg)
    raw = cfg.get("channel_mix_channels") or []
    # Stored invalid/legacy entries are filtered on read, preserving the RIG behavior.
    safe = []
    for entry in raw:
        cid = str(entry.get("channel_id", entry.get("tvg_id", ""))).strip()
        if cid not in registry:
            continue
        try: mins = max(1, min(1440, int(entry.get("duration_minutes", 120))))
        except (TypeError, ValueError): mins = 120
        safe.append({"channel_id": cid, "duration_minutes": mins})
    return {"name": str(cfg.get("channel_mix_name") or "Channel Mix").strip() or "Channel Mix", "channels": safe}


def _channel_mix_status(config: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or store.get_config())}
    registry = _channel_mix_registry(cfg)
    mix = _get_channel_mix_config(cfg)
    available = []
    for cid, meta in registry.items():
        if not meta["enabled"]:
            continue
        try:
            if meta["buffered"](): available.append(cid)
        except Exception:
            continue
    active, remaining, scheduled = get_active_available_channel(mix["channels"], available)
    total = sum(int(c["duration_minutes"]) * 60 for c in mix["channels"])
    channels = [{**c, "name": registry.get(c["channel_id"], {}).get("name", c["channel_id"]),
                 "available": c["channel_id"] in available} for c in mix["channels"]]
    return {"name": mix["name"], "active_channel_id": active,
            "active_name": registry.get(active, {}).get("name") if active else None,
            "scheduled_channel_id": scheduled, "seconds_remaining": remaining,
            "total_cycle_seconds": total, "channels": channels}


@app.get("/api/channel_mix")
@app.get("/api/channel-mix")
def api_channel_mix():
    return jsonify(_channel_mix_status())


_channel_mix_hls_state = ChannelMixHLSState(OUTPUT_DIR, window_size=10)
_channel_mix_refresh_stop = threading.Event()
_channel_mix_refresh_thread: threading.Thread | None = None
_channel_mix_audio_popen: subprocess.Popen | None = None
_channel_mix_audio_signature: tuple | None = None


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _channel_mix_standby_text(cfg: dict) -> str | None:
    off_air_now = _is_off_air(cfg)
    segment = _resolve_standby_segment(cfg, off_air_now=off_air_now)
    if not segment.exists():
        return None
    duration = max(1, int(math.ceil(float(STANDBY_DURATION_SECS))))
    sequence = int(time.time()) // duration
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f"#EXT-X-TARGETDURATION:{duration}\n"
        f"#EXT-X-MEDIA-SEQUENCE:{sequence}\n"
        "#EXT-X-INDEPENDENT-SEGMENTS\n"
        f"#EXTINF:{float(STANDBY_DURATION_SECS):.6f},\n"
        f"{segment.name}\n"
    )


def _refresh_channel_mix_local_playlist() -> tuple[str | None, dict]:
    """Refresh the disk-backed CH 5 playlist and return ``(text, status)``.

    This is used both by the public HLS route and by one process-wide background
    refresher.  the Channel Mix audio-override pipeline reads ``output/channel-mix-source.m3u8`` directly,
    so CH 5 can switch sources without making recursive HTTP requests into Flask.
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    status = _channel_mix_status(cfg)
    active = status["active_channel_id"]
    registry = _channel_mix_registry(cfg)
    text: str | None = None
    meta = registry.get(active) if active else None
    if meta:
        path = OUTPUT_DIR / meta["playlist"]
        if path.exists():
            try:
                source_text = path.read_text(encoding="utf-8")
                text = _channel_mix_hls_state.build(active, source_text)
            except OSError:
                text = None
    if not text:
        text = _channel_mix_standby_text(cfg)
    if text:
        try:
            _atomic_write_text(OUTPUT_DIR / CHANNEL_MIX_SOURCE_PLAYLIST, text)
        except OSError as exc:
            manager.logger.warning("channel-mix", f"Unable to write local Channel Mix playlist: {_error_label(exc)}")
    return text, status


def _channel_mix_music_signature(cfg: dict) -> tuple:
    return (
        str(cfg.get("channel_mix_music_mode", "none")),
        bool(_coerce_bool(cfg.get("channel_mix_music_loop"), False)),
        str(cfg.get("channel_mix_music_single_file", "")),
        tuple(cfg.get("channel_mix_music_playlist_files", []) or []),
    )


def _stop_channel_mix_audio_pipeline() -> None:
    global _channel_mix_audio_popen, _channel_mix_audio_signature
    proc = _channel_mix_audio_popen
    _channel_mix_audio_popen = None
    _channel_mix_audio_signature = None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    # Public output is generated only by the override muxer.
    for path in OUTPUT_DIR.glob("channel_mix_audio_*.ts"):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        (OUTPUT_DIR / CHANNEL_MIX_LOCAL_PLAYLIST).unlink(missing_ok=True)
    except OSError:
        pass


def _build_channel_mix_audio_command(cfg: dict) -> list[str]:
    audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(
        cfg, "aac", key_prefix="channel_mix_music_", music_dir=MUSIC_DIR,
        playlist_filename="channel_mix_music_playlist.txt",
    )
    start_number = str(int(time.time()) // 6)
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        "-re", "-live_start_index", "-1", "-fflags", "+genpts",
        "-i", str(OUTPUT_DIR / CHANNEL_MIX_SOURCE_PLAYLIST),
        *audio_input_args,
        "-c:v", "copy", *audio_codec_args, *audio_map_args,
        "-f", "hls", "-hls_time", "6", "-hls_segment_type", "mpegts",
        "-hls_list_size", "10", "-segment_list_flags", "+live",
        "-hls_flags", "delete_segments+program_date_time+omit_endlist+discont_start+independent_segments",
        "-start_number", start_number,
        "-hls_segment_filename", str(OUTPUT_DIR / "channel_mix_audio_%d.ts"),
        str(OUTPUT_DIR / CHANNEL_MIX_LOCAL_PLAYLIST),
    ]


def _ensure_channel_mix_audio_pipeline(cfg: dict) -> None:
    global _channel_mix_audio_popen, _channel_mix_audio_signature
    if not _coerce_bool(cfg.get("channel_mix_enabled"), False):
        _stop_channel_mix_audio_pipeline()
        return
    if not (OUTPUT_DIR / CHANNEL_MIX_SOURCE_PLAYLIST).is_file():
        return
    signature = _channel_mix_music_signature(cfg)
    if (_channel_mix_audio_popen is not None and _channel_mix_audio_popen.poll() is None
            and _channel_mix_audio_signature == signature):
        return
    _stop_channel_mix_audio_pipeline()
    try:
        _channel_mix_audio_popen = subprocess.Popen(
            _build_channel_mix_audio_command(cfg),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _channel_mix_audio_signature = signature
        manager.logger.info("channel-mix", f"Channel Mix audio override started (PID {_channel_mix_audio_popen.pid})")
    except OSError as exc:
        manager.logger.error("channel-mix", f"Unable to start Channel Mix audio override: {_error_label(exc)}")
        _channel_mix_audio_popen = None
        _channel_mix_audio_signature = None


def _channel_mix_refresh_loop() -> None:
    while not _channel_mix_refresh_stop.wait(CHANNEL_MIX_REFRESH_SECONDS):
        try:
            cfg = {**DEFAULT_CONFIG, **store.get_config()}
            if _coerce_bool(cfg.get("channel_mix_enabled"), False):
                _refresh_channel_mix_local_playlist()
                _ensure_channel_mix_audio_pipeline(cfg)
            else:
                _stop_channel_mix_audio_pipeline()
        except Exception as exc:
            manager.logger.warning("channel-mix", f"Channel Mix refresh failed: {_error_label(exc)}")


def _start_channel_mix_refresher() -> None:
    global _channel_mix_refresh_thread
    if _channel_mix_refresh_thread is not None and _channel_mix_refresh_thread.is_alive():
        return
    _channel_mix_refresh_stop.clear()
    _channel_mix_refresh_thread = threading.Thread(
        target=_channel_mix_refresh_loop,
        daemon=True,
        name="channel-mix-refresh",
    )
    _channel_mix_refresh_thread.start()


def _stop_channel_mix_refresher() -> None:
    _channel_mix_refresh_stop.set()
    thread = _channel_mix_refresh_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    _stop_channel_mix_audio_pipeline()


@app.get("/hls/channel-mix.m3u8")
def hls_channel_mix_playlist():
    """Serve the same disk-backed continuous playlist used by HDHomeRun."""
    local_path = OUTPUT_DIR / CHANNEL_MIX_LOCAL_PLAYLIST
    status = _channel_mix_status()
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    _refresh_channel_mix_local_playlist()
    _ensure_channel_mix_audio_pipeline(cfg)
    text = None
    for _ in range(8):
        if local_path.is_file():
            try:
                text = local_path.read_text(encoding="utf-8")
                if "#EXTINF:" in text:
                    break
            except OSError:
                text = None
        time.sleep(0.1)
    if not text:
        abort(404)
    response = Response(text, mimetype="application/vnd.apple.mpegurl")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Access-Control-Allow-Origin"] = "*"
    if status.get("active_channel_id"):
        response.headers["X-RSMC-Channel-Mix-Source"] = str(status["active_channel_id"])
        response.headers["X-RSMC-Channel-Mix-Seconds-Remaining"] = str(status["seconds_remaining"])
    return response


@app.post("/virtual-channels/channel-mix/config")
def virtual_channels_channel_mix_config():
    try:
        enabled = "1" in request.form.getlist("channel_mix_enabled")
        name = request.form.get("channel_mix_name", "Channel Mix")
        order = [x.strip() for x in request.form.getlist("channel_mix_id") if x.strip()]
        durations = request.form.getlist("channel_mix_duration")
        entries = []
        for i, cid in enumerate(order):
            minutes = durations[i] if i < len(durations) else "120"
            entries.append({"channel_id": cid, "duration_minutes": minutes})
        registry = _channel_mix_registry({**DEFAULT_CONFIG, **store.get_config()})
        normalized = normalize_channel_mix_config(name, entries, registry.keys())
        music_cfg = _normalize_music_selection("channel_mix_", request.form)
        store.save_config({"channel_mix_enabled": enabled, "channel_mix_name": normalized["name"],
                           "channel_mix_channels": normalized["channels"], **music_cfg})
        _stop_channel_mix_audio_pipeline()
        if enabled:
            cfg = {**DEFAULT_CONFIG, **store.get_config()}
            _refresh_channel_mix_local_playlist()
            _ensure_channel_mix_audio_pipeline(cfg)
        flash("Channel Mix settings saved.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash(f"Could not save Channel Mix settings: {exc}", "error")
    return redirect(url_for("virtual_channels_page"))

# ─── Virtual Channels Admin Page ─────────────────────────────────────────────

@app.get("/virtual-channels")
def virtual_channels_page():
    """Admin page for virtual channels configuration."""
    wx_cfg = _get_weather_config()
    music_files = _list_audio_files(MUSIC_DIR)
    weather_music_files = music_files  # backwards-compatible template alias
    traffic_cfg   = _get_traffic_config()
    traffic_cities = _get_traffic_cities()
    news_cfg = _get_news_config()
    mix_cfg = _get_channel_mix_config()
    mix_cfg.update(_music_view({**DEFAULT_CONFIG, **store.get_config()}, "channel_mix_"))
    mix_registry = _channel_mix_registry({**DEFAULT_CONFIG, **store.get_config()})
    return render_template(
        "virtual_channels.html",
        weather=wx_cfg,
        weather_music_files=weather_music_files,
        music_files=music_files,
        traffic=traffic_cfg,
        traffic_cities=traffic_cities,
        news=news_cfg,
        channel_mix=mix_cfg,
        channel_mix_registry=mix_registry,
        channel_mix_enabled=_coerce_bool(store.get_config().get("channel_mix_enabled"), False),
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
        if weather_music_mode not in ("none", "single", "playlist", "all"):
            weather_music_mode = "none"
        weather_music_loop = request.form.get("weather_music_loop") == "1"
        weather_music_single_file = secure_filename(request.form.get("weather_music_single_file", "").strip())
        weather_music_playlist_files = [
            secure_filename(name)
            for name in request.form.getlist("weather_music_playlist_files")
            if name
        ]
        available_files = set(_list_audio_files(MUSIC_DIR))
        if weather_music_single_file not in available_files:
            weather_music_single_file = ""
        weather_music_playlist_files = [name for name in weather_music_playlist_files if name in available_files]

        weather_aspect_ratio = _normalize_aspect_ratio(request.form.get("weather_aspect_ratio"))
        weather_resolution = request.form.get("weather_resolution", DEFAULT_CONFIG["weather_resolution"]).strip()
        _VALID_RESOLUTIONS = {"1280x720", "1920x1080", "960x720", "1440x1080"}
        if weather_resolution not in _VALID_RESOLUTIONS:
            weather_resolution = DEFAULT_CONFIG["weather_resolution"]

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
                "weather_aspect_ratio": weather_aspect_ratio,
                "weather_resolution": weather_resolution,
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


# ─── Simulated Traffic Channel ────────────────────────────────────────────────

@app.post("/virtual-channels/traffic/config")
def virtual_channels_traffic_config():
    """Save Simulated Traffic Channel configuration."""
    try:
        enabled_vals = request.form.getlist("traffic_channel_enabled")
        enabled = "1" in enabled_vals

        rotation_mode = request.form.get("traffic_rotation_mode", "admin_rotation").strip()
        if rotation_mode not in ("admin_rotation", "random_pack"):
            rotation_mode = "admin_rotation"

        rotation_seconds_raw = request.form.get("traffic_rotation_seconds", "120").strip()
        try:
            rotation_seconds = max(30, min(3600, int(rotation_seconds_raw)))
        except (TypeError, ValueError):
            rotation_seconds = 120

        pack_size_raw = request.form.get("traffic_pack_size", "10").strip()
        try:
            pack_size = max(1, min(50, int(pack_size_raw)))
        except (TypeError, ValueError):
            pack_size = 10

        traffic_aspect_ratio = _normalize_aspect_ratio(request.form.get("traffic_aspect_ratio"))
        traffic_resolution = request.form.get("traffic_resolution", DEFAULT_CONFIG["traffic_resolution"]).strip()
        _VALID_RESOLUTIONS = {"1280x720", "1920x1080", "960x720", "1440x1080"}
        if traffic_resolution not in _VALID_RESOLUTIONS:
            traffic_resolution = DEFAULT_CONFIG["traffic_resolution"]

        _save_traffic_config(
            {
                "enabled":          enabled,
                "aspect_ratio":     traffic_aspect_ratio,
                "resolution":       traffic_resolution,
                "rotation_mode":    rotation_mode,
                "rotation_seconds": rotation_seconds,
                "pack_size":        pack_size,
            }
        )
        store.save_config(_normalize_music_selection("traffic_", request.form))
        flash("Simulated Traffic Channel settings saved.", "success")
        if traffic_manager is not None:
            if enabled:
                traffic_manager.start_pipeline()
            else:
                traffic_manager._stop_pipeline()  # noqa: SLF001
    except ValueError as exc:
        flash(f"Invalid traffic settings: {exc}", "error")
    except Exception as exc:
        flash(f"Could not save traffic settings: {exc}", "error")
    return redirect(url_for("virtual_channels_page"))


@app.post("/api/traffic/cities/<int:city_id>")
def api_traffic_city_save(city_id: int):
    """Update enabled flag and weight for a single city."""
    data = request.get_json(silent=True) or {}
    try:
        cities = _get_traffic_cities()
        city   = next((c for c in cities if c["id"] == city_id), None)
        if city is None:
            return jsonify({"ok": False, "error": "City not found"}), 404
        city["enabled"] = bool(data.get("enabled", city["enabled"]))
        city["weight"]  = max(1, int(data.get("weight", city.get("weight", 1))))
        _save_traffic_cities(cities)
        return jsonify({"ok": True})
    except Exception as exc:
        logging.exception("api_traffic_city_save failed for id=%s", city_id)
        return jsonify({"ok": False, "error": "Internal error updating city"}), 500


@app.post("/api/traffic/cities/enable-all")
def api_traffic_cities_enable_all():
    """Enable all cities at once."""
    try:
        cities = _get_traffic_cities()
        for c in cities:
            c["enabled"] = True
        _save_traffic_cities(cities)
        return jsonify({"ok": True})
    except Exception as exc:
        logging.exception("api_traffic_cities_enable_all failed")
        return jsonify({"ok": False, "error": "Internal error enabling cities"}), 500


@app.post("/api/traffic/cities/disable-all")
def api_traffic_cities_disable_all():
    """Disable all cities at once."""
    try:
        cities = _get_traffic_cities()
        for c in cities:
            c["enabled"] = False
        _save_traffic_cities(cities)
        return jsonify({"ok": True})
    except Exception as exc:
        logging.exception("api_traffic_cities_disable_all failed")
        return jsonify({"ok": False, "error": "Internal error disabling cities"}), 500


@app.get("/traffic")
def traffic_page():
    """Simulated Traffic Channel display page.

    All congestion levels and incidents shown are synthetically generated
    for retro TV presentation purposes only.  This page must never be
    presented as a source of real traffic information.
    """
    return render_template("traffic.html", disclaimer=TRAFFIC_DISCLAIMER)


@app.get("/api/traffic")
def api_traffic():
    """Simulated traffic overlay data endpoint.

    Returns a payload whose ``demo_mode`` field is always ``True``.
    All congestion data and incidents are synthetic; the ``disclaimer``
    field in the response makes this explicit.
    """
    try:
        cities = _get_traffic_cities()
        cfg    = _get_traffic_config()
        payload = _build_traffic_payload(cities, cfg)
        return jsonify(payload)
    except Exception as exc:
        logging.exception("api_traffic failed: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.get("/api/traffic/roads/<int:city_id>")
def api_traffic_roads(city_id: int):
    """Return road GeoJSON for a city (cached; no live API calls on cache hit).

    Road geometry is from OpenStreetMap.  Only the congestion colours
    overlaid on the roads are simulated.
    """
    try:
        cities = _get_traffic_cities()
        city   = next((c for c in cities if c["id"] == city_id), None)
        if city is None:
            return jsonify({"error": "City not found"}), 404
        geojson = _get_traffic_road_geojson(city)
        resp    = jsonify(geojson)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp
    except Exception as exc:
        logging.exception("api_traffic_roads failed for city_id=%s", city_id)
        return jsonify({"error": "Internal server error"}), 500


@app.get("/traffic-map/<path:filename>")
def traffic_basemap_file(filename: str):
    """Serve a static basemap PNG from the local cache directory.

    Basemaps are stitched from OSM tiles and stored in
    ``data/maps/traffic/<cityslug>.png``.  The route provides the
    traffic.html template with a stable URL for each city basemap.
    """
    safe_name = posixpath.basename(filename)
    if not safe_name.endswith(".png") or "/" in filename:
        abort(404)
    return send_from_directory(str(TRAFFIC_BASEMAP_DIR), safe_name)


# Native video/HLS pipeline for Traffic. Keep /traffic as the browser preview;
# IPTV clients tune /hls/traffic.m3u8 instead.
def _get_traffic_data_for_renderer() -> dict | None:
    """Build renderer state including the actual cached OSM map and road geometry.

    The browser preview can fetch these pieces independently, but the HLS renderer
    runs as a separate process.  Embed the road FeatureCollection and the local
    basemap path in its state file so the broadcast channel renders the same
    real-road map rather than a schematic placeholder.
    """
    try:
        payload = _build_traffic_payload(_get_traffic_cities(), _get_traffic_config())
        if not payload or payload.get("no_cities"):
            return payload
        city = payload.get("city") or {}
        if city.get("name") and city.get("lat") is not None and city.get("lon") is not None:
            basemap = _ensure_traffic_basemap(city["name"], float(city["lat"]), float(city["lon"]))
            payload["basemap_path"] = str(basemap) if basemap else ""
            roads = _get_traffic_road_geojson(city)
            payload["roads"] = roads if isinstance(roads, dict) else {"type": "FeatureCollection", "features": []}
        return payload
    except Exception:
        logging.exception("_get_traffic_data_for_renderer failed")
        return None


traffic_manager = TrafficChannelManager(store, data_fetcher=_get_traffic_data_for_renderer)
traffic_manager.start()
atexit.register(traffic_manager.stop)
news_manager = NewsChannelManager(store, data_fetcher=_build_news_state)
news_manager.start()
atexit.register(news_manager.stop)
_start_channel_mix_refresher()
atexit.register(_stop_channel_mix_refresher)


if __name__ == "__main__":
    host = __import__("os").environ.get("RETROGUIDE_HOST", "0.0.0.0")
    port = int(__import__("os").environ.get("RETROGUIDE_PORT", "8787"))
    discovery_service = HDHomeRunDiscoveryService(
        enabled_getter=_hdhomerun_discovery_enabled,
        device_id_getter=_hdhomerun_discovery_device_id,
        tuner_count=HDHOMERUN_TUNER_COUNT,
        http_port=port,
    )
    discovery_service.start()
    atexit.register(discovery_service.stop)
    try:
        app.run(host=host, port=port, debug=False)
    finally:
        discovery_service.stop()
