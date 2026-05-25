from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import math
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont

# Duration of the page-scroll animation in seconds.  Increasing this value
# slows the transition between pages, making the guide more comfortable to read.
SCROLL_SECS = 4.0
FRAME_DEADLINE_EPSILON = 1e-9
JITTER_WARN_THRESHOLD_MULTIPLIER = 0.25
DRIFT_WARN_THRESHOLD_MULTIPLIER = 0.5
PROGRAM_TEXT_HIDE_WIDTH = 40
PROGRAM_TEXT_ABBREV_WIDTH = 90
DEFAULT_PROGRAM_MERGE_GAP_SECONDS = 90
DEFAULT_MIN_PIXELS_PER_MINUTE = 6.0
DEFAULT_PROGRAM_BG = "#21406b"

_SPORTS_GROUP_RE = re.compile(r"\bsports?\b", re.IGNORECASE)
_MOVIES_GROUP_RE = re.compile(r"\bmovies?\b", re.IGNORECASE)


def _resolve_program_cell_fill(colors: dict[str, Any], channel_group: str) -> str:
    """Return the program-cell fill color, with optional group-specific overrides."""
    group = channel_group or ""
    if _SPORTS_GROUP_RE.search(group):
        return str(colors.get("program_bg_sports", colors.get("program_bg", DEFAULT_PROGRAM_BG)))
    if _MOVIES_GROUP_RE.search(group):
        return str(colors.get("program_bg_movies", colors.get("program_bg", DEFAULT_PROGRAM_BG)))
    return str(colors.get("program_bg", DEFAULT_PROGRAM_BG))


class RendererTelemetry:
    def __init__(self, enabled: bool, target_fps: int, log_interval_secs: float = 5.0):
        self.enabled = enabled
        self.frame_interval = 1.0 / max(1.0, float(target_fps))
        self.log_interval_secs = max(1.0, float(log_interval_secs))
        self._lock = threading.Lock()
        self._next_log_due = time.monotonic() + self.log_interval_secs
        self._reset_window(time.monotonic())

    def _reset_window(self, now_mono: float) -> None:
        self._window_started = now_mono
        self.render_frames = 0
        self.render_duration_sum = 0.0
        self.render_duration_max = 0.0
        self.render_jitter_sum = 0.0
        self.render_jitter_max = 0.0
        self.render_jitter_samples = 0
        self.missed_frames = 0
        self.dropped_frames = 0
        self.output_frames = 0
        self.output_write_block_sum = 0.0
        self.output_write_block_max = 0.0
        self.output_drift_sum = 0.0
        self.output_abs_drift_sum = 0.0
        self.output_drift_max = 0.0
        self.output_last_frame_version = -1
        self.last_render_started: float | None = None

    def record_render(self, started_at: float, duration_secs: float, missed_slots: int) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.render_frames += 1
            self.render_duration_sum += duration_secs
            self.render_duration_max = max(self.render_duration_max, duration_secs)
            if missed_slots > 0:
                self.missed_frames += missed_slots
            if self.last_render_started is not None:
                jitter = abs((started_at - self.last_render_started) - self.frame_interval)
                self.render_jitter_sum += jitter
                self.render_jitter_max = max(self.render_jitter_max, jitter)
                self.render_jitter_samples += 1
            self.last_render_started = started_at

    def record_output(
        self,
        frame_version: int,
        write_block_secs: float,
        drift_secs: float,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.output_frames += 1
            self.output_write_block_sum += write_block_secs
            self.output_write_block_max = max(self.output_write_block_max, write_block_secs)
            self.output_drift_sum += drift_secs
            self.output_abs_drift_sum += abs(drift_secs)
            self.output_drift_max = max(self.output_drift_max, abs(drift_secs))
            if frame_version == self.output_last_frame_version:
                self.dropped_frames += 1
            self.output_last_frame_version = frame_version

    def maybe_emit(self) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            if now < self._next_log_due:
                return
            elapsed = max(1e-6, now - self._window_started)
            render_fps = self.render_frames / elapsed
            output_fps = self.output_frames / elapsed
            avg_render_ms = (self.render_duration_sum / max(1, self.render_frames)) * 1000.0
            avg_jitter_ms = (self.render_jitter_sum / max(1, self.render_jitter_samples)) * 1000.0
            avg_block_ms = (self.output_write_block_sum / max(1, self.output_frames)) * 1000.0
            avg_drift_ms = (self.output_drift_sum / max(1, self.output_frames)) * 1000.0
            avg_abs_drift_ms = (self.output_abs_drift_sum / max(1, self.output_frames)) * 1000.0
            payload = {
                "type": "renderer_telemetry",
                "window_secs": round(elapsed, 3),
                "target_fps": round(1.0 / self.frame_interval, 3),
                "render_ms_avg": round(avg_render_ms, 3),
                "render_ms_max": round(self.render_duration_max * 1000.0, 3),
                "render_fps_avg": round(render_fps, 3),
                "output_fps": round(output_fps, 3),
                "missed_frames": int(self.missed_frames),
                "dropped_frames": int(self.dropped_frames),
                "frame_pacing_jitter_ms_avg": round(avg_jitter_ms, 3),
                "frame_pacing_jitter_ms_max": round(self.render_jitter_max * 1000.0, 3),
                "stdout_write_block_ms_avg": round(avg_block_ms, 3),
                "stdout_write_block_ms_max": round(self.output_write_block_max * 1000.0, 3),
                "output_timing_drift_ms_avg": round(avg_drift_ms, 3),
                "output_timing_drift_ms_avg_abs": round(avg_abs_drift_ms, 3),
                "output_timing_drift_ms_max_abs": round(self.output_drift_max * 1000.0, 3),
            }
            warnings: list[str] = []
            jitter_warn_ms = self.frame_interval * 1000.0 * JITTER_WARN_THRESHOLD_MULTIPLIER
            drift_warn_ms = self.frame_interval * 1000.0 * DRIFT_WARN_THRESHOLD_MULTIPLIER
            if self.missed_frames > 0:
                warnings.append("render_missed_frames")
            if self.dropped_frames > 0:
                warnings.append("output_reused_frame")
            if payload["frame_pacing_jitter_ms_avg"] > jitter_warn_ms:
                warnings.append("frame_pacing_jitter_high")
            if payload["stdout_write_block_ms_max"] > drift_warn_ms:
                warnings.append("stdout_backpressure_high")
            if payload["output_timing_drift_ms_avg_abs"] > drift_warn_ms:
                warnings.append("output_timing_drift_high")
            payload["warnings"] = warnings
            self._next_log_due = now + self.log_interval_secs
            self._reset_window(now)

        print(f"telemetry:{json.dumps(payload, sort_keys=True)}", file=sys.stderr, flush=True)


def abbreviate_channel_name(name: str, max_len: int = 6) -> str:
    """Return an abbreviated channel label no longer than *max_len* characters.

    For multi-word names the initials are used first (e.g. "Home Box Office"
    becomes "HBO").  If the result is still longer than *max_len* it is
    truncated.  Single-word names are truncated directly.
    """
    words = name.split()
    if len(words) > 1:
        abbrev = "".join(w[0].upper() for w in words if w)
        if len(abbrev) <= max_len:
            return abbrev
    return name[:max_len]


def load_font(size: int):
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def advance_frame_deadline(next_frame_due: float, frame_interval: float, now_mono: float) -> float:
    """Return the next scheduled frame deadline after *next_frame_due*.

    The returned monotonic timestamp is always at least one frame interval
    ahead of the current slot and skips any frame slots that are already
    overdue at *now_mono*.

    Returns:
        float: The monotonic timestamp for the next frame deadline.
    """
    candidate_due = next_frame_due + frame_interval
    if candidate_due <= now_mono:
        overdue = now_mono - candidate_due
        remainder = overdue % frame_interval
        if abs(remainder) < FRAME_DEADLINE_EPSILON or abs(frame_interval - remainder) < FRAME_DEADLINE_EPSILON:
            return now_mono + frame_interval
        return now_mono + (frame_interval - remainder)
    return candidate_due


class GuideRenderer:
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self._last_load = 0.0
        self.state: dict[str, Any] = {}
        self.font_small = load_font(20)
        self.font_medium = load_font(26)
        self.font_large = load_font(36)
        # Throttle mtime checks so that only one stat() syscall is issued
        # every 0.5 s rather than on every call to draw_frame().  The full
        # read+parse only runs when the mtime actually changes.
        self._last_mtime_check: float = 0.0
        self._cache_generation = 0
        self._static_frame_cache_key: tuple[Any, ...] | None = None
        self._static_frame_layer: Image.Image | None = None
        self._static_content_cache: OrderedDict[tuple[Any, ...], Image.Image] = OrderedDict()
        self._max_static_content_cache_entries = 16

    def _invalidate_static_layers(self) -> None:
        self._cache_generation += 1
        self._static_frame_cache_key = None
        self._static_frame_layer = None
        self._static_content_cache.clear()

    def reload_if_needed(self) -> None:
        # Throttle to at most one filesystem stat() call per 0.5 s to reduce
        # per-frame syscall overhead and avoid jitter from the stat() latency
        # spike when the state file is being rewritten by the manager.
        now = time.monotonic()
        if now - self._last_mtime_check < 0.5:
            return
        self._last_mtime_check = now
        try:
            mtime = self.state_path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime <= self._last_load:
            return
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self._last_load = mtime
        self._invalidate_static_layers()

    def _current_page_index(self) -> int:
        pages = self.state.get("pages", [])
        if not pages:
            return 0
        page_seconds = max(3, int(self.state.get("display", {}).get("page_seconds", 12)))
        return int(time.time() // page_seconds) % len(pages)

    def current_page(self) -> list[dict]:
        pages = self.state.get("pages", [])
        if not pages:
            return []
        return pages[self._current_page_index()]

    @staticmethod
    def _normalize_display_title(title: str) -> str:
        """Trim and collapse whitespace for display-only title normalization."""
        return re.sub(r"\s+", " ", str(title or "").strip())

    @classmethod
    def _display_title_key(cls, title: str) -> str:
        normalized = cls._normalize_display_title(title)
        return normalized.casefold()

    def _build_display_programs(
        self,
        programs: list[dict],
        merge_gap_seconds: float,
    ) -> list[dict[str, Any]]:
        """Return display-only programs merged by normalized title within a gap tolerance."""
        parsed: list[dict[str, Any]] = []
        for prog in programs:
            try:
                prog_start = datetime.fromisoformat(prog["start"])
                prog_stop = datetime.fromisoformat(prog["stop"])
            except (KeyError, ValueError):
                continue
            if prog_stop <= prog_start:
                continue
            display_title = self._normalize_display_title(prog.get("title", "Untitled")) or "Untitled"
            parsed.append(
                {
                    "title": display_title,
                    "title_key": self._display_title_key(display_title),
                    "start": prog_start,
                    "stop": prog_stop,
                }
            )
        parsed.sort(key=lambda p: p["start"])
        if not parsed:
            return []

        merged = [parsed[0]]
        for current in parsed[1:]:
            last = merged[-1]
            if current["title_key"] != last["title_key"]:
                merged.append(current)
                continue
            gap = (current["start"] - last["stop"]).total_seconds()
            if gap <= merge_gap_seconds:
                if current["stop"] > last["stop"]:
                    last["stop"] = current["stop"]
                continue
            merged.append(current)
        return merged

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
        return float(draw.textlength(text, font=font))

    def _ellipsize_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> str | None:
        """Return text clipped with an ellipsis so rendered width never exceeds *max_width*."""
        if max_width <= 0:
            return None
        normalized = self._normalize_display_title(text)
        if not normalized:
            return None
        if self._text_width(draw, normalized, font) <= max_width:
            return normalized
        ellipsis = "…"
        if self._text_width(draw, ellipsis, font) > max_width:
            return None
        lo = 0
        hi = len(normalized)
        best = ellipsis
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = normalized[:mid].rstrip() + ellipsis
            if self._text_width(draw, candidate, font) <= max_width:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _program_text_for_cell(
        self,
        draw: ImageDraw.ImageDraw,
        title: str,
        max_text_width: int,
        cell_width: int,
    ) -> str | None:
        """Choose a safe in-cell label based on width thresholds or return None for tiny cells."""
        if cell_width < PROGRAM_TEXT_HIDE_WIDTH:
            return None
        width_limit = max_text_width
        if cell_width <= PROGRAM_TEXT_ABBREV_WIDTH:
            width_limit = int(max_text_width * 0.75)
        return self._ellipsize_text(draw, title, self.font_medium, width_limit)

    @staticmethod
    def _channel_range_label(page: list[dict]) -> str:
        """Return a printable channel-number range for the currently visible page."""
        if not page:
            return "n/a"
        first = str(page[0].get("number", "")).strip()
        last = str(page[-1].get("number", "")).strip()
        if not first and not last:
            return "n/a"
        if not first:
            return last
        if not last or first == last:
            return first
        return f"{first}\u2013{last}"

    @staticmethod
    def _apply_display_tz(dt: datetime, tz_setting: str, browser_timezone: str = "") -> datetime:
        """Convert *dt* to the timezone configured by *tz_setting*.

        ``"utc"`` keeps the time in UTC.  ``"local"`` converts to the IANA
        timezone detected from the admin browser (stored in *browser_timezone*,
        e.g. ``"America/New_York"``).  If no browser timezone has been recorded
        yet the server's OS local timezone is used as a fallback.
        """
        if tz_setting == "utc":
            return dt.astimezone(timezone.utc)
        if browser_timezone:
            try:
                return dt.astimezone(ZoneInfo(browser_timezone))
            except ZoneInfoNotFoundError:
                pass
        return dt.astimezone()

    @staticmethod
    def _timeline_bounds(width: int, layout: dict, total_seconds: float) -> tuple[int, int]:
        """Compute timeline x-bounds while preserving a minimum pixels-per-minute density."""
        guide_minutes = max(1.0, total_seconds / 60.0)
        min_pixels_per_minute = float(layout.get("min_pixels_per_minute", DEFAULT_MIN_PIXELS_PER_MINUTE))
        min_timeline_w = int(math.ceil(guide_minutes * min_pixels_per_minute))
        channel_col_requested = int(layout.get("channel_column_width", 250))
        max_channel_col = max(120, width - min_timeline_w - 12)
        channel_col = min(channel_col_requested, max_channel_col)
        channel_col = max(120, min(channel_col, width - 80))
        return channel_col, width - 12

    @staticmethod
    def _format_footer_text(page_num: int, total_pages: int, channel_range: str, rotation_secs: int) -> str:
        """Build footer pagination/context text shown in the guide chrome."""
        return f"Page {page_num}/{total_pages} | Channels {channel_range} | Rotation interval {rotation_secs}s"

    def _get_static_frame_layer(
        self,
        width: int,
        height: int,
        colors: dict,
        layout: dict,
        display: dict,
    ) -> Image.Image:
        header_height = int(layout.get("header_height", 88))
        footer_height = int(layout.get("footer_height", 42))
        cache_key = (
            self._cache_generation,
            width,
            height,
            header_height,
            footer_height,
            self.state.get("title", "Guide Channel"),
            int(display.get("page_seconds", 12)),
            colors.get("background", "#0a1020"),
            colors.get("header_bg", "#142850"),
            colors.get("header_text", "#ffffff"),
            colors.get("footer_bg", "#102040"),
            colors.get("footer_text", "#d9e6ff"),
        )
        if self._static_frame_cache_key == cache_key and self._static_frame_layer is not None:
            return self._static_frame_layer

        static_frame = Image.new("RGB", (width, height), colors.get("background", "#0a1020"))
        draw = ImageDraw.Draw(static_frame)
        draw.rectangle([0, 0, width, header_height], fill=colors.get("header_bg", "#142850"))
        draw.rectangle([0, height - footer_height, width, height], fill=colors.get("footer_bg", "#102040"))
        draw.text((24, 22), self.state.get("title", "Guide Channel"), font=self.font_large, fill=colors.get("header_text", "#ffffff"))
        self._static_frame_layer = static_frame
        self._static_frame_cache_key = cache_key
        return static_frame

    def _render_content_static(
        self,
        width: int,
        content_height: int,
        page: list[dict],
        colors: dict,
        layout: dict,
        start_dt: datetime,
        end_dt: datetime,
        tz_setting: str = "local",
        browser_timezone: str = "",
    ) -> Image.Image:
        """Render the static guide content layer for one page.

        The returned image has dimensions *(width, content_height)* with y=0
        corresponding to the top of the content area (just below the header).
        """
        img = Image.new("RGB", (width, content_height), colors.get("background", "#0a1020"))
        draw = ImageDraw.Draw(img)

        total_seconds = max(60, (end_dt - start_dt).total_seconds())
        channel_col, timeline_x1 = self._timeline_bounds(width, layout, total_seconds)
        row_height = int(layout.get("row_height", 68))
        timeline_x0 = channel_col
        timeline_w = max(10, timeline_x1 - timeline_x0)
        program_merge_gap_seconds = float(layout.get("program_merge_gap_seconds", DEFAULT_PROGRAM_MERGE_GAP_SECONDS))

        draw.rectangle([0, 0, channel_col, content_height], fill=colors.get("channel_bg", "#0f1b33"))
        draw.line([channel_col, 0, channel_col, content_height], fill=colors.get("grid_line", "#2d4a7a"), width=2)

        first_label = start_dt.replace(minute=(start_dt.minute // 30) * 30, second=0, microsecond=0)
        t = first_label
        while t <= end_dt:
            frac = (t - start_dt).total_seconds() / total_seconds
            x = int(timeline_x0 + frac * timeline_w)
            if timeline_x0 <= x <= timeline_x1:
                draw.line([x, 0, x, content_height], fill=colors.get("grid_line", "#2d4a7a"), width=1)
                label = self._apply_display_tz(t, tz_setting, browser_timezone).strftime("%I:%M %p").lstrip("0")
                draw.text((x + 4, 8), label, font=self.font_small, fill=colors.get("time_text", "#d9e6ff"))
            t += timedelta(minutes=30)

        row_y = 40
        for channel in page:
            if row_y + row_height > content_height:
                break
            program_bg = _resolve_program_cell_fill(colors, str(channel.get("group", "")))
            draw.rectangle([0, row_y, width, row_y + row_height], outline=colors.get("grid_line", "#2d4a7a"), width=1)
            chan_name = abbreviate_channel_name(channel.get('name', 'Unknown'))
            chan_label = f"{channel.get('number', '')}  {chan_name}"
            chan_bbox = draw.textbbox((0, 0), chan_label, font=self.font_medium)
            chan_h = chan_bbox[3] - chan_bbox[1]
            chan_y = row_y + max(0, (row_height - chan_h) // 2)
            draw.text((18, chan_y), chan_label, font=self.font_medium, fill=colors.get("channel_text", "#ffffff"))

            for prog in self._build_display_programs(channel.get("programs", []), program_merge_gap_seconds):
                left_seconds = (prog["start"] - start_dt).total_seconds()
                right_seconds = (prog["stop"] - start_dt).total_seconds()
                raw_x0 = timeline_x0 + int(round((left_seconds / total_seconds) * timeline_w))
                raw_x1 = timeline_x0 + int(round((right_seconds / total_seconds) * timeline_w))
                if raw_x1 <= timeline_x0 or raw_x0 >= timeline_x1:
                    continue
                x0 = max(timeline_x0, raw_x0)
                x1 = min(timeline_x1, raw_x1)
                if x1 - x0 < 4:
                    continue
                left_inset = 1 if x0 == timeline_x0 else 2
                right_inset = 1 if x1 == timeline_x1 else 2
                cell_x0 = x0 + left_inset
                cell_x1 = x1 - right_inset
                if cell_x1 - cell_x0 < 3:
                    continue
                cell_y0 = row_y + 8
                cell_y1 = row_y + row_height - 8
                draw.rounded_rectangle(
                    [cell_x0, cell_y0, cell_x1, cell_y1],
                    radius=min(10, max(2, (cell_y1 - cell_y0) // 2)),
                    fill=program_bg,
                    outline=colors.get("program_outline", "#7db2ff"),
                    width=1,
                )
                text_padding = 8
                available_text_w = max(0, cell_x1 - cell_x0 - (text_padding * 2))
                label = self._program_text_for_cell(draw, prog["title"], available_text_w, cell_x1 - cell_x0)
                if label:
                    text_bbox = draw.textbbox((0, 0), label, font=self.font_medium)
                    text_h = text_bbox[3] - text_bbox[1]
                    text_y = cell_y0 + max(0, ((cell_y1 - cell_y0) - text_h) // 2)
                    draw.text((cell_x0 + text_padding, text_y), label, font=self.font_medium, fill=colors.get("program_text", "#ffffff"))

            row_y += row_height

        return img

    def _get_static_content_layer(
        self,
        width: int,
        content_height: int,
        page_index: int,
        page: list[dict],
        colors: dict,
        layout: dict,
        start_dt: datetime,
        end_dt: datetime,
        tz_setting: str = "local",
        browser_timezone: str = "",
    ) -> Image.Image:
        cache_key = (
            self._cache_generation,
            width,
            content_height,
            int(layout.get("channel_column_width", 250)),
            int(layout.get("row_height", 68)),
            page_index,
            tz_setting,
            browser_timezone,
        )
        cached = self._static_content_cache.get(cache_key)
        if cached is not None:
            self._static_content_cache.move_to_end(cache_key)
            return cached

        static_content = self._render_content_static(
            width,
            content_height,
            page,
            colors,
            layout,
            start_dt,
            end_dt,
            tz_setting,
            browser_timezone,
        )
        self._static_content_cache[cache_key] = static_content
        self._static_content_cache.move_to_end(cache_key)
        if len(self._static_content_cache) > self._max_static_content_cache_entries:
            self._static_content_cache.popitem(last=False)
        return static_content

    def _render_content_dynamic(
        self,
        static_content: Image.Image,
        width: int,
        content_height: int,
        colors: dict,
        layout: dict,
        start_dt: datetime,
        end_dt: datetime,
        now: datetime,
    ) -> Image.Image:
        img = static_content.copy()
        draw = ImageDraw.Draw(img)
        total_seconds = max(60, (end_dt - start_dt).total_seconds())
        timeline_x0, timeline_x1 = self._timeline_bounds(width, layout, total_seconds)
        timeline_w = max(10, timeline_x1 - timeline_x0)
        now_frac = min(1.0, max(0.0, (now - start_dt).total_seconds() / total_seconds))
        now_x = int(timeline_x0 + now_frac * timeline_w)
        draw.line([now_x - 2, 0, now_x - 2, content_height], fill=colors.get("now_line_shadow", "#000000"), width=1)
        draw.line([now_x + 2, 0, now_x + 2, content_height], fill=colors.get("now_line_shadow", "#000000"), width=1)
        draw.line([now_x - 1, 0, now_x - 1, content_height], fill=colors.get("now_line_glow", "#ffe6a6"), width=1)
        draw.line([now_x + 1, 0, now_x + 1, content_height], fill=colors.get("now_line_glow", "#ffe6a6"), width=1)
        draw.line([now_x, 0, now_x, content_height], fill=colors.get("now_line", "#ffd166"), width=3)
        return img

    def draw_frame(self, epoch_time: float | None = None) -> Image.Image:
        """Render one video frame.

        *epoch_time* is the Unix timestamp the frame should represent (used for
        the clock display and scroll position).  When omitted the current wall
        time is used.  Passing the frame's scheduled presentation time keeps the
        scroll animation evenly paced regardless of how long rendering actually
        takes.
        """
        if epoch_time is None:
            epoch_time = time.time()
        self.reload_if_needed()
        display = self.state.get("display", {})
        resolution = display.get("resolution", "1280x720")
        width, height = [int(x) for x in resolution.lower().split("x", 1)]
        theme = self.state.get("theme_data", {})
        colors = theme.get("colors", {})
        layout = theme.get("layout", {})

        header_height = int(layout.get("header_height", 88))
        footer_height = int(layout.get("footer_height", 42))
        content_top = header_height
        content_bottom = height - footer_height
        content_h = content_bottom - content_top
        now = datetime.fromtimestamp(epoch_time, tz=timezone.utc)
        img = self._get_static_frame_layer(width, height, colors, layout, display).copy()
        draw = ImageDraw.Draw(img)

        time_window = self.state.get("time_window", {})
        start_dt = datetime.fromisoformat(time_window.get("start", now.isoformat()))
        end_dt = datetime.fromisoformat(time_window.get("end", now.isoformat()))

        pages = self.state.get("pages", [])
        page_index = self._current_page_index()
        current_page = pages[page_index] if pages else []
        transition = display.get("transition", "scroll")
        tz_setting = display.get("timezone", "local")
        browser_timezone = display.get("browser_timezone", "")

        if transition == "scroll" and pages:
            # Dwell-then-scroll: hold each page for most of page_seconds, then
            # smoothly scroll down to the next page over SCROLL_SECS seconds.
            # This ensures the timeline header is always visible when the page
            # is static and the scroll speed is comfortable to read.
            page_seconds = max(3, int(display.get("page_seconds", 12)))
            dwell_secs = max(1.0, page_seconds - SCROLL_SECS)
            cycle_secs = dwell_secs + SCROLL_SECS

            num_pages = len(pages)
            t = epoch_time % (cycle_secs * num_pages)
            page_idx = int(t // cycle_secs)
            t_in_cycle = t % cycle_secs

            # Compute scroll progress (only used during the scroll phase).
            if t_in_cycle >= dwell_secs:
                progress = (t_in_cycle - dwell_secs) / SCROLL_SECS
                progress = progress * progress * (3.0 - 2.0 * progress)
            else:
                progress = 0.0

            # Only render the 1–2 pages currently visible rather than all N
            # pages at once.  Rendering all pages every frame causes the
            # renderer to fall behind its target fps when there are many pages
            # (e.g. 49 channels → 7 pages), which makes ffmpeg timestamp the
            # slow frames as realtime 30 fps and the guide appears to fast-forward.
            if t_in_cycle < dwell_secs:
                # Dwell phase: only the current page is visible.
                static_page = self._get_static_content_layer(
                    width, content_h, page_idx, pages[page_idx], colors, layout, start_dt, end_dt, tz_setting, browser_timezone
                )
                content_img = self._render_content_dynamic(
                    static_page, width, content_h, colors, layout, start_dt, end_dt, now
                )
            else:
                # Scroll phase: current page scrolls off, next page scrolls in.
                next_idx = (page_idx + 1) % num_pages
                curr_static = self._get_static_content_layer(
                    width, content_h, page_idx, pages[page_idx], colors, layout, start_dt, end_dt, tz_setting, browser_timezone
                )
                next_static = self._get_static_content_layer(
                    width, content_h, next_idx, pages[next_idx], colors, layout, start_dt, end_dt, tz_setting, browser_timezone
                )
                curr_img = self._render_content_dynamic(
                    curr_static, width, content_h, colors, layout, start_dt, end_dt, now
                )
                next_img = self._render_content_dynamic(
                    next_static, width, content_h, colors, layout, start_dt, end_dt, now
                )
                # y_offset is the number of pixels scrolled so far within the
                # current page.  The bottom portion of curr_img fills the top of
                # the viewport; the top portion of next_img fills the rest.
                crop_y = round(progress * content_h)
                content_img = Image.new("RGB", (width, content_h), colors.get("background", "#0a1020"))
                if crop_y > 0:
                    content_img.paste(next_img.crop((0, 0, width, crop_y)), (0, content_h - crop_y))
                if crop_y < content_h:
                    content_img.paste(curr_img.crop((0, crop_y, width, content_h)), (0, 0))

            img.paste(content_img, (0, content_top))
        else:
            # Cut transition: render the current page directly.
            static_page = self._get_static_content_layer(
                width, content_h, page_index, current_page, colors, layout, start_dt, end_dt, tz_setting, browser_timezone
            )
            content_img = self._render_content_dynamic(
                static_page, width, content_h, colors, layout, start_dt, end_dt, now
            )
            img.paste(content_img, (0, content_top))

        clock_text = self._apply_display_tz(now, tz_setting, browser_timezone).strftime("%Y-%m-%d %I:%M:%S %p")
        clock_bbox = draw.textbbox((0, 0), clock_text, font=self.font_medium)
        draw.text((width - (clock_bbox[2] - clock_bbox[0]) - 24, 28), clock_text, font=self.font_medium, fill=colors.get("header_text", "#ffffff"))

        total_pages = max(1, len(pages))
        active_page_idx = page_index
        if transition == "scroll" and pages:
            page_seconds = max(3, int(display.get("page_seconds", 12)))
            dwell_secs = max(1.0, page_seconds - SCROLL_SECS)
            cycle_secs = dwell_secs + SCROLL_SECS
            active_page_idx = int((epoch_time % (cycle_secs * total_pages)) // cycle_secs)
        visible_page = pages[active_page_idx] if pages else []
        channel_range = self._channel_range_label(visible_page)
        footer_text = self._format_footer_text(
            active_page_idx + 1,
            total_pages,
            channel_range,
            max(3, int(display.get("page_seconds", 12))),
        )
        footer_bbox = draw.textbbox((0, 0), footer_text, font=self.font_small)
        footer_y = height - footer_height + max(2, (footer_height - (footer_bbox[3] - footer_bbox[1])) // 2)
        draw.text((24, footer_y), footer_text, font=self.font_small, fill=colors.get("footer_text", "#d9e6ff"))

        return img



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--fps", required=True, type=int)
    parser.add_argument("--resolution", required=True)
    parser.add_argument("--telemetry", action="store_true", help="Enable periodic structured telemetry logs")
    args = parser.parse_args()

    width, height = [int(x) for x in args.resolution.lower().split("x", 1)]
    renderer = GuideRenderer(Path(args.state))
    frame_interval = 1.0 / max(1, args.fps)
    stop_event = threading.Event()
    latest_frame_lock = threading.Lock()
    shared_frame_buffer = {"frame": Image.new("RGB", (width, height), "#000000").tobytes(), "version": 0}
    telemetry = RendererTelemetry(enabled=args.telemetry, target_fps=args.fps)
    exit_code = 0

    def _set_latest_frame(frame_bytes: bytes, version: int) -> None:
        with latest_frame_lock:
            shared_frame_buffer["frame"] = frame_bytes
            shared_frame_buffer["version"] = version

    def _get_latest_frame() -> tuple[bytes, int]:
        with latest_frame_lock:
            return shared_frame_buffer["frame"], int(shared_frame_buffer["version"])

    # Render thread: update the newest frame independently from stdout timing.
    def _render_loop() -> None:
        nonlocal exit_code
        consecutive_errors = 0
        mono_to_wall = time.time() - time.monotonic()
        next_render_due = time.monotonic()
        frame_version = 0
        while not stop_event.is_set():
            sleep_for = next_render_due - time.monotonic()
            if sleep_for > 0:
                stop_event.wait(sleep_for)
                if stop_event.is_set():
                    return

            frame_epoch = next_render_due + mono_to_wall
            render_started = time.monotonic()
            missed_slots = 0
            try:
                frame = renderer.draw_frame(epoch_time=frame_epoch)
                if frame.size != (width, height):
                    frame = frame.resize((width, height))
                frame_version += 1
                _set_latest_frame(frame.tobytes(), frame_version)
                render_duration = max(0.0, time.monotonic() - render_started)
                next_due_candidate = advance_frame_deadline(next_render_due, frame_interval, time.monotonic())
                delta = next_due_candidate - next_render_due
                skipped = max(0.0, (delta / frame_interval) - 1.0)
                missed_slots = int(math.floor(skipped + FRAME_DEADLINE_EPSILON))
                telemetry.record_render(render_started, render_duration, missed_slots)
                next_render_due = next_due_candidate
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                traceback.print_exc(file=sys.stderr)
                if consecutive_errors >= 10:
                    print("renderer: too many consecutive errors, exiting", file=sys.stderr)
                    exit_code = 1
                    stop_event.set()
                    return
                next_render_due = advance_frame_deadline(next_render_due, frame_interval, time.monotonic())
            telemetry.maybe_emit()

    # Output thread: emit exactly one frame per slot at fixed cadence.
    # If rendering falls behind, reuse the latest valid frame and never backfill.
    def _output_loop() -> None:
        next_frame_due = time.monotonic()
        while not stop_event.is_set():
            sleep_for = next_frame_due - time.monotonic()
            if sleep_for > 0:
                stop_event.wait(sleep_for)
                if stop_event.is_set():
                    return
            try:
                frame_bytes, frame_version = _get_latest_frame()
                write_started = time.monotonic()
                drift_secs = write_started - next_frame_due
                sys.stdout.buffer.write(frame_bytes)
                sys.stdout.buffer.flush()
                write_block_secs = max(0.0, time.monotonic() - write_started)
                telemetry.record_output(frame_version, write_block_secs, drift_secs)
            except BrokenPipeError:
                stop_event.set()
                return
            next_frame_due = advance_frame_deadline(next_frame_due, frame_interval, time.monotonic())
            telemetry.maybe_emit()

    render_thread = threading.Thread(target=_render_loop, name="render")
    output_thread = threading.Thread(target=_output_loop, name="output")
    render_thread.start()
    output_thread.start()

    try:
        while True:
            if not render_thread.is_alive() or not output_thread.is_alive():
                stop_event.set()
                break
            render_thread.join(timeout=0.05)
            output_thread.join(timeout=0.05)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        render_thread.join()
        output_thread.join()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
