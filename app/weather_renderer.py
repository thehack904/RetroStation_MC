"""Weather channel frame renderer.

Reads a weather state JSON file written by WeatherChannelManager and renders
cycling 16:9 frames to stdout (raw RGB24) so that a companion ffmpeg process
can encode them into an HLS media stream at /hls/weather.m3u8.

The display cycles through five segments wall-clock aligned:
  0 – Current Conditions
  1 – 5-Day Forecast
  2 – Regional Radar
  3 – Severe Weather Alerts
  4 – Extended Forecast (10-Day)

Usage (invoked by WeatherChannelManager):
    python app/weather_renderer.py \\
        --state /path/to/weather_state.json \\
        --fps 10 \\
        --resolution 1280x720
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

# ── Colour palette ─────────────────────────────────────────────────────────────
_BG        = (10, 26, 110)    # main background
_BG_HDR    = ( 5, 14,  80)    # header bar
_BG_TICK   = ( 4, 10,  65)    # ticker strip
_BG_CARD   = (14, 36, 140)    # column / card highlight
_BG_SEP    = (20, 50, 170)    # column separator
_WHITE     = (255, 255, 255)
_YELLOW    = (255, 215,  40)   # temperature / accent
_LBLUE     = (170, 195, 255)   # secondary text / labels
_GRAY      = (110, 145, 210)   # dimmed / hint text
_RED       = (240,  80,  80)   # alert highlight
_ORANGE    = (255, 150,  40)   # warning
_GREEN     = ( 80, 210,  80)   # ok / positive
_SUN       = (255, 214,  40)
_SUN_GLOW  = (255, 236, 140)
_RAIN      = ( 90, 180, 255)
_MOON      = (220, 230, 255)

FRAME_DEADLINE_EPSILON = 1e-9
JITTER_WARN_THRESHOLD  = 0.25

# ── Short condition labels used in multi-column views ─────────────────────────
_ICON_SHORT: dict[str, str] = {
    "sunny":               "SUNNY",
    "partly_cloudy":       "PT CLOUDY",
    "partly_cloudy_night": "PT CLOUDY",
    "cloudy":              "CLOUDY",
    "cloudy_night":        "CLOUDY",
    "rain":                "RAIN",
    "showers":             "SHOWERS",
    "thunderstorm":        "T-STORM",
    "drizzle":             "DRIZZLE",
    "snow":                "SNOW",
    "foggy":               "FOGGY",
    "windy":               "WINDY",
}


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _advance_deadline(next_due: float, interval: float, now: float) -> float:
    candidate = next_due + interval
    if candidate <= now:
        overdue   = now - candidate
        remainder = overdue % interval
        if (
            abs(remainder) < FRAME_DEADLINE_EPSILON
            or abs(interval - remainder) < FRAME_DEADLINE_EPSILON
        ):
            return now + interval
        return now + (interval - remainder)
    return candidate


class WeatherRenderer:
    """PIL-based renderer for the weather virtual channel."""

    def __init__(self, state_path: Path, width: int, height: int) -> None:
        self.state_path = state_path
        self.width      = width
        self.height     = height

        self._state: dict = {}
        self._last_mtime: float       = 0.0
        self._last_mtime_check: float = 0.0

        h = height
        # Fixed layout geometry
        self._hdr_h  = max(36, h * 9  // 100)   # header bar
        self._tick_h = max(26, h * 7  // 100)   # ticker strip
        self._body_h = h - self._hdr_h - self._tick_h
        self._body_y = self._hdr_h

        # Pre-load font sizes scaled to frame height
        self._f_huge  = _load_font(max(24, h * 20 // 100))  # giant temperature
        self._f_large = _load_font(max(18, h *  7 // 100))  # segment titles
        self._f_med   = _load_font(max(14, h *  5 // 100))  # body text
        self._f_small = _load_font(max(12, h *  4 // 100))  # details / labels
        self._f_tiny  = _load_font(max(10, h *  3 // 100))  # ticker / fine print

    # ── State loading ─────────────────────────────────────────────────────────

    def _reload(self) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_mtime_check < 0.5:
            return
        self._last_mtime_check = now_mono
        try:
            mtime = self.state_path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime <= self._last_mtime:
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._state       = data
            self._last_mtime  = mtime
        except (json.JSONDecodeError, OSError):
            pass

    # ── Segment helpers ───────────────────────────────────────────────────────

    def _current_segment(self, epoch: float) -> int:
        sps   = max(30, int(self._state.get("seconds_per_segment", 300)))
        cycle = 5 * sps
        return int(epoch % cycle) // sps

    def _unit_sym(self) -> str:
        return "°C" if self._state.get("units") == "C" else "°F"

    def _apply_display_tz(self, dt: datetime) -> datetime:
        tz_setting = str(self._state.get("timezone", "local")).strip().lower()
        if tz_setting == "utc":
            return dt.astimezone(timezone.utc)
        browser_timezone = str(self._state.get("browser_timezone", "")).strip()
        if browser_timezone:
            try:
                return dt.astimezone(ZoneInfo(browser_timezone))
            except Exception:
                pass
        return dt.astimezone()

    # ── Text helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]

    @staticmethod
    def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[3] - bb[1]

    def _center(self, draw: ImageDraw.ImageDraw, cx: int, y: int,
                text: str, font, fill) -> None:
        w = self._tw(draw, text, font)
        draw.text((cx - w // 2, y), text, font=font, fill=fill)

    # ── Icon drawing helpers ─────────────────────────────────────────────────

    def _draw_cloud(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int,
                    fill: tuple[int, int, int] = _LBLUE) -> None:
        w = max(16, int(size * 0.72))
        h = max(10, int(size * 0.34))
        y = cy + max(1, size // 18)
        left = cx - w // 2
        right = cx + w // 2
        top = y - h // 2
        bottom = y + h // 2
        bump = max(4, h // 3)
        draw.ellipse([left + w * 0.03, top + bump, left + w * 0.45, bottom], fill=fill)
        draw.ellipse([left + w * 0.26, top, left + w * 0.74, bottom], fill=fill)
        draw.ellipse([left + w * 0.52, top + bump, right - w * 0.02, bottom], fill=fill)
        draw.rounded_rectangle([left + w * 0.07, top + bump, right - w * 0.07, bottom], radius=max(3, h // 4), fill=fill)

    def _draw_sun(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int) -> None:
        r = max(6, int(size * 0.22))
        inner = r + max(4, size // 12)
        outer = r + max(8, size // 4)
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = cx + int(inner * math.cos(rad))
            y1 = cy + int(inner * math.sin(rad))
            x2 = cx + int(outer * math.cos(rad))
            y2 = cy + int(outer * math.sin(rad))
            draw.line([x1, y1, x2, y2], fill=_SUN, width=max(2, size // 16))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_SUN, outline=_SUN_GLOW, width=max(1, size // 16))

    def _draw_weather_icon(self, draw: ImageDraw.ImageDraw, icon: str, cx: int, cy: int, size: int) -> None:
        key = (icon or "").strip().lower()
        if key not in _ICON_SHORT:
            key = "cloudy"

        if key == "sunny":
            self._draw_sun(draw, cx, cy, size)
            return

        if key in {"partly_cloudy", "partly_cloudy_night"}:
            if key.endswith("night"):
                moon_r = max(5, int(size * 0.16))
                moon_x = cx - max(8, size // 5)
                moon_y = cy - max(8, size // 5)
                draw.ellipse([moon_x - moon_r, moon_y - moon_r, moon_x + moon_r, moon_y + moon_r], fill=_MOON)
                cut = max(2, moon_r // 2)
                draw.ellipse(
                    [moon_x - moon_r + cut, moon_y - moon_r - cut, moon_x + moon_r + cut, moon_y + moon_r - cut],
                    fill=_BG,
                )
            else:
                self._draw_sun(draw, cx - max(8, size // 6), cy - max(8, size // 6), max(24, int(size * 0.8)))
            self._draw_cloud(draw, cx + max(2, size // 16), cy + max(2, size // 16), size)
            return

        if key in {"cloudy", "cloudy_night", "windy"}:
            self._draw_cloud(draw, cx, cy, size)
            if key == "windy":
                y_base = cy + max(8, size // 6)
                for i in range(2):
                    y = y_base + i * max(5, size // 10)
                    draw.line([cx - size // 3, y, cx + size // 3, y], fill=_RAIN, width=max(1, size // 20))
            return

        if key in {"rain", "showers", "drizzle"}:
            self._draw_cloud(draw, cx, cy - max(2, size // 20), size)
            drop_len = max(7, size // 5)
            count = 4 if key == "showers" else 3
            spacing = max(6, size // 7)
            start_x = cx - (spacing * (count - 1)) // 2
            for i in range(count):
                x = start_x + i * spacing
                y0 = cy + max(8, size // 8)
                y1 = y0 + (drop_len if key != "drizzle" else max(4, drop_len // 2))
                draw.line([x, y0, x - max(1, size // 30), y1], fill=_RAIN, width=max(2, size // 18))
            return

        if key == "thunderstorm":
            self._draw_cloud(draw, cx, cy - max(2, size // 16), size)
            bolt_h = max(10, size // 3)
            top = cy + max(5, size // 10)
            draw.polygon(
                [
                    (cx - size // 12, top),
                    (cx + size // 16, top),
                    (cx - size // 18, top + bolt_h // 2),
                    (cx + size // 10, top + bolt_h // 2),
                    (cx - size // 10, top + bolt_h),
                    (cx - size // 18, top + bolt_h // 2),
                ],
                fill=_YELLOW,
            )
            return

        if key == "snow":
            self._draw_cloud(draw, cx, cy - max(2, size // 20), size)
            r = max(4, size // 11)
            for xoff in (-size // 7, 0, size // 7):
                fx = cx + xoff
                fy = cy + max(10, size // 7)
                draw.line([fx - r, fy, fx + r, fy], fill=_WHITE, width=max(1, size // 24))
                draw.line([fx, fy - r, fx, fy + r], fill=_WHITE, width=max(1, size // 24))
            return

        if key == "foggy":
            self._draw_cloud(draw, cx, cy - max(2, size // 16), size)
            for i in range(3):
                y = cy + max(8, size // 7) + i * max(4, size // 14)
                draw.line([cx - size // 3, y, cx + size // 3, y], fill=_GRAY, width=max(1, size // 24))
            return

        self._draw_cloud(draw, cx, cy, size)

    # ── Persistent chrome ─────────────────────────────────────────────────────

    def _draw_header(self, draw: ImageDraw.ImageDraw, epoch: float) -> None:
        w, hh = self.width, self._hdr_h
        draw.rectangle([0, 0, w - 1, hh - 1], fill=_BG_HDR)

        brand = "RETROSTATION WEATHER"
        by    = (hh - self._th(draw, brand, self._f_small)) // 2
        draw.text((12, by), brand, font=self._f_small, fill=_YELLOW)

        location = str(self._state.get("location", ""))
        if location:
            lw = self._tw(draw, location, self._f_small)
            draw.text((w // 2 - lw // 2, by), location, font=self._f_small, fill=_WHITE)

        ts    = self._apply_display_tz(datetime.fromtimestamp(epoch, tz=timezone.utc))
        clock = ts.strftime("%I:%M:%S %p")
        cw    = self._tw(draw, clock, self._f_small)
        draw.text((w - cw - 12, by), clock, font=self._f_small, fill=_LBLUE)

        draw.line([0, hh - 2, w - 1, hh - 2], fill=_YELLOW, width=2)

    def _draw_ticker(self, draw: ImageDraw.ImageDraw, epoch: float) -> None:
        w, h   = self.width, self.height
        ty     = h - self._tick_h
        draw.rectangle([0, ty, w - 1, h - 1], fill=_BG_TICK)
        draw.line([0, ty, w - 1, ty], fill=_YELLOW, width=2)

        # Build ticker text from alerts, fallback to last-updated timestamp
        ticker_items: list[str] = list(self._state.get("ticker", []))
        if not ticker_items:
            for alert in self._state.get("alerts", [])[:2]:
                evt = alert.get("event", "")
                headline = alert.get("headline", "")
                if evt or headline:
                    ticker_items.append(f"{evt}: {headline}" if evt and headline else evt or headline)
        if not ticker_items:
            updated = self._state.get("updated", "")
            if updated:
                try:
                    dt = datetime.fromisoformat(updated)
                    ticker_items = [f"Last updated: {self._apply_display_tz(dt).strftime('%b %d  %I:%M %p')}"]
                except Exception:
                    pass
        if not ticker_items:
            ticker_items = ["Weather data loading…"]

        ticker_text = "   •   ".join(ticker_items)
        text_w      = self._tw(draw, ticker_text, self._f_tiny)
        # Scroll at ~6 % of frame width per second
        speed = max(40, w * 6 // 100)
        cycle = max(1, (w + text_w) / speed)
        offset = (epoch % cycle) * speed
        x  = int(w - offset)
        text_h = self._th(draw, ticker_text, self._f_tiny)
        text_y = ty + (self._tick_h - text_h) // 2
        draw.text((x, text_y), ticker_text, font=self._f_tiny, fill=_WHITE)

    # ── Segment 0 – Current Conditions ───────────────────────────────────────

    def _draw_current_conditions(self, draw: ImageDraw.ImageDraw) -> None:
        w   = self.width
        by  = self._body_y
        bh  = self._body_h
        now = self._state.get("now", {})
        usym = self._unit_sym()

        # ── Left half: temperature + condition ───────────────────────────────
        left_cx = w * 5 // 16

        temp = now.get("temp")
        if temp is not None:
            temp_text = f"{temp}{usym}"
            tx = left_cx - self._tw(draw, temp_text, self._f_huge) // 2
            ty = by + bh * 10 // 100
            draw.text((tx, ty), temp_text, font=self._f_huge, fill=_YELLOW)

        icon_size = max(34, min(self.width // 8, bh * 24 // 100))
        self._draw_weather_icon(draw, str(now.get("icon", "cloudy")), left_cx, by + bh * 44 // 100, icon_size)

        condition = str(now.get("condition", "Not configured"))
        cy = by + bh * 67 // 100
        self._center(draw, left_cx, cy, condition, self._f_med, _WHITE)

        icon = now.get("icon", "")
        short = _ICON_SHORT.get(icon, "")
        if short:
            self._center(draw, left_cx, cy + self._th(draw, condition, self._f_med) + 6,
                         short, self._f_small, _LBLUE)

        # ── Vertical divider ─────────────────────────────────────────────────
        sep_x = w // 2
        draw.line([sep_x, by + 16, sep_x, by + bh - 16], fill=_BG_SEP, width=2)

        # ── Right half: detail rows ───────────────────────────────────────────
        rx    = sep_x + 24
        label_w = w * 20 // 100
        rows: list[tuple[str, str]] = []

        if now.get("feels_like") is not None:
            rows.append(("FEELS LIKE", f"{now['feels_like']}{usym}"))
        if now.get("humidity") is not None:
            rows.append(("HUMIDITY", f"{now['humidity']}%"))
        if now.get("wind"):
            rows.append(("WIND", str(now["wind"])))

        updated = self._state.get("updated", "")
        if updated:
            try:
                dt = datetime.fromisoformat(updated)
                rows.append(("UPDATED", self._apply_display_tz(dt).strftime("%I:%M %p")))
            except Exception:
                pass

        row_gap = bh * 20 // 100
        row_y   = by + bh * 15 // 100
        for label, value in rows:
            draw.text((rx, row_y), label + ":", font=self._f_small, fill=_LBLUE)
            draw.text((rx + label_w + 12, row_y), value, font=self._f_small, fill=_WHITE)
            row_y += row_gap

    # ── Segment 1 – 5-Day Forecast ────────────────────────────────────────────

    def _draw_five_day(self, draw: ImageDraw.ImageDraw) -> None:
        w  = self.width
        by = self._body_y
        bh = self._body_h

        title = "5-DAY FORECAST"
        self._center(draw, w // 2, by + 10, title, self._f_large, _YELLOW)

        five_day = self._state.get("five_day", [])
        if not five_day:
            self._center(draw, w // 2, by + bh // 2 - 20,
                         "Weather data not configured", self._f_med, _GRAY)
            return

        usym  = self._unit_sym()
        n     = min(5, len(five_day))
        col_w = w // n

        title_h = self._th(draw, title, self._f_large) + 20
        row_y   = by + title_h + bh * 8 // 100
        icon_size = max(28, min(col_w // 3, bh * 22 // 100))

        for i, day in enumerate(five_day[:n]):
            cx = i * col_w + col_w // 2

            # Column separator
            if i:
                draw.line([i * col_w, by + title_h, i * col_w, by + bh - 10],
                          fill=_BG_SEP, width=1)

            # Day label
            dow = str(day.get("dow", ""))
            self._center(draw, cx, row_y, dow, self._f_med, _YELLOW)
            icon_y = row_y + self._th(draw, dow, self._f_med) + icon_size // 2 + 8
            self._draw_weather_icon(draw, str(day.get("icon", "cloudy")), cx, icon_y, icon_size)

            # Condition (short)
            icon  = day.get("icon", "")
            short = _ICON_SHORT.get(icon, str(day.get("condition", ""))[:9])
            self._center(draw, cx,
                         icon_y + icon_size // 2 + 8,
                         short, self._f_tiny, _LBLUE)

            # Hi / Lo
            hi = day.get("hi")
            lo = day.get("lo")
            if hi is not None and lo is not None:
                hi_lo = f"{hi}{usym} / {lo}{usym}"
            else:
                hi_lo = "--"
            self._center(draw, cx,
                         row_y + bh * 52 // 100,
                         hi_lo, self._f_small, _WHITE)

    # ── Segment 4 – Extended Forecast (10-Day) ──────────────────────────────

    def _draw_extended_forecast(self, draw: ImageDraw.ImageDraw) -> None:
        w  = self.width
        by = self._body_y
        bh = self._body_h

        title = "EXTENDED FORECAST"
        self._center(draw, w // 2, by + 10, title, self._f_large, _YELLOW)

        extended = self._state.get("extended", [])
        # Show from the current day (index 0) through the next 10 days.
        days = extended[:10]
        if not days:
            self._center(draw, w // 2, by + bh // 2 - 20,
                         "Weather data not configured", self._f_med, _GRAY)
            return

        usym = self._unit_sym()
        n = min(10, len(days))
        # Use two rows of up to 5 columns each for a clean layout.
        cols_per_row = min(5, -(-n // 2))  # ceil(n/2), max 5
        rows = 2 if n > 5 else 1

        title_h   = self._th(draw, title, self._f_large) + 20
        avail_h   = bh - title_h - 10
        row_h     = avail_h // rows
        col_w     = w // cols_per_row
        icon_size = max(22, min(col_w // 3, row_h * 28 // 100))

        for i, day in enumerate(days[:n]):
            row_idx = i // cols_per_row
            col_idx = i % cols_per_row
            # Centre columns in last row when it has fewer entries.
            row_count = min(cols_per_row, n - row_idx * cols_per_row)
            row_start_x = (w - row_count * col_w) // 2
            cx = row_start_x + col_idx * col_w + col_w // 2
            row_y = by + title_h + row_idx * row_h

            # Column separator (skip first in each row)
            if col_idx:
                draw.line([row_start_x + col_idx * col_w,
                           row_y + 4,
                           row_start_x + col_idx * col_w,
                           row_y + row_h - 4],
                          fill=_BG_SEP, width=1)

            # Day + date label
            dow  = str(day.get("dow", ""))
            mmdd = str(day.get("date", ""))
            label = f"{dow} {mmdd}" if mmdd else dow
            self._center(draw, cx, row_y + 6, label, self._f_tiny, _YELLOW)

            lbl_h = self._th(draw, label, self._f_tiny) + 6
            icon_cy = row_y + lbl_h + icon_size // 2 + 4
            self._draw_weather_icon(draw, str(day.get("icon", "cloudy")), cx, icon_cy, icon_size)

            # Condition short label
            icon  = day.get("icon", "")
            short = _ICON_SHORT.get(icon, str(day.get("condition", ""))[:9])
            self._center(draw, cx,
                         icon_cy + icon_size // 2 + 4,
                         short, self._f_tiny, _LBLUE)

            # Hi / Lo
            hi = day.get("hi")
            lo = day.get("lo")
            hi_lo = f"{hi}{usym}/{lo}{usym}" if hi is not None and lo is not None else "--"
            self._center(draw, cx,
                         row_y + row_h - self._th(draw, hi_lo, self._f_tiny) - 6,
                         hi_lo, self._f_tiny, _WHITE)

    # ── Segment 2 – Regional Radar ───────────────────────────────────────────

    def _draw_regional_radar(self, draw: ImageDraw.ImageDraw, target_img: Image.Image) -> None:
        w  = self.width
        by = self._body_y
        bh = self._body_h

        title = "REGIONAL RADAR"
        self._center(draw, w // 2, by + 10, title, self._f_large, _YELLOW)

        radar_path = str(self._state.get("radar_image_path", "")).strip()
        title_h = self._th(draw, title, self._f_large) + 20
        img_x = 20
        img_y = by + title_h + 10
        img_w = max(100, w - 40)
        img_h = max(80, bh - title_h - 30)

        if radar_path:
            try:
                with Image.open(radar_path).convert("RGB") as radar_img:
                    if radar_img.size != (img_w, img_h):
                        radar_img = radar_img.resize((img_w, img_h), Image.Resampling.LANCZOS)
                    radar_frame = radar_img.copy()
                target_img.paste(radar_frame, (img_x, img_y))
                draw.rectangle([img_x, img_y, img_x + img_w, img_y + img_h], outline=_BG_SEP, width=2)
                return
            except Exception:
                pass

        self._center(draw, w // 2, by + bh // 2 - 20,
                     "Radar Unavailable", self._f_med, _ORANGE)
        self._center(draw, w // 2,
                     by + bh // 2 + self._th(draw, "Radar Unavailable", self._f_med) + 8,
                     "Waiting for local radar cache", self._f_small, _GRAY)

    # ── Segment 3 – Severe Weather Alerts ─────────────────────────────────────

    def _draw_alerts(self, draw: ImageDraw.ImageDraw) -> None:
        w  = self.width
        by = self._body_y
        bh = self._body_h

        alerts = self._state.get("alerts", [])

        if alerts:
            title = "⚠  ACTIVE WEATHER ALERTS"
            self._center(draw, w // 2, by + 10, title, self._f_large, _RED)

            y = by + self._th(draw, title, self._f_large) + 28
            for alert in alerts[:4]:
                event    = str(alert.get("event", ""))
                headline = str(alert.get("headline", ""))
                severity = str(alert.get("severity", ""))

                color = _RED if severity.lower() in {"extreme", "severe"} else _ORANGE
                if event:
                    draw.text((20, y), event, font=self._f_med, fill=color)
                    y += self._th(draw, event, self._f_med) + 4
                if headline:
                    # Word-wrap headline to ~80 chars
                    words   = headline.split()
                    line    = ""
                    for word in words:
                        if len(line) + len(word) + 1 > 78:
                            draw.text((28, y), line.rstrip(), font=self._f_small, fill=_LBLUE)
                            y += self._th(draw, line, self._f_small) + 2
                            line = word + " "
                        else:
                            line += word + " "
                    if line.strip():
                        draw.text((28, y), line.rstrip(), font=self._f_small, fill=_LBLUE)
                        y += self._th(draw, line, self._f_small) + 2
                y += 12
                if y > by + bh - 30:
                    break
        else:
            msg = "No Active Severe Weather Alerts"
            self._center(draw, w // 2, by + bh // 2 - 20,
                         msg, self._f_med, _GREEN)
            sub = self._state.get("location", "Local Area")
            self._center(draw, w // 2,
                         by + bh // 2 + self._th(draw, msg, self._f_med) + 8,
                         str(sub), self._f_small, _GRAY)

    # ── Public draw method ────────────────────────────────────────────────────

    def draw_frame(self, epoch_time: float) -> Image.Image:
        self._reload()

        img  = Image.new("RGB", (self.width, self.height), _BG)
        draw = ImageDraw.Draw(img)

        seg = self._current_segment(epoch_time)

        self._draw_header(draw, epoch_time)

        if seg == 0:
            self._draw_current_conditions(draw)
        elif seg == 1:
            self._draw_five_day(draw)
        elif seg == 2:
            self._draw_regional_radar(draw, img)
        elif seg == 3:
            self._draw_alerts(draw)
        else:
            self._draw_extended_forecast(draw)

        self._draw_ticker(draw, epoch_time)
        return img


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Weather channel frame renderer")
    parser.add_argument("--state",      required=True,  help="Path to weather state JSON file")
    parser.add_argument("--fps",        required=True,  type=int)
    parser.add_argument("--resolution", required=True,  help="WxH e.g. 1280x720")
    args = parser.parse_args()

    try:
        width, height = [int(v) for v in args.resolution.lower().split("x", 1)]
    except ValueError:
        print("weather_renderer: invalid --resolution (expected WxH)", file=sys.stderr)
        return 1

    state_path    = Path(args.state)
    renderer      = WeatherRenderer(state_path, width, height)
    frame_interval = 1.0 / max(1, args.fps)
    stop_event    = threading.Event()

    # Shared frame buffer (render thread produces, output thread consumes)
    _lock   = threading.Lock()
    _buffer: dict = {
        "frame":   Image.new("RGB", (width, height), _BG).tobytes(),
        "version": 0,
    }

    def _set(frame_bytes: bytes, version: int) -> None:
        with _lock:
            _buffer["frame"]   = frame_bytes
            _buffer["version"] = version

    def _get() -> tuple[bytes, int]:
        with _lock:
            return _buffer["frame"], _buffer["version"]

    exit_code = 0

    def _render_loop() -> None:
        nonlocal exit_code
        consecutive_errors = 0
        mono_to_wall       = time.time() - time.monotonic()
        next_due           = time.monotonic()
        version            = 0
        while not stop_event.is_set():
            sleep_for = next_due - time.monotonic()
            if sleep_for > 0:
                stop_event.wait(sleep_for)
                if stop_event.is_set():
                    return
            epoch = next_due + mono_to_wall
            try:
                frame = renderer.draw_frame(epoch_time=epoch)
                if frame.size != (width, height):
                    frame = frame.resize((width, height))
                version += 1
                _set(frame.tobytes(), version)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                traceback.print_exc(file=sys.stderr)
                if consecutive_errors >= 10:
                    print("weather_renderer: too many consecutive errors, exiting", file=sys.stderr)
                    exit_code = 1
                    stop_event.set()
                    return
            next_due = _advance_deadline(next_due, frame_interval, time.monotonic())

    def _output_loop() -> None:
        next_due = time.monotonic()
        while not stop_event.is_set():
            sleep_for = next_due - time.monotonic()
            if sleep_for > 0:
                stop_event.wait(sleep_for)
                if stop_event.is_set():
                    return
            try:
                frame_bytes, _ = _get()
                sys.stdout.buffer.write(frame_bytes)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                stop_event.set()
                return
            next_due = _advance_deadline(next_due, frame_interval, time.monotonic())

    render_thread = threading.Thread(target=_render_loop, name="wx-render")
    output_thread = threading.Thread(target=_output_loop, name="wx-output")
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
