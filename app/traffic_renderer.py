"""Native HLS renderer for the RSMC simulated Traffic virtual channel.

Uses the same cached OpenStreetMap basemap and road GeoJSON as the browser
preview.  Only congestion colours/incidents are simulated.
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
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

BASEMAP_ZOOM = 10
BASEMAP_W = 1280
BASEMAP_H = 720

NAVY = (9, 24, 104)
NAVY_DARK = (5, 12, 43)
BLUE = (18, 54, 190)
BLUE_2 = (28, 72, 218)
PANEL = (10, 40, 170)
PANEL_DARK = (7, 26, 105)
WHITE = (250, 252, 255)
PALE = (200, 216, 255)
YELLOW = (255, 215, 0)
GREEN = (34, 204, 68)
AMBER = (255, 187, 0)
RED = (238, 34, 34)
BORDER = (48, 88, 216)

FRAME_DEADLINE_EPSILON = 1e-9

def _advance_deadline(next_due: float, interval: float, now: float) -> float:
    """Use the same real-time frame deadline behavior as Guide/Weather."""
    candidate = next_due + interval
    if candidate <= now:
        overdue = now - candidate
        remainder = overdue % interval
        if (
            abs(remainder) < FRAME_DEADLINE_EPSILON
            or abs(interval - remainder) < FRAME_DEADLINE_EPSILON
        ):
            return now + interval
        return now + (interval - remainder)
    return candidate


def _font(size: int, bold: bool = False):
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _lonlat_to_pixel(lon: float, lat: float, city: dict, map_w: int, map_h: int) -> tuple[float, float]:
    n = 2 ** BASEMAP_ZOOM
    cx = (float(city['lon']) + 180.0) / 360.0 * n
    lat_r = math.radians(float(city['lat']))
    cy = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n

    px = (float(lon) + 180.0) / 360.0 * n
    lat_p = math.radians(float(lat))
    py = (1.0 - math.asinh(math.tan(lat_p)) / math.pi) / 2.0 * n

    scale_x = map_w / BASEMAP_W
    scale_y = map_h / BASEMAP_H
    return ((px - cx) * 256.0 * scale_x + map_w / 2.0,
            (py - cy) * 256.0 * scale_y + map_h / 2.0)


def _road_width(highway: str, scale: float) -> int:
    if highway == 'motorway':
        return max(3, round(6 * scale))
    if highway == 'trunk':
        return max(3, round(5 * scale))
    if highway == 'primary':
        return max(2, round(4 * scale))
    return max(2, round(3 * scale))


class TrafficRenderer:
    def __init__(self, state_path: Path, width: int, height: int):
        self.state_path = state_path
        self.w = width
        self.h = height
        self._mtime = -1.0
        self._frame: Image.Image | None = None
        self._clock_second = -1
        self._basemap_cache: dict[str, tuple[float, Image.Image]] = {}

        scale = height / 720.0
        self.f_title = _font(max(22, round(38 * scale)), True)
        self.f_city = _font(max(17, round(27 * scale)), True)
        self.f_box_title = _font(max(11, round(14 * scale)), True)
        self.f_level = _font(max(22, round(34 * scale)), True)
        self.f_body = _font(max(10, round(13 * scale)), True)
        self.f_small = _font(max(8, round(11 * scale)))
        self.f_tiny = _font(max(7, round(9 * scale)))

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {}


    @staticmethod
    def _display_time(data: dict[str, Any], epoch: float) -> datetime:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if str(data.get("timezone", "local")).strip().lower() == "utc":
            return dt.astimezone(timezone.utc)
        browser_timezone = str(data.get("browser_timezone", "")).strip()
        if browser_timezone:
            try:
                return dt.astimezone(ZoneInfo(browser_timezone))
            except Exception:
                pass
        return dt.astimezone()

    def _load_basemap(self, path: str, size: tuple[int, int]) -> Image.Image | None:
        if not path:
            return None
        p = Path(path)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return None
        cached = self._basemap_cache.get(str(p))
        if cached and cached[0] == mtime:
            img = cached[1]
        else:
            try:
                img = Image.open(p).convert('RGB')
                self._basemap_cache[str(p)] = (mtime, img.copy())
            except OSError:
                return None
        return img.resize(size, Image.Resampling.LANCZOS)

    def _panel(self, d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int = 5):
        d.rounded_rectangle(box, radius=radius, fill=PANEL, outline=BORDER, width=max(1, self.h // 360))

    def _render_map(self, im: Image.Image, data: dict, box: tuple[int, int, int, int]):
        x0, y0, x1, y1 = box
        title_h = max(26, round(self.h * 0.045))
        d = ImageDraw.Draw(im)
        self._panel(d, box)
        d.text((x0 + 12, y0 + 7), f"ROAD MAP — {data.get('city', {}).get('name', '')}, {data.get('city', {}).get('state', '')}",
               font=self.f_box_title, fill=YELLOW)
        d.line((x0, y0 + title_h, x1, y0 + title_h), fill=BORDER, width=1)

        map_x0, map_y0 = x0 + 2, y0 + title_h + 2
        map_x1, map_y1 = x1 - 2, y1 - 2
        map_w, map_h = max(1, map_x1 - map_x0), max(1, map_y1 - map_y0)

        basemap = self._load_basemap(str(data.get('basemap_path') or ''), (map_w, map_h))
        if basemap is None:
            basemap = Image.new('RGB', (map_w, map_h), (232, 229, 220))
        # Slightly darken/desaturate so congestion lines remain legible on TV.
        shade = Image.new('RGB', basemap.size, (18, 28, 58))
        basemap = Image.blend(basemap, shade, 0.17)
        im.paste(basemap, (map_x0, map_y0))

        roads = data.get('roads') or {}
        features = roads.get('features') if isinstance(roads, dict) else []
        features = features or []
        segments = data.get('segments') or []
        seg_colors = {str(s.get('id')): str(s.get('color') or 'green') for s in segments}
        color_map = {'green': GREEN, 'yellow': AMBER, 'red': RED}
        city = data.get('city') or {}
        # Draw roads on a map-sized transparent layer so off-screen geometry is
        # clipped to the map panel instead of bleeding into the header/sidebar.
        road_layer = Image.new('RGBA', (map_w, map_h), (0, 0, 0, 0))
        overlay = ImageDraw.Draw(road_layer)
        scale = map_w / BASEMAP_W

        for idx, feat in enumerate(features):
            geom = feat.get('geometry') or {}
            coords = geom.get('coordinates') or feat.get('c') or []
            if not isinstance(coords, list) or len(coords) < 2:
                continue
            props = feat.get('properties') or {}
            hw = props.get('highway') or feat.get('hw') or 'motorway'
            points = []
            for coord in coords:
                if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                    continue
                try:
                    px, py = _lonlat_to_pixel(coord[0], coord[1], city, map_w, map_h)
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                points.append((px, py))
            if len(points) < 2:
                continue
            seg_id = f"seg_{(idx % max(1, len(segments))) + 1}"
            clr = color_map.get(seg_colors.get(seg_id, 'green'), GREEN)
            width = _road_width(str(hw), scale)
            overlay.line(points, fill=(15, 20, 28, 255), width=width + max(2, width // 2), joint='curve')
            overlay.line(points, fill=(*clr, 255), width=width, joint='curve')

        im.paste(road_layer.convert('RGB'), (map_x0, map_y0), road_layer.getchannel('A'))

        # OSM attribution is intentionally retained in the rendered channel.
        credit = '© OpenStreetMap contributors'
        bb = d.textbbox((0, 0), credit, font=self.f_tiny)
        cx = map_x1 - (bb[2] - bb[0]) - 5
        cy = map_y1 - (bb[3] - bb[1]) - 5
        d.rectangle((cx - 4, cy - 2, map_x1 - 2, map_y1 - 2), fill=(0, 0, 0))
        d.text((cx, cy), credit, font=self.f_tiny, fill=(225, 225, 225))

    def _render(self, data: dict, epoch: float | None = None) -> Image.Image:
        epoch = time.time() if epoch is None else epoch
        im = Image.new('RGB', (self.w, self.h), NAVY)
        d = ImageDraw.Draw(im)
        header_h = max(58, round(self.h * 0.105))
        footer_h = max(42, round(self.h * 0.070))
        ticker_h = max(30, round(self.h * 0.055))

        # Header gradient approximation with stacked strips.
        d.rectangle((0, 0, self.w, header_h), fill=BLUE)
        d.rectangle((self.w // 3, 0, self.w * 2 // 3, header_h), fill=BLUE_2)
        d.line((0, header_h - 2, self.w, header_h - 2), fill=(74, 112, 255), width=3)
        title_x = round(self.w * 0.025)
        title_y = round(self.h * 0.019)
        title = 'TRAFFIC REPORT'
        d.text((title_x, title_y), title, font=self.f_title, fill=WHITE)

        # Position the SIMULATED badge relative to the actual rendered title
        # instead of using a fixed percentage.  The fixed position could touch
        # or overlap the title as font metrics/resolution changed.
        title_bb = d.textbbox((title_x, title_y), title, font=self.f_title)
        badge = 'SIMULATED'
        badge_bb = d.textbbox((0, 0), badge, font=self.f_small)
        badge_pad_x = max(7, round(self.w * 0.006))
        badge_pad_y = max(3, round(self.h * 0.004))
        badge_gap = max(16, round(self.w * 0.014))
        bx = title_bb[2] + badge_gap
        badge_h = (badge_bb[3] - badge_bb[1]) + (badge_pad_y * 2)
        by = max(4, title_y + ((title_bb[3] - title_bb[1]) - badge_h) // 2)
        badge_w = (badge_bb[2] - badge_bb[0]) + (badge_pad_x * 2)
        d.rounded_rectangle((bx, by, bx + badge_w, by + badge_h), radius=3, fill=YELLOW)
        d.text((bx + badge_pad_x, by + badge_pad_y - badge_bb[1]), badge, font=self.f_small, fill=NAVY_DARK)

        city = data.get('city') or {}
        city_label = f"{city.get('name', 'NO CITY')}, {city.get('state', '')}".rstrip(', ')
        right_margin = round(self.w * 0.025)
        bb = d.textbbox((0, 0), city_label, font=self.f_city)
        city_x = self.w - (bb[2] - bb[0]) - right_margin
        city_y = round(self.h * 0.018)
        d.text((city_x, city_y), city_label, font=self.f_city, fill=YELLOW)

        # Live clock, matching the active wall-clock treatment used by Guide/Weather.
        clock = self._display_time(data, epoch).strftime('%I:%M:%S %p').lstrip('0')
        clock_bb = d.textbbox((0, 0), clock, font=self.f_box_title)
        d.text((self.w - (clock_bb[2] - clock_bb[0]) - right_margin, round(self.h * 0.061)),
               clock, font=self.f_box_title, fill=PALE)

        if data.get('no_cities'):
            msg = 'NO TRAFFIC CITIES ENABLED'
            bb = d.textbbox((0, 0), msg, font=self.f_title)
            d.text(((self.w - (bb[2] - bb[0])) // 2, self.h // 2), msg, font=self.f_title, fill=YELLOW)
            return im

        body_top = header_h + round(self.h * 0.012)
        body_bottom = self.h - footer_h - ticker_h - round(self.h * 0.012)
        gap = max(8, round(self.w * 0.009))
        margin_x = round(self.w * 0.018)
        left_w = round(self.w * 0.245)
        left_box = (margin_x, body_top, margin_x + left_w, body_bottom)
        map_box = (left_box[2] + gap, body_top, self.w - margin_x, body_bottom)

        # Left information column, closely matching RetroIPTVGuide's traffic layout.
        lx0, ly0, lx1, ly1 = left_box
        summary = data.get('summary') or {}
        top_h = round((ly1 - ly0) * 0.36)
        mid_h = round((ly1 - ly0) * 0.19)
        self._panel(d, (lx0, ly0, lx1, ly0 + top_h))
        d.text((lx0 + 12, ly0 + 9), 'ROAD CONDITIONS', font=self.f_box_title, fill=YELLOW)
        level = str(summary.get('congestion_level', '--'))
        level_color = RED if level.lower() == 'heavy' else AMBER if level.lower() == 'moderate' else GREEN
        level_bb = d.textbbox((0, 0), level.upper(), font=self.f_level)
        d.text((lx0 + (left_w - (level_bb[2] - level_bb[0])) // 2, ly0 + round(top_h * 0.18)), level.upper(), font=self.f_level, fill=level_color)
        d.text((lx0 + 12, ly0 + round(top_h * 0.49)), 'SIMULATED CONDITIONS', font=self.f_small, fill=PALE)
        gp = int(summary.get('green_percent', 0) or 0)
        yp = int(summary.get('yellow_percent', 0) or 0)
        rp = int(summary.get('red_percent', 0) or 0)
        bar_x0, bar_x1 = lx0 + 12, lx1 - 12
        bar_y0 = ly0 + round(top_h * 0.61)
        bar_y1 = bar_y0 + max(8, round(self.h * 0.016))
        total_w = bar_x1 - bar_x0
        gx = bar_x0 + round(total_w * gp / 100)
        yx = gx + round(total_w * yp / 100)
        d.rectangle((bar_x0, bar_y0, gx, bar_y1), fill=GREEN)
        d.rectangle((gx, bar_y0, yx, bar_y1), fill=AMBER)
        d.rectangle((yx, bar_y0, bar_x1, bar_y1), fill=RED)
        row_y = bar_y1 + 8
        for name, pct, clr in [('FREE FLOW', gp, GREEN), ('SLOW', yp, AMBER), ('HEAVY', rp, RED)]:
            d.ellipse((lx0 + 13, row_y + 3, lx0 + 21, row_y + 11), fill=clr)
            d.text((lx0 + 28, row_y), name, font=self.f_small, fill=PALE)
            pct_txt = f'{pct}%'
            pbb = d.textbbox((0, 0), pct_txt, font=self.f_small)
            d.text((lx1 - 12 - (pbb[2] - pbb[0]), row_y), pct_txt, font=self.f_small, fill=YELLOW)
            row_y += max(17, round(self.h * 0.026))

        mid_y0 = ly0 + top_h + gap
        self._panel(d, (lx0, mid_y0, lx1, mid_y0 + mid_h))
        d.text((lx0 + 12, mid_y0 + 9), 'ACTIVE ALERTS', font=self.f_box_title, fill=YELLOW)
        count = int(summary.get('incident_count', len(data.get('incidents') or [])) or 0)
        count_txt = str(count)
        cbb = d.textbbox((0, 0), count_txt, font=self.f_level)
        d.text((lx0 + 18, mid_y0 + round(mid_h * 0.38)), count_txt, font=self.f_level, fill=WHITE)
        d.text((lx0 + 28 + (cbb[2] - cbb[0]), mid_y0 + round(mid_h * 0.43)), 'SIMULATED\nINCIDENTS', font=self.f_small, fill=PALE, spacing=2)

        inc_y0 = mid_y0 + mid_h + gap
        self._panel(d, (lx0, inc_y0, lx1, ly1))
        d.text((lx0 + 12, inc_y0 + 9), 'INCIDENT LOG', font=self.f_box_title, fill=YELLOW)
        y = inc_y0 + max(35, round(self.h * 0.052))
        sev_colors = {'red': RED, 'yellow': AMBER, 'green': GREEN}
        for inc in (data.get('incidents') or [])[:5]:
            clr = sev_colors.get(str(inc.get('severity')), AMBER)
            d.rectangle((lx0 + 10, y, lx0 + 13, y + max(28, round(self.h * 0.048))), fill=clr)
            title = str(inc.get('title') or 'Incident')[:28]
            road = f"{inc.get('road', '')} {inc.get('direction', '')}".strip()[:32]
            d.text((lx0 + 20, y), title, font=self.f_body, fill=WHITE)
            d.text((lx0 + 20, y + max(14, round(self.h * 0.021))), road, font=self.f_small, fill=PALE)
            y += max(38, round(self.h * 0.060))
            if y > ly1 - 35:
                break

        self._render_map(im, data, map_box)

        # Disclaimer and ticker bars.
        disclaimer_y = self.h - footer_h - ticker_h
        d.rectangle((0, disclaimer_y, self.w, disclaimer_y + footer_h), fill=(0, 0, 0))
        disclaimer = str(data.get('disclaimer') or 'SIMULATED TRAFFIC — Conditions are synthetic and not real traffic data.')
        if len(disclaimer) > 180:
            disclaimer = disclaimer[:177] + '...'
        dbb = d.textbbox((0, 0), disclaimer, font=self.f_tiny)
        d.text(((self.w - (dbb[2] - dbb[0])) // 2, disclaimer_y + max(5, (footer_h - (dbb[3]-dbb[1])) // 2)), disclaimer,
               font=self.f_tiny, fill=YELLOW)

        ticker_y = self.h - ticker_h
        d.rectangle((0, ticker_y, self.w, self.h), fill=NAVY_DARK)
        label_w = round(self.w * 0.11)
        d.rectangle((0, ticker_y, label_w, self.h), fill=YELLOW)
        d.text((round(label_w * 0.14), ticker_y + max(4, round(ticker_h * 0.23))), 'TRAFFIC', font=self.f_body, fill=NAVY_DARK)
        ticker_parts = [f"CONDITIONS: {level.upper()}", f"GREEN {gp}%", f"YELLOW {yp}%", f"RED {rp}%"]
        for inc in (data.get('incidents') or [])[:2]:
            ticker_parts.append(f"{inc.get('road','')} {inc.get('direction','')}: {inc.get('title','')}")
        ticker = '  •  '.join(ticker_parts)
        d.text((label_w + 14, ticker_y + max(4, round(ticker_h * 0.23))), ticker[:150], font=self.f_body, fill=YELLOW)
        return im

    def frame(self, epoch_time: float | None = None) -> Image.Image:
        # Match Guide/Weather: derive display time from the renderer's
        # wall-clock-aligned frame deadline rather than a free-running time.time()
        # call on every output write.
        now = time.time() if epoch_time is None else epoch_time
        second = int(now)
        try:
            mtime = self.state_path.stat().st_mtime
        except OSError:
            mtime = -1.0
        if self._frame is None or mtime != self._mtime or second != self._clock_second:
            data = self._read_state()
            self._frame = self._render(data, now)
            self._mtime = mtime
            self._clock_second = second
        return self._frame


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', required=True)
    ap.add_argument('--fps', type=float, default=10)
    ap.add_argument('--resolution', default='1280x720')
    args = ap.parse_args()
    try:
        w, h = map(int, args.resolution.lower().split('x', 1))
    except ValueError:
        w, h = 1280, 720

    renderer = TrafficRenderer(Path(args.state), w, h)
    frame_interval = 1.0 / max(1.0, args.fps)
    stop_event = threading.Event()
    latest_frame_lock = threading.Lock()
    shared = {'frame': Image.new('RGB', (w, h), '#000000').tobytes(), 'version': 0}
    exit_code = 0

    def _set(frame_bytes: bytes, version: int) -> None:
        with latest_frame_lock:
            shared['frame'] = frame_bytes
            shared['version'] = version

    def _get() -> tuple[bytes, int]:
        with latest_frame_lock:
            return shared['frame'], int(shared['version'])

    # Same two-loop clock/output model as Guide and Weather.  Rendering uses a
    # monotonic deadline mapped to wall clock, and output independently emits at
    # exactly the configured FPS.  A slow frame is reused rather than causing
    # the clock/video timeline to run fast while catching up.
    def _render_loop() -> None:
        nonlocal exit_code
        mono_to_wall = time.time() - time.monotonic()
        next_due = time.monotonic()
        version = 0
        consecutive_errors = 0
        while not stop_event.is_set():
            sleep_for = next_due - time.monotonic()
            if sleep_for > 0:
                stop_event.wait(sleep_for)
                if stop_event.is_set():
                    return
            epoch = next_due + mono_to_wall
            try:
                frame = renderer.frame(epoch_time=epoch)
                if frame.size != (w, h):
                    frame = frame.resize((w, h))
                version += 1
                _set(frame.tobytes(), version)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                traceback.print_exc(file=sys.stderr)
                if consecutive_errors >= 10:
                    print('traffic_renderer: too many consecutive errors, exiting', file=sys.stderr)
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

    render_thread = threading.Thread(target=_render_loop, name='traffic-render')
    output_thread = threading.Thread(target=_output_loop, name='traffic-output')
    render_thread.start()
    output_thread.start()
    try:
        while render_thread.is_alive() and output_thread.is_alive():
            render_thread.join(timeout=0.05)
            output_thread.join(timeout=0.05)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        render_thread.join()
        output_thread.join()
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
