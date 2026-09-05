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
        self._ui_scale = 1.0
        self.font_small = load_font(20)
        self.font_medium = load_font(26)
        self.font_large = load_font(36)
        # Throttle mtime checks so that only one stat() syscall is issued
        # every 0.5 s rather than on every call to draw_frame().  The full
        # read+parse only runs when the mtime actually changes.
        self._last_mtime_check: float = 0.0
        self._guide_message_signature: tuple[Any, ...] | None = None
        self._guide_message_cycle_started_at: float = time.time()
        self._cache_generation = 0
        self._static_frame_cache_key: tuple[Any, ...] | None = None
        self._static_frame_layer: Image.Image | None = None
        self._static_content_cache: OrderedDict[tuple[Any, ...], Image.Image] = OrderedDict()
        self._max_static_content_cache_entries = 16

    def _set_ui_scale(self, value: Any) -> None:
        try:
            scale = float(value)
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(0.50, min(1.0, scale))
        if abs(scale - self._ui_scale) < 0.001:
            return
        self._ui_scale = scale
        self.font_small = load_font(max(10, round(20 * scale)))
        self.font_medium = load_font(max(12, round(26 * scale)))
        self.font_large = load_font(max(16, round(36 * scale)))
        self._invalidate_static_layers()

    def _px(self, value: float, minimum: int = 1) -> int:
        return max(minimum, int(round(float(value) * self._ui_scale)))

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
            new_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        display = new_state.get("display", {}) or {}
        message_signature = (
            bool(display.get("guide_message_enabled", False)),
            str(display.get("guide_message_text", "") or ""),
            str(display.get("guide_message_interval_seconds", 8)),
        )
        if message_signature != self._guide_message_signature:
            self._guide_message_signature = message_signature
            self._guide_message_cycle_started_at = time.time()
        self.state = new_state
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

    @staticmethod
    def _split_guide_messages(raw_text: str) -> list[list[str]]:
        """Return plain-text content as one message while preserving blank lines.

        Blank lines are formatting now, not message separators. Explicit
        ``[message]`` blocks control rotation boundaries. This helper remains
        for callers/tests that need the normalized plain-text representation.
        """
        normalized = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            return []
        lines = [line.rstrip() for line in normalized.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return [lines] if lines else []

    @staticmethod
    def _parse_guide_message_slides(raw_text: str) -> list[dict[str, Any]]:
        """Parse explicit message blocks and blank-screen directives.

        Supported syntax::

            [message]
            Normal-duration message

            Blank lines inside are preserved.
            [/message]

            [message:45]
            This message displays for 45 seconds.
            [/message]

            [blank]
            [blank:90]

        Blank lines never separate rotating messages. Text outside explicit
        tags is treated as one normal-duration message for a forgiving/simple
        single-message configuration. Durations are capped at one hour.
        """
        import re

        normalized = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            return []

        message_open_re = re.compile(r"^\[message(?::\s*(\d+))?\]$", re.IGNORECASE)
        message_close_re = re.compile(r"^\[/message\]$", re.IGNORECASE)
        blank_re = re.compile(r"^\[blank(?::\s*(\d+))?\]$", re.IGNORECASE)

        slides: list[dict[str, Any]] = []
        outside: list[str] = []
        inside: list[str] | None = None
        inside_duration: int | None = None

        def trim_edge_blanks(lines: list[str]) -> list[str]:
            result = [line.rstrip() for line in lines]
            while result and not result[0].strip():
                result.pop(0)
            while result and not result[-1].strip():
                result.pop()
            return result

        def flush_outside() -> None:
            nonlocal outside
            block = trim_edge_blanks(outside)
            if block:
                slides.append({"lines": block, "blank": False, "duration": None})
            outside = []

        for raw_line in normalized.split("\n"):
            stripped = raw_line.strip()

            if inside is not None:
                if message_close_re.match(stripped):
                    block = trim_edge_blanks(inside)
                    if block:
                        slides.append({
                            "lines": block,
                            "blank": False,
                            "duration": inside_duration,
                        })
                    inside = None
                    inside_duration = None
                else:
                    inside.append(raw_line.rstrip())
                continue

            open_match = message_open_re.match(stripped)
            if open_match:
                flush_outside()
                raw_seconds = open_match.group(1)
                inside_duration = None
                if raw_seconds:
                    seconds = int(raw_seconds)
                    if seconds > 0:
                        inside_duration = min(seconds, 3600)
                inside = []
                continue

            blank_match = blank_re.match(stripped)
            if blank_match:
                flush_outside()
                raw_seconds = blank_match.group(1)
                duration = None
                if raw_seconds:
                    seconds = int(raw_seconds)
                    if seconds <= 0:
                        # Invalid non-positive directives remain visible text
                        # rather than silently creating an unexpected pause.
                        outside.append(raw_line.rstrip())
                        continue
                    duration = min(seconds, 3600)
                slides.append({"lines": [], "blank": True, "duration": duration})
                continue

            # A stray closing tag is treated literally.
            outside.append(raw_line.rstrip())

        # An unclosed [message] block is still useful admin content; preserve it
        # as a message instead of dropping the text.
        if inside is not None:
            block = trim_edge_blanks(inside)
            if block:
                slides.append({"lines": block, "blank": False, "duration": inside_duration})
        else:
            flush_outside()

        return slides

    @staticmethod
    def _select_guide_message_slide(
        slides: list[dict[str, Any]], epoch_time: float, default_interval: int
    ) -> dict[str, Any] | None:
        """Select the active slide using each slide's effective duration."""
        if not slides:
            return None
        durations = [
            max(1, int(slide.get("duration") or default_interval))
            for slide in slides
        ]
        cycle_seconds = sum(durations)
        if cycle_seconds <= 0:
            return slides[0]
        position = float(epoch_time) % cycle_seconds
        elapsed = 0.0
        for slide, duration in zip(slides, durations):
            elapsed += duration
            if position < elapsed:
                return slide
        return slides[-1]

    def _wrap_guide_message_lines(
        self,
        draw: ImageDraw.ImageDraw,
        source_lines: list[str],
        max_width: int,
        max_lines: int = 4,
    ) -> list[str]:
        """Wrap one message while preserving intentional blank display lines."""
        wrapped: list[str] = []
        truncated = False
        for source_index, source in enumerate(source_lines):
            # Blank lines inside [message] blocks are intentional vertical
            # spacing and consume one of the bounded on-screen lines.
            if not source.strip():
                wrapped.append("")
                if len(wrapped) >= max_lines:
                    truncated = source_index < len(source_lines) - 1
                    break
                continue

            words = source.split()
            line = words[0]
            for word in words[1:]:
                candidate = f"{line} {word}"
                if self._text_width(draw, candidate, self.font_small) <= max_width:
                    line = candidate
                    continue
                clipped = self._ellipsize_text(draw, line, self.font_small, max_width)
                if clipped:
                    wrapped.append(clipped)
                if len(wrapped) >= max_lines:
                    truncated = True
                    break
                line = word
            if truncated:
                break
            clipped = self._ellipsize_text(draw, line, self.font_small, max_width)
            if clipped:
                wrapped.append(clipped)
            if len(wrapped) >= max_lines:
                truncated = source_index < len(source_lines) - 1
                break

        wrapped = wrapped[:max_lines]
        if truncated and wrapped:
            # Put the ellipsis on the last visible text line. If the final
            # visible line is intentionally blank, walk backward to find text.
            last_text_index = next(
                (index for index in range(len(wrapped) - 1, -1, -1) if wrapped[index]),
                None,
            )
            if last_text_index is not None and not wrapped[last_text_index].endswith("…"):
                candidate = wrapped[last_text_index].rstrip(".") + "…"
                clipped = self._ellipsize_text(draw, candidate, self.font_small, max_width)
                if clipped:
                    wrapped[last_text_index] = clipped
        return wrapped

    def _draw_guide_message(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        colors: dict[str, Any],
        display: dict[str, Any],
        epoch_time: float,
    ) -> None:
        """Draw one low-emphasis rotating message opposite the video preview."""
        if not bool(display.get("preview_enabled", False)):
            return
        if not bool(display.get("guide_message_enabled", False)):
            return
        slides = self._parse_guide_message_slides(display.get("guide_message_text", ""))
        if not slides:
            return

        preview_layout = display.get("preview_layout", {}) or {}
        frame_x = int(preview_layout.get("preview_frame_x", width))
        frame_y = int(preview_layout.get("preview_frame_y", 100))
        guide_top = int(preview_layout.get("guide_top", 288))

        # Begin approximately under the left edge of the 'u' in 'Guide Channel'.
        title_x = self._px(24)
        message_x = int(title_x + self._text_width(draw, "G", self.font_large))

        # Keep text firmly in the left information area. The 44% cap matches the
        # approved mockup near the 9:30/current-time divider at 1280x720 and
        # scales proportionally across the four supported Guide resolutions.
        right_cap = min(frame_x - max(self._px(24), round(width * 0.018)), round(width * 0.44))
        max_width = right_cap - message_x
        if max_width < self._px(120, minimum=60):
            return

        try:
            interval = max(3, min(60, int(display.get("guide_message_interval_seconds", 8))))
        except (TypeError, ValueError):
            interval = 8
        cycle_elapsed = max(0.0, float(epoch_time) - self._guide_message_cycle_started_at)
        slide = self._select_guide_message_slide(slides, cycle_elapsed, interval)
        if slide is None or bool(slide.get("blank", False)):
            return
        lines = self._wrap_guide_message_lines(
            draw, list(slide.get("lines", [])), max_width, max_lines=4
        )
        if not lines:
            return

        bbox = self.font_small.getbbox("Ag")
        line_height = max(self._px(22, minimum=12), (bbox[3] - bbox[1]) + self._px(8, minimum=4))
        block_h = line_height * len(lines)
        usable_top = max(self._px(72, minimum=36), frame_y)
        usable_bottom = guide_top - self._px(18, minimum=9)
        message_y = max(usable_top, round((usable_top + usable_bottom - block_h) / 2))

        primary = colors.get("header_text", "#ffffff")
        secondary = colors.get("footer_text", primary)
        for i, line in enumerate(lines):
            fill = primary if i == 0 else secondary
            draw.text((message_x, message_y + i * line_height), line, font=self.font_small, fill=fill)

    def _program_text_for_cell(
        self,
        draw: ImageDraw.ImageDraw,
        title: str,
        max_text_width: int,
        cell_width: int,
    ) -> str | None:
        """Choose a safe in-cell label based on width thresholds or return None for tiny cells."""
        if cell_width < self._px(PROGRAM_TEXT_HIDE_WIDTH, minimum=20):
            return None
        width_limit = max_text_width
        if cell_width <= self._px(PROGRAM_TEXT_ABBREV_WIDTH, minimum=45):
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

    def _timeline_bounds(self, width: int, layout: dict, total_seconds: float) -> tuple[int, int]:
        """Compute timeline x-bounds while preserving a minimum pixels-per-minute density."""
        guide_minutes = max(1.0, total_seconds / 60.0)
        min_pixels_per_minute = float(layout.get("min_pixels_per_minute", DEFAULT_MIN_PIXELS_PER_MINUTE))
        min_timeline_w = int(math.ceil(guide_minutes * min_pixels_per_minute))
        channel_col_requested = int(layout.get("channel_column_width", 250))
        edge = self._px(12)
        min_channel = self._px(120, minimum=60)
        max_channel_col = max(min_channel, width - min_timeline_w - edge)
        channel_col = min(channel_col_requested, max_channel_col)
        channel_col = max(min_channel, min(channel_col, width - self._px(80, minimum=40)))
        return channel_col, width - edge

    @staticmethod
    def _format_footer_text(page_num: int, total_pages: int, channel_range: str, rotation_secs: int) -> str:
        """Build footer pagination/context text shown in the guide chrome."""
        return f"Page {page_num}/{total_pages} | Channels {channel_range} | Rotation interval {rotation_secs}s"

    @staticmethod
    def _draw_preview_frame(
        draw: ImageDraw.ImageDraw, preview_layout: dict[str, Any], colors: dict[str, Any]
    ) -> None:
        """Draw a theme-safe raised/beveled surround behind the preview.

        Shadow tones are blended against the active theme header color instead
        of using one opaque near-black block.  This keeps the lift visible on
        bright themes without becoming harsh, while retaining separation on
        dark themes.
        """

        def _rgb(value: str, fallback: str = "#142850") -> tuple[int, int, int]:
            raw = str(value or fallback).lstrip("#")
            if len(raw) != 6:
                raw = fallback.lstrip("#")
            try:
                return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return (20, 40, 80)

        def _blend(base: str, target: str, amount: float) -> str:
            b = _rgb(base)
            t = _rgb(target)
            amount = max(0.0, min(1.0, float(amount)))
            vals = [round(bv * (1.0 - amount) + tv * amount) for bv, tv in zip(b, t)]
            return "#" + "".join(f"{v:02X}" for v in vals)
        x = int(preview_layout.get("preview_frame_x", 0))
        y = int(preview_layout.get("preview_frame_y", 0))
        w = int(preview_layout.get("preview_frame_width", 0))
        h = int(preview_layout.get("preview_frame_height", 0))
        inset = max(2, int(preview_layout.get("preview_frame_inset", 8)))
        depth = max(2, int(preview_layout.get("preview_frame_depth", 3)))
        shadow = max(2, int(preview_layout.get("preview_shadow_offset", 4)))
        if w <= 0 or h <= 0:
            return

        x2 = x + w - 1
        y2 = y + h - 1
        radius = max(5, inset)

        header_bg = str(colors.get("header_bg", "#142850"))

        # A stepped, neutral shadow approximates a soft alpha shadow in the RGB
        # renderer.  Each layer is blended with the current theme background.
        # This avoids a heavy black rectangle on light/bright themes.
        shadow_layers = (
            (shadow + 3, 0.16),
            (shadow + 2, 0.22),
            (shadow + 1, 0.30),
            (shadow, 0.40),
        )
        for offset, strength in shadow_layers:
            draw.rounded_rectangle(
                [x + offset, y + offset, x2 + offset, y2 + offset],
                radius=radius,
                fill=_blend(header_bg, "#000000", strength),
            )

        # Neutral metallic body and bevel remain intentionally independent of
        # theme hue so the window reads as raised hardware on every theme.
        draw.rounded_rectangle([x, y, x2, y2], radius=radius, fill="#30333F", outline="#8A8E9E", width=1)

        # Multi-line bevel: light on top/left, dark on bottom/right creates the
        # raised edge without requiring transparency or post-processing.
        for i in range(depth):
            hi = "#7D8192" if i == 0 else "#555968"
            lo = "#151721" if i == 0 else "#20232E"
            draw.line([x + radius, y + i, x2 - radius, y + i], fill=hi, width=1)
            draw.line([x + i, y + radius, x + i, y2 - radius], fill=hi, width=1)
            draw.line([x + radius, y2 - i, x2 - radius, y2 - i], fill=lo, width=1)
            draw.line([x2 - i, y + radius, x2 - i, y2 - radius], fill=lo, width=1)

        # Recessed black inner lip directly surrounds the video surface.
        ix = x + inset - 2
        iy = y + inset - 2
        ix2 = x2 - inset + 2
        iy2 = y2 - inset + 2
        draw.rectangle([ix, iy, ix2, iy2], fill="#080A10", outline="#111522", width=2)

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
        preview_enabled = bool(display.get("preview_enabled", False))
        preview_layout = display.get("preview_layout", {}) if preview_enabled else {}
        guide_top = int(preview_layout.get("guide_top", header_height)) if preview_enabled else header_height
        cache_key = (
            self._cache_generation,
            width,
            height,
            header_height,
            footer_height,
            preview_enabled,
            guide_top,
            tuple(sorted(preview_layout.items())) if preview_enabled else (),
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
        # When preview video is enabled, the entire upper information region uses
        # the theme's header color. This keeps custom theme colors consistent
        # around the preview video and reserves the left side for future text.
        upper_fill_bottom = guide_top if preview_enabled else header_height
        draw.rectangle([0, 0, width, upper_fill_bottom], fill=colors.get("header_bg", "#142850"))
        draw.rectangle([0, height - footer_height, width, height], fill=colors.get("footer_bg", "#102040"))
        if preview_enabled:
            self._draw_preview_frame(draw, preview_layout, colors)
        draw.text((self._px(24), self._px(22)), self.state.get("title", "Guide Channel"), font=self.font_large, fill=colors.get("header_text", "#ffffff"))
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
                draw.text((x + self._px(4), self._px(8)), label, font=self.font_small, fill=colors.get("time_text", "#d9e6ff"))
            t += timedelta(minutes=30)

        row_y = self._px(40, minimum=20)
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
            draw.text((self._px(18), chan_y), chan_label, font=self.font_medium, fill=colors.get("channel_text", "#ffffff"))

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
                cell_y0 = row_y + self._px(8, minimum=4)
                cell_y1 = row_y + row_height - self._px(8, minimum=4)
                draw.rounded_rectangle(
                    [cell_x0, cell_y0, cell_x1, cell_y1],
                    radius=min(self._px(10, minimum=4), max(2, (cell_y1 - cell_y0) // 2)),
                    fill=program_bg,
                    outline=colors.get("program_outline", "#7db2ff"),
                    width=1,
                )
                text_padding = self._px(8, minimum=4)
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
        self._set_ui_scale(display.get("ui_scale", 1.0))
        resolution = display.get("resolution", "1280x720")
        width, height = [int(x) for x in resolution.lower().split("x", 1)]
        theme = self.state.get("theme_data", {})
        colors = theme.get("colors", {})
        layout = theme.get("layout", {})

        header_height = int(layout.get("header_height", 88))
        footer_height = int(layout.get("footer_height", 42))
        preview_enabled = bool(display.get("preview_enabled", False))
        preview_layout = display.get("preview_layout", {}) if preview_enabled else {}
        content_top = int(preview_layout.get("guide_top", header_height)) if preview_enabled else header_height
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
        draw.text((width - (clock_bbox[2] - clock_bbox[0]) - self._px(24), self._px(28)), clock_text, font=self.font_medium, fill=colors.get("header_text", "#ffffff"))
        self._draw_guide_message(draw, width, colors, display, epoch_time)

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
        footer_y = height - footer_height + max(self._px(2), (footer_height - (footer_bbox[3] - footer_bbox[1])) // 2)
        draw.text((self._px(24), footer_y), footer_text, font=self.font_small, fill=colors.get("footer_text", "#d9e6ff"))

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
