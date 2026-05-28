from __future__ import annotations

import atexit
import csv
import io
import json
import traceback
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, send_from_directory, url_for
from flask import request
from werkzeug.utils import secure_filename

from app.config_store import ConfigStore, DEFAULT_CONFIG
from app.hls_playlist import trim_playlist_for_delayed_live_edge
from app.manager import GuideManager, STANDBY_SEGMENT, STANDBY_DURATION_SECS, MUSIC_DIR

BASE_DIR = Path(__file__).resolve().parent
THEMES_DIR = BASE_DIR / "app" / "themes"
OUTPUT_DIR = BASE_DIR / "output"
GUIDE_LOGO_DIR = BASE_DIR / "data" / "guide_logo"
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

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.secret_key = "retro-guide-poc-local-only"
app.config["SESSION_COOKIE_NAME"] = "retro_guide_session"
app.config["MAX_CONTENT_LENGTH"] = MAX_MUSIC_FILE_BYTES

store = ConfigStore()
manager = GuideManager(store)
manager.start()
atexit.register(manager.stop)


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


def coerce_form(form) -> dict:
    cfg = {
        "playlist_source": form.get("playlist_source", DEFAULT_CONFIG["playlist_source"]).strip(),
        "xmltv_source": form.get("xmltv_source", DEFAULT_CONFIG["xmltv_source"]).strip(),
        "theme": form.get("theme", DEFAULT_CONFIG["theme"]).strip(),
        "title": form.get("title", DEFAULT_CONFIG["title"]).strip(),
        "resolution": form.get("resolution", DEFAULT_CONFIG["resolution"]).strip(),
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
    }
    return cfg


def _coerce_int(value, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


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
    music_files = sorted(
        p.name for p in MUSIC_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_AUDIO_EXTENSIONS
    )
    return render_template(
        "index.html",
        config=config,
        status=manager.status(),
        events=events,
        events_total=store.count_events(),
        themes=themes,
        theme_labels=theme_labels,
        theme_data_all=theme_data_all,
        music_files=music_files,
        guide_logo_custom_file=logo_filename if logo_path and logo_path.is_file() else "",
        guide_logo_custom_url=url_for("guide_logo_file", filename=logo_filename) if logo_path and logo_path.is_file() else "",
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


@app.get("/channel.m3u")
def channel_playlist():
    config = store.get_config()
    raw_name = config.get("title", "Channel Guide") or "Channel Guide"
    # Strip newlines and escape double-quotes to keep the M3U line well-formed.
    channel_name = raw_name.replace("\r", "").replace("\n", "").replace('"', '\\"')
    base_url = request.host_url.rstrip("/")
    stream_url = base_url + "/hls/master.m3u8"
    xmltv_url = base_url + "/channel.xmltv"
    logo_url = _channel_logo_url(config, base_url)
    content = _build_channel_m3u_content(channel_name, stream_url, xmltv_url, logo_url)
    resp = Response(content, mimetype="application/x-mpegURL")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/channel.m3u8")
def channel_playlist_m3u8():
    config = store.get_config()
    raw_name = config.get("title", "Channel Guide") or "Channel Guide"
    channel_name = raw_name.replace("\r", "").replace("\n", "").replace('"', '\\"')
    base_url = request.host_url.rstrip("/")
    stream_url = base_url + "/hls/master.m3u8"
    xmltv_url = base_url + "/channel.xmltv"
    logo_url = _channel_logo_url(config, base_url)
    content = _build_channel_m3u_content(channel_name, stream_url, xmltv_url, logo_url)
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


@app.get("/channel.xmltv")
def channel_xmltv():
    config = store.get_config()
    raw_name = config.get("title", "Channel Guide") or "Channel Guide"
    channel_name = raw_name.replace("\r", "").replace("\n", "")
    content = _build_xmltv_content(channel_name)
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
    if st.get("guide_buffered"):
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
    if not STANDBY_SEGMENT.exists():
        abort(404)
    return _make_standby_playlist_response(diag)


def _make_standby_playlist_response(diag: dict) -> Response:
    """Build and return a synthetic standby media playlist response.

    Extracted so both ``/hls/guide.m3u8`` (backward compat) and the dedicated
    ``/hls/standby.m3u8`` endpoint can share the same logic without duplication.
    """
    _sdur = STANDBY_DURATION_SECS
    try:
        _live_seg_secs = max(1, int(store.get_config().get("segment_seconds", 6)))
    except (TypeError, ValueError):
        _live_seg_secs = 6
    seq = int(time.time()) // _live_seg_secs
    _window = diag["standby_window_segments"]
    _seg_entries = "".join(
        f"#EXT-X-DISCONTINUITY\n#EXTINF:{float(_sdur):.3f},\nstandby.ts?s={seq + i}\n"
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
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
    guide_path = OUTPUT_DIR / "guide.m3u8"
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
    if not STANDBY_SEGMENT.exists():
        abort(404)
    return _make_standby_playlist_response(diag)


@app.get("/hls/live.m3u8")
def hls_live_playlist():
    """Serve the real guide media playlist, or 404 when the guide is not ready.

    This endpoint is the "live" variant that ``/hls/master.m3u8`` points to
    once the guide has enough buffer.  It returns 404 while the pipeline is
    still starting (or is stopped) so that clients that somehow reach this URL
    early get a clean error rather than a partial playlist.
    """
    cfg = {**DEFAULT_CONFIG, **store.get_config()}
    diag = _read_diag_settings(cfg)
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


@app.post("/music/upload")
def music_upload():
    """Upload one or more audio files to the music library."""
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        flash("No files selected.", "error")
        return redirect(url_for("index") + "#music-section")

    saved = []
    skipped = []
    for f in files:
        if not f.filename:
            continue
        name = secure_filename(f.filename)
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            skipped.append(f.filename)
            continue
        dest = MUSIC_DIR / name
        try:
            f.save(str(dest))
            # Validate file content after saving to check magic bytes.
            if not _is_audio_file(dest):
                dest.unlink(missing_ok=True)
                flash(f"Rejected {name}: file does not appear to be a valid audio file.", "error")
                continue
            saved.append(name)
        except OSError as exc:
            flash(f"Could not save {name}: {exc}", "error")

    if saved:
        flash(f"Uploaded: {', '.join(saved)}", "success")
    if skipped:
        flash(
            f"Skipped (unsupported format): {', '.join(skipped)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}",
            "error",
        )
    return redirect(url_for("index") + "#music-section")


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


if __name__ == "__main__":
    host = __import__("os").environ.get("RETROGUIDE_HOST", "0.0.0.0")
    port = int(__import__("os").environ.get("RETROGUIDE_PORT", "8787"))
    app.run(host=host, port=port, debug=False)
