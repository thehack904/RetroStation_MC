#!/usr/bin/env python3
"""Generate static basemap PNGs for the RetroStation MC simulated traffic channel.

Run this script ONCE from any machine that has internet access (your laptop,
home PC, etc.).  Cloud/server IPs are often rate-limited by OSM tile servers,
so running this locally is more reliable.

Usage
-----
    # From the repository root:
    pip install Pillow requests
    python scripts/generate_basemaps.py

    # The PNG files will be saved to:
    #   data/maps/traffic/<cityslug>.png

Each PNG is 1280×720, stitched from OpenStreetMap tiles at zoom level 10.
The script tries several OSM tile mirrors if the primary server is unavailable.
A placeholder basemap (map-grid with city name) is generated for any city
whose tiles cannot be fetched.

Adapted from RetroIPTVGuide v4.9.9-dev scripts/generate_basemaps.py.
Source: RetroIPTVGuide project
"""

import io
import logging
import math
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

try:
    import requests
except ImportError:
    sys.exit("ERROR: 'requests' is not installed.  Run: pip install requests")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("ERROR: 'Pillow' is not installed.  Run: pip install Pillow")

# ── Configuration ──────────────────────────────────────────────────────────────

ZOOM      = 10
OUT_W     = 1280
OUT_H     = 720
TILE_SIZE = 256

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "data", "maps", "traffic"))

TILE_SERVERS = [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "https://b.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "https://c.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
]

UA = (
    "RetroStation-MC/1.0 basemap generator "
    "(RetroStation MC; map data helper)"
)

# Cities — must match _TRAFFIC_DEMO_CITIES_SEED in app/traffic_channel.py
CITIES = [
    {"name": "New York City", "lat": 40.7128, "lon": -74.0060},
    {"name": "Los Angeles",   "lat": 34.0522, "lon": -118.2437},
    {"name": "Chicago",       "lat": 41.8781, "lon": -87.6298},
    {"name": "Houston",       "lat": 29.7604, "lon": -95.3698},
    {"name": "Phoenix",       "lat": 33.4484, "lon": -112.0740},
    {"name": "Philadelphia",  "lat": 39.9526, "lon": -75.1652},
    {"name": "San Antonio",   "lat": 29.4241, "lon": -98.4936},
    {"name": "San Diego",     "lat": 32.7157, "lon": -117.1611},
    {"name": "Dallas",        "lat": 32.7767, "lon": -96.7970},
    {"name": "San Jose",      "lat": 37.3382, "lon": -121.8863},
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def city_slug(name: str) -> str:
    """Lowercase alphanumeric slug — mirrors city_slug() in app/traffic_channel.py."""
    return "".join(c for c in name.lower() if c.isalnum())


def lat_lon_to_tile_float(lat: float, lon: float, zoom: int):
    n   = 2 ** zoom
    x   = (lon + 180.0) / 360.0 * n
    y   = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def fetch_tile(tx: int, ty: int, zoom: int, session: requests.Session) -> Image.Image:
    """Download a single 256×256 tile, trying each server in turn."""
    last_exc = None
    for srv in TILE_SERVERS:
        url = srv.format(z=zoom, x=tx, y=ty)
        for attempt in range(2):
            try:
                resp = session.get(url, timeout=20)
                resp.raise_for_status()
                return Image.open(io.BytesIO(resp.content)).convert("RGB")
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2)
    raise RuntimeError(
        f"All {len(TILE_SERVERS)} tile servers failed for {zoom}/{tx}/{ty}: {last_exc}"
    ) from last_exc


def generate_placeholder(city_name: str, out_path: str) -> bool:
    """Generate a placeholder PNG with a map-grid background and city name."""
    w, h   = OUT_W, OUT_H
    BG     = (242, 239, 233)
    MAJOR  = (200, 196, 187)
    MINOR  = (220, 216, 208)
    LABEL  = (130, 120, 100)

    img  = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    for x in range(0, w, 64):
        draw.line([(x, 0), (x, h)], fill=MINOR, width=1)
    for y in range(0, h, 64):
        draw.line([(0, y), (w, y)], fill=MINOR, width=1)
    for x in range(0, w, 256):
        draw.line([(x, 0), (x, h)], fill=MAJOR, width=2)
    for y in range(0, h, 256):
        draw.line([(0, y), (w, y)], fill=MAJOR, width=2)

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    font = None
    for fp in font_candidates:
        try:
            font = ImageFont.truetype(fp, 36)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), city_name, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) // 2, (h - th) // 2), city_name, fill=LABEL, font=font)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    img.save(tmp, "PNG", optimize=True)
    os.replace(tmp, out_path)
    return True


def generate_basemap(lat: float, lon: float, out_path: str, session: requests.Session) -> bool:
    """Stitch OSM tiles into a {OUT_W}×{OUT_H} PNG centred on (lat, lon)."""
    n          = 2 ** ZOOM
    cx_f, cy_f = lat_lon_to_tile_float(lat, lon, ZOOM)
    cx_t       = int(cx_f)
    cy_t       = int(cy_f)
    off_x      = (cx_f - cx_t) * TILE_SIZE
    off_y      = (cy_f - cy_t) * TILE_SIZE

    half_w = OUT_W / 2
    half_h = OUT_H / 2

    x0 = cx_t - math.ceil((half_w - (TILE_SIZE - off_x)) / TILE_SIZE) - 1
    y0 = cy_t - math.ceil((half_h - (TILE_SIZE - off_y)) / TILE_SIZE) - 1
    x1 = cx_t + math.ceil((half_w - off_x) / TILE_SIZE) + 1
    y1 = cy_t + math.ceil((half_h - off_y) / TILE_SIZE) + 1

    cols   = x1 - x0 + 1
    rows   = y1 - y0 + 1
    canvas = Image.new("RGB", (cols * TILE_SIZE, rows * TILE_SIZE))
    any_ok = False

    total = cols * rows
    done  = 0
    for row_i, ty in enumerate(range(y0, y1 + 1)):
        for col_i, tx in enumerate(range(x0, x1 + 1)):
            done += 1
            tx_c = max(0, min(n - 1, tx))
            ty_c = max(0, min(n - 1, ty))
            try:
                tile = fetch_tile(tx_c, ty_c, ZOOM, session)
                canvas.paste(tile, (col_i * TILE_SIZE, row_i * TILE_SIZE))
                any_ok = True
                time.sleep(0.2)
            except Exception as exc:
                logging.warning("  tile (%s,%s) failed: %s", tx_c, ty_c, exc)
            print(f"  {done}/{total} tiles", end="\r", flush=True)

    print()
    if not any_ok:
        return False

    cx_canvas = (cx_t - x0) * TILE_SIZE + off_x
    cy_canvas = (cy_t - y0) * TILE_SIZE + off_y
    left    = int(cx_canvas - half_w)
    top     = int(cy_canvas - half_h)
    cropped = canvas.crop((left, top, left + OUT_W, top + OUT_H))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    cropped.save(tmp, "PNG", optimize=True)
    os.replace(tmp, out_path)
    return True


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    total_cities = len(CITIES)
    generated    = 0
    skipped      = 0
    failed       = 0

    for i, city in enumerate(CITIES, 1):
        slug     = city_slug(city["name"])
        out_path = os.path.normpath(os.path.join(OUT_DIR, f"{slug}.png"))

        if os.path.isfile(out_path):
            logging.info(
                "[%d/%d] SKIP %s — already exists (%s)",
                i, total_cities, city["name"], out_path,
            )
            skipped += 1
            continue

        logging.info(
            "[%d/%d] Generating %s → %s …", i, total_cities, city["name"], f"{slug}.png"
        )
        ok = False
        try:
            ok = generate_basemap(city["lat"], city["lon"], out_path, session)
        except Exception as exc:
            logging.exception("  ✗ Exception for %s: %s", city["name"], exc)

        if ok:
            size_kb = os.path.getsize(out_path) // 1024
            logging.info("  ✓ Saved %s (%d KB)", out_path, size_kb)
            generated += 1
        else:
            logging.warning(
                "  ✗ Tile download failed for %s — generating placeholder …", city["name"]
            )
            try:
                generate_placeholder(city["name"], out_path)
                logging.info("  ✓ Saved placeholder for %s", city["name"])
                generated += 1
            except Exception as exc:
                logging.exception("  ✗ Placeholder also failed for %s: %s", city["name"], exc)
                failed += 1

        time.sleep(1)

    print()
    print(f"Done.  Generated: {generated}  Skipped: {skipped}  Failed: {failed}")
    if failed:
        print("Some cities failed — check the warnings above and re-run the script.")
    else:
        print(f"All PNGs are in:  {os.path.normpath(OUT_DIR)}")
        print("The app will detect them automatically at /traffic-map/<slug>.png")


if __name__ == "__main__":
    main()
