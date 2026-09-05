from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

SUPPORTED_GUIDE_RESOLUTIONS = {
    "1280x720",
    "1920x1080",
    "960x720",
    "1440x1080",
    # Secondary-output CRT/SD targets. These use native Guide layout rather
    # than scaling a completed widescreen frame.
    "640x480",
    "720x480",
}
SUPPORTED_PREVIEW_AUDIO_MODES = {"guide", "preview", "silent"}
SUPPORTED_PREVIEW_ASPECT_MODES = {"auto", "16:9", "4:3"}
VIRTUAL_PREVIEW_SOURCES = {
    "virtual_weather": ("weather_channel_enabled", "/hls/weather.m3u8"),
    "virtual_traffic": ("traffic_channel_enabled", "/hls/traffic.m3u8"),
    "virtual_news": ("news_channel_enabled", "/hls/news.m3u8"),
    "virtual_channel_mix": ("channel_mix_enabled", "/hls/channel-mix.m3u8"),
}
SUPPORTED_PREVIEW_SOURCE_TYPES = {"file", "url", *VIRTUAL_PREVIEW_SOURCES}
TIMELINE_HEADER_HEIGHT = 40
CLOCK_TEXT_Y = 28
CLOCK_FONT_SIZE = 26
CLOCK_VISUAL_BOTTOM = CLOCK_TEXT_Y + CLOCK_FONT_SIZE + 6


def _even(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def normalize_preview_audio_mode(value: Any) -> str:
    mode = str(value or "guide").strip().lower()
    return mode if mode in SUPPORTED_PREVIEW_AUDIO_MODES else "guide"



def normalize_preview_aspect_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in SUPPORTED_PREVIEW_ASPECT_MODES else "auto"


def preview_source_cache_key(config: dict[str, Any]) -> str:
    """Return a stable key identifying the currently selected preview source."""
    source_type = normalize_preview_source_type(config.get("guide_preview_source_type"))
    if source_type == "url":
        selected = str(config.get("guide_preview_url_channel", "") or "").strip()
        raw = str(config.get("guide_preview_url", "") or "").strip()
        return f"url:{selected or raw}"
    if source_type == "file":
        return f"file:{Path(str(config.get('guide_preview_file', '') or '')).name}"
    return source_type


def known_preview_aspect_ratio(config: dict[str, Any]) -> str | None:
    """Return an aspect ratio RSMC already knows without probing."""
    source_type = normalize_preview_source_type(config.get("guide_preview_source_type"))
    key_map = {
        "virtual_weather": "weather_aspect_ratio",
        "virtual_traffic": "traffic_aspect_ratio",
        "virtual_news": "news_aspect_ratio",
    }
    key = key_map.get(source_type)
    if not key:
        return None
    value = str(config.get(key, "") or "").strip()
    return value if value in {"16:9", "4:3"} else None


def effective_preview_aspect_ratio(config: dict[str, Any]) -> str:
    """Return the preview-window aspect ratio without performing any I/O.

    Manual overrides win. Auto uses a cached detection only when it belongs to
    the exact currently selected source; otherwise it falls back immediately to
    16:9 so Guide startup is never delayed by probing.
    """
    mode = normalize_preview_aspect_mode(config.get("guide_preview_aspect_mode"))
    if mode in {"16:9", "4:3"}:
        return mode
    detected = str(config.get("guide_preview_detected_aspect_ratio", "") or "").strip()
    detected_for = str(config.get("guide_preview_detected_source_key", "") or "").strip()
    if detected in {"16:9", "4:3"} and detected_for == preview_source_cache_key(config):
        return detected
    return "16:9"


def _fraction(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"0:1", "N/A"}:
        return None
    try:
        if ":" in text:
            a, b = text.split(":", 1)
            denominator = float(b)
            return float(a) / denominator if denominator else None
        if "/" in text:
            a, b = text.split("/", 1)
            denominator = float(b)
            return float(a) / denominator if denominator else None
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def detect_preview_aspect_ratio(source: str, timeout_seconds: float = 3.0) -> str | None:
    """Probe one video source and classify it as 16:9 or 4:3.

    This helper is intentionally bounded and is designed to be called from a
    background worker, never from the Guide startup path. Unknown/unusual
    aspect ratios return None and therefore retain the 16:9 Auto fallback.
    """
    if not source:
        return None
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
        "-of", "json", source,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(0.5, float(timeout_seconds)), check=False)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [])[0]
    except (json.JSONDecodeError, IndexError, TypeError, AttributeError):
        return None

    ratio = _fraction(stream.get("display_aspect_ratio"))
    if ratio is None:
        try:
            width = float(stream.get("width") or 0)
            height = float(stream.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width > 0 and height > 0:
            sar = _fraction(stream.get("sample_aspect_ratio")) or 1.0
            ratio = (width * sar) / height
    if ratio is None:
        return None

    candidates = {"4:3": 4 / 3, "16:9": 16 / 9}
    label, target = min(candidates.items(), key=lambda item: abs(ratio - item[1]))
    # Avoid forcing genuinely unusual formats (1:1, 2.35:1, etc.) into a TV
    # ratio. Auto will safely retain its 16:9 fallback for those sources.
    return label if abs(ratio - target) <= 0.12 else None


def normalize_preview_source_type(value: Any) -> str:
    source_type = str(value or "file").strip().lower()
    return source_type if source_type in SUPPORTED_PREVIEW_SOURCE_TYPES else "file"


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def parse_preview_m3u(text: str, playlist_url: str) -> list[dict[str, str]]:
    """Parse an M3U playlist into Guide Preview channel choices.

    Only EXTINF-backed entries are exposed as channels. Relative stream URLs
    are resolved against the playlist URL so playlists served by IPTV systems
    can use either absolute or relative channel targets.
    """
    channels: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = {key: value for key, value in re.findall(r'([\w\-]+)="([^"]*)"', line)}
            # The display name is the text after the first comma outside quotes.
            in_quotes = False
            display_name = ""
            for index, char in enumerate(line):
                if char == '"':
                    in_quotes = not in_quotes
                elif char == "," and not in_quotes:
                    display_name = line[index + 1 :].strip()
                    break
            current = {
                "id": attrs.get("tvg-id", "").strip(),
                "name": (attrs.get("tvg-name") or display_name or "Unknown").strip(),
                "number": attrs.get("tvg-chno", "").strip(),
                "group": attrs.get("group-title", "").strip(),
            }
            continue
        if line.startswith("#") or current is None:
            continue
        stream_url = urljoin(playlist_url, line)
        if is_http_url(stream_url):
            channels.append({**current, "stream_url": stream_url})
        current = None
    return channels


def calculate_preview_layout(
    resolution: str,
    aspect_ratio: str,
    theme_layout: dict[str, Any] | None = None,
    preview_aspect_ratio: str = "16:9",
) -> dict[str, int | str]:
    """Return resolution-aware Guide Channel preview geometry.

    The preview is presented as a raised video window in the information area
    above the guide grid.  Its *outer frame* is vertically centered between the
    clock/header band and the guide, while the frame's right edge lines up with
    the end of the seconds field in the top-right clock (immediately before
    `` PM``/`` AM``).  The same proportions are maintained for all four
    supported 16:9 and 4:3 Guide Channel outputs, including the secondary CRT/SD targets.
    """
    if resolution not in SUPPORTED_GUIDE_RESOLUTIONS:
        resolution = "1280x720"
    width, height = [int(part) for part in resolution.lower().split("x", 1)]
    layout = theme_layout or {}
    ui_scale = max(0.50, min(1.0, float(layout.get("_ui_scale", 1.0) or 1.0)))
    header_height = max(2, int(layout.get("header_height", round(88 * ui_scale))))
    footer_height = max(2, int(layout.get("footer_height", round(42 * ui_scale))))
    row_height = max(16, int(layout.get("row_height", round(68 * ui_scale))))

    # Keep the guide boundary at the approved ~40% position.
    guide_top = _even(round(height * 0.40))
    guide_top = max(header_height + round(80 * ui_scale), min(guide_top, height - footer_height - round(120 * ui_scale)))
    guide_top = _even(guide_top)

    aspect = "4:3" if aspect_ratio == "4:3" else "16:9"
    preview_aspect = "4:3" if preview_aspect_ratio == "4:3" else "16:9"

    # Raised-frame dimensions scale with output height.  The inset is the
    # beveled surround between the outer frame and the video itself.
    frame_inset = _even(max(6, round(height * 0.010)))
    frame_depth = max(2, round(height * 0.004))
    shadow_offset = max(2, round(height * 0.006))

    # The right edge is deliberately farther inboard than the old screen-edge
    # margin.  At the renderer's 26 px clock font, ~70 px is the width of the
    # normal 24 px screen margin plus the trailing " PM"/" AM" text, so this
    # lines the frame up with the final seconds digit.  The renderer clock font
    # and its 24 px edge margin are fixed-size across output resolutions, so the
    # alignment inset is intentionally fixed as well.
    preview_right_margin = max(28, round(70 * ui_scale))

    body_top = header_height
    body_bottom = guide_top
    available_body_h = max(80, body_bottom - body_top)
    vertical_clearance = _even(max(8, round(height * 0.010)))
    max_outer_h = _even(max(64, available_body_h - (vertical_clearance * 2)))
    max_video_h = _even(max(48, max_outer_h - (frame_inset * 2)))

    width_ratio = 0.34 if aspect == "4:3" else 0.30
    requested_video_w = _even(max(120, round(width * width_ratio)))
    # The raised video window follows the selected/detected source aspect.
    # Its right edge and vertical center remain fixed, so switching to 4:3 only
    # moves the left edge inward.
    aspect_w, aspect_h = (4, 3) if preview_aspect == "4:3" else (16, 9)
    requested_video_h = _even(round(requested_video_w * aspect_h / aspect_w))
    if requested_video_h > max_video_h:
        video_h = max_video_h
        video_w = _even(round(video_h * aspect_w / aspect_h))
    else:
        video_w = requested_video_w
        video_h = requested_video_h

    frame_w = _even(video_w + (frame_inset * 2))
    frame_h = _even(video_h + (frame_inset * 2))

    # Keep the approved preview size, but center the complete raised frame in
    # the *visible blank area* between the bottom of the clock text and the top
    # of the guide grid.  Using header_height here made the window read too low
    # because the clock itself ends well above the nominal header boundary.
    visual_top = min(header_height, round(CLOCK_VISUAL_BOTTOM * ui_scale))
    visual_center = (visual_top + guide_top) / 2
    frame_y = _even(round(visual_center - (frame_h / 2)))
    frame_right = width - preview_right_margin
    frame_x = _even(max(0, frame_right - frame_w))
    video_x = frame_x + frame_inset
    video_y = frame_y + frame_inset

    content_height = max(1, height - guide_top - footer_height)
    max_rows = max(1, (content_height - TIMELINE_HEADER_HEIGHT) // row_height)

    return {
        "resolution": resolution,
        "aspect_ratio": aspect,
        "preview_aspect_ratio": preview_aspect,
        "width": width,
        "height": height,
        "header_height": header_height,
        "footer_height": footer_height,
        "guide_top": guide_top,
        "clock_visual_bottom": round(CLOCK_VISUAL_BOTTOM * ui_scale),
        "content_height": content_height,
        "margin": vertical_clearance,
        "preview_right_margin": preview_right_margin,
        "preview_frame_x": frame_x,
        "preview_frame_y": frame_y,
        "preview_frame_width": frame_w,
        "preview_frame_height": frame_h,
        "preview_frame_inset": frame_inset,
        "preview_frame_depth": frame_depth,
        "preview_shadow_offset": shadow_offset,
        "preview_x": video_x,
        "preview_y": video_y,
        "preview_max_width": video_w,
        "preview_max_height": video_h,
        "row_height": row_height,
        "ui_scale": ui_scale,
        "max_rows": max_rows,
    }


def resolve_preview_source(config: dict[str, Any], base_dir: Path) -> str | None:
    """Resolve the configured preview source to a local file path or URL."""
    source_type = normalize_preview_source_type(config.get("guide_preview_source_type"))
    if source_type == "url":
        # When the configured URL is an M3U playlist, use the explicitly
        # selected channel stream. If no channel was selected, retain the
        # original direct-URL behavior.
        selected = str(config.get("guide_preview_url_channel", "") or "").strip()
        if selected and is_http_url(selected):
            return selected
        raw = str(config.get("guide_preview_url", "") or "").strip()
        return raw if is_http_url(raw) else None

    if source_type in VIRTUAL_PREVIEW_SOURCES:
        enabled_key, stream_path = VIRTUAL_PREVIEW_SOURCES[source_type]
        if not bool(config.get(enabled_key, False)):
            return None
        port = str(os.environ.get("RETROGUIDE_PORT", "8787") or "8787").strip()
        if not port.isdigit():
            port = "8787"
        return f"http://127.0.0.1:{port}{stream_path}"

    filename = Path(str(config.get("guide_preview_file", "") or "")).name
    if not filename:
        return None
    path = base_dir / "data" / "guide_preview" / filename
    return str(path) if path.is_file() else None
