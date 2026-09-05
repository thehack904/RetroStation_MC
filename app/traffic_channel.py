"""Simulated Traffic virtual channel for RetroStation MC.

All traffic conditions (congestion levels, incidents) are **synthetically
generated** for retro TV presentation purposes.  No real-time or live
traffic data is used at any point.  Road geometry (GeoJSON) and basemap
images are sourced from OpenStreetMap; only the traffic conditions overlaid
on that geometry are simulated.

Ported and adapted from RetroIPTVGuide v4.9.9-dev:
  - ``_TRAFFIC_DEMO_CITIES_SEED`` and city-configuration helpers
  - ``_generate_demo_incidents()`` synthetic incident generation
  - ``_get_congestion_distribution()`` time-of-day/weekend simulation model
  - ``build_traffic_payload()`` deterministic rotation/congestion generation
  - Road-geometry retrieval/cache helpers and Overpass-to-GeoJSON conversion
  - Basemap generation/cache helpers

Differences from the RetroIPTVGuide implementation:
  - No Flask/DB dependency.  City lists and channel settings are passed in
    as plain dicts so the module can be tested independently.
  - Road cache is keyed by city slug (alphanumeric name) rather than a
    DB row integer, and is stored under ``DATA_DIR/roads_cache/``.
  - Basemaps are stored under ``DATA_DIR/maps/traffic/``.
  - ``build_traffic_payload()`` accepts the city list and channel config as
    arguments instead of fetching them from a database.
"""
from __future__ import annotations

import hashlib
import io as _io
import json as _json
import logging
import math as _math
import os
import random as _random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image as _PilImage
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILLOW_AVAILABLE = False

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

# ── Paths ─────────────────────────────────────────────────────────────────────

_MODULE_DIR   = Path(__file__).resolve().parent
_REPO_DIR     = _MODULE_DIR.parent
DATA_DIR      = _REPO_DIR / "data"
ROADS_CACHE_DIR  = DATA_DIR / "roads_cache"
BASEMAP_DIR      = DATA_DIR / "maps" / "traffic"

# Pre-downloaded bundled road GeoJSON files (shipped with the repository via
# ``scripts/download_road_data.py``).  Stored under ``data/roads/``.
ROADS_BUNDLED_DIR = DATA_DIR / "roads"

# ── Disclaimer ────────────────────────────────────────────────────────────────

CHANNEL_DISCLAIMER = (
    "SIMULATED TRAFFIC — Congestion levels and incidents are synthetically "
    "generated for demonstration purposes only and do not reflect real-time "
    "road conditions.  Do not use for navigation."
)

# ── City seed data ────────────────────────────────────────────────────────────

# US cities with population > 1,000,000 (2024 Census estimates, city proper).
# Ported directly from RetroIPTVGuide v4.9.9-dev.
_TRAFFIC_DEMO_CITIES_SEED: list[dict] = [
    {"name": "New York City", "state": "NY", "lat": 40.7128, "lon": -74.0060,  "population": 8258035},
    {"name": "Los Angeles",   "state": "CA", "lat": 34.0522, "lon": -118.2437, "population": 3898747},
    {"name": "Chicago",       "state": "IL", "lat": 41.8781, "lon": -87.6298,  "population": 2696555},
    {"name": "Houston",       "state": "TX", "lat": 29.7604, "lon": -95.3698,  "population": 2304580},
    {"name": "Phoenix",       "state": "AZ", "lat": 33.4484, "lon": -112.0740, "population": 1608139},
    {"name": "Philadelphia",  "state": "PA", "lat": 39.9526, "lon": -75.1652,  "population": 1550542},
    {"name": "San Antonio",   "state": "TX", "lat": 29.4241, "lon": -98.4936,  "population": 1434625},
    {"name": "San Diego",     "state": "CA", "lat": 32.7157, "lon": -117.1611, "population": 1386932},
    {"name": "Dallas",        "state": "TX", "lat": 32.7767, "lon": -96.7970,  "population": 1304379},
    {"name": "San Jose",      "state": "CA", "lat": 37.3382, "lon": -121.8863, "population": 1013240},
]

# Per-city highway/arterial names for realistic-looking synthetic incidents.
# Ported from RetroIPTVGuide v4.9.9-dev.
_CITY_HIGHWAYS: dict[str, list[str]] = {
    "New York City": ["I-95", "I-278", "I-495", "FDR Drive", "Belt Pkwy", "Cross Bronx Expwy"],
    "Los Angeles":   ["I-5", "I-10", "I-405", "US-101", "SR-110", "SR-60"],
    "Chicago":       ["I-90", "I-94", "I-290", "I-55", "I-88", "Lake Shore Dr"],
    "Houston":       ["I-10", "I-45", "I-610", "US-59", "US-290", "Beltway 8"],
    "Phoenix":       ["I-10", "I-17", "SR-51", "SR-101", "US-60", "Loop 202"],
    "Philadelphia":  ["I-95", "I-76", "I-676", "US-1", "PA-309", "Schuylkill Expwy"],
    "San Antonio":   ["I-10", "I-35", "I-37", "US-281", "Loop 410", "Loop 1604"],
    "San Diego":     ["I-5", "I-8", "I-15", "SR-94", "SR-163", "SR-125"],
    "Dallas":        ["I-30", "I-35E", "I-635", "US-75", "SR-114", "Loop 12"],
    "San Jose":      ["I-280", "I-880", "SR-87", "SR-101", "US-101", "SR-85"],
}
_CITY_HIGHWAYS_DEFAULT = ["I-10", "I-20", "I-40", "US-1", "State Hwy 1", "Main Blvd"]

_INCIDENT_TYPES = [
    ("Accident",            "red",    "\u26a0"),
    ("Multi-vehicle crash", "red",    "\u26a0"),
    ("Stalled vehicle",     "yellow", "\U0001f698"),
    ("Road work",           "yellow", "\U0001f6a7"),
    ("Debris on road",      "yellow", "\u26a0"),
    ("Slow traffic",        "green",  "\U0001f422"),
    ("Lane closure",        "yellow", "\U0001f6a7"),
    ("Emergency response",  "red",    "\U0001f6a8"),
]
_DIRECTIONS = ["Northbound", "Southbound", "Eastbound", "Westbound"]

# ── In-memory caches ──────────────────────────────────────────────────────────

_PAYLOAD_CACHE: dict = {}         # cache_key -> payload dict
_PAYLOAD_CACHE_TTL = 120          # seconds

_ROADS_CACHE: dict = {}           # city_slug -> GeoJSON FeatureCollection
_ROADS_CACHE_TIME: dict = {}      # city_slug -> fetch timestamp
_ROADS_CACHE_TTL  = 86400         # 24-hour in-memory TTL
_ROADS_DISK_TTL   = 2_592_000     # 30-day disk TTL

_OVERPASS_SEMAPHORE    = threading.Semaphore(1)
_OVERPASS_MAX_RETRIES  = 3
_OVERPASS_RETRY_BACKOFF_S = 10
_OVERPASS_LAST_ERROR: dict = {}   # diagnostic info from last Overpass failure

# ── Basemap constants ─────────────────────────────────────────────────────────

BASEMAP_ZOOM  = 10
BASEMAP_W     = 1280
BASEMAP_H     = 720
_TILE_SIZE    = 256
_OSM_TILE_UA  = (
    "RetroStation-MC/1.0 (simulated-traffic basemap; "
    "RetroStation MC project)"
)
_TILE_SERVERS = [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "https://b.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "https://c.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
]

# ── City helpers ──────────────────────────────────────────────────────────────


def city_slug(name: str) -> str:
    """Lowercase alphanumeric slug used for file cache keys and template JS.

    Must stay in sync with the JavaScript ``citySlug()`` function in
    ``app/templates/traffic.html``.
    """
    return "".join(c for c in name.lower() if c.isalnum())


# Keep the private alias used throughout this module.
_city_slug = city_slug


def seed_cities() -> list[dict]:
    """Return a fresh copy of the default city list with ids assigned."""
    return [
        {
            "id":         i + 1,
            "name":       c["name"],
            "state":      c["state"],
            "lat":        c["lat"],
            "lon":        c["lon"],
            "population": c["population"],
            "enabled":    True,
            "weight":     1,
        }
        for i, c in enumerate(_TRAFFIC_DEMO_CITIES_SEED)
    ]


# ── Simulation engine ─────────────────────────────────────────────────────────


def _generate_demo_incidents(
    city_name: str,
    rng: _random.Random,
    red_pct: int,
) -> list[dict]:
    """Return a deterministic list of synthetic traffic incidents.

    Count and severity scale with congestion level.  No real-time data is used.
    Ported from RetroIPTVGuide v4.9.9-dev.
    """
    highways = _CITY_HIGHWAYS.get(city_name, _CITY_HIGHWAYS_DEFAULT)
    max_incidents = 3 + (red_pct // 15)   # 3–7 depending on red_pct
    count = rng.randint(max(1, max_incidents - 2), max_incidents)

    incidents = []
    used_roads: set = set()
    for _ in range(count):
        road      = rng.choice(highways)
        direction = rng.choice(_DIRECTIONS)
        inc_type, severity, icon = rng.choice(_INCIDENT_TYPES)
        key = (road, direction)
        if key in used_roads and len(used_roads) < len(highways) * 4:
            alts = [h for h in highways if h != road] or highways
            road = rng.choice(alts)
            key  = (road, direction)
        used_roads.add(key)
        incidents.append(
            {
                "title":     inc_type,
                "severity":  severity,
                "icon":      icon,
                "road":      road,
                "direction": direction,
            }
        )
    return incidents


def _get_congestion_distribution(hour: int, is_weekend: bool = False) -> tuple[int, int, int]:
    """Return ``(green_pct, yellow_pct, red_pct)`` for the given local hour.

    Percentages always sum to 100.  Simulates higher congestion during
    weekday rush hours and lighter traffic overnight and on weekends.
    Ported from RetroIPTVGuide v4.9.9-dev.
    """
    if is_weekend:
        if 2 <= hour < 5:
            return 90, 8, 2
        elif 9 <= hour < 12:
            return 70, 20, 10
        elif 12 <= hour < 15:
            return 65, 25, 10
        elif 17 <= hour < 20:
            return 60, 28, 12
        else:
            return 82, 13, 5
    else:
        if 2 <= hour < 5:
            return 90, 8, 2
        elif 7 <= hour < 9:
            return 50, 30, 20
        elif 12 <= hour < 14:
            return 65, 25, 10
        elif 16 <= hour < 19:
            return 45, 30, 25
        elif 22 <= hour or hour < 1:
            return 80, 15, 5
        else:
            return 75, 18, 7


def build_traffic_payload(
    cities: list[dict],
    cfg: dict,
) -> dict:
    """Build a deterministic simulated traffic payload for the current rotation slot.

    Parameters
    ----------
    cities:
        List of city dicts (id, name, state, lat, lon, enabled, weight).
        Disabled cities are filtered out; an empty enabled set returns
        ``{'no_cities': True}``.
    cfg:
        Channel configuration dict with keys:
          ``rotation_mode``      – ``"admin_rotation"`` (default) or ``"random_pack"``
          ``rotation_seconds``   – seconds per city slot (default 120)
          ``pack``               – JSON list of city ids for random_pack mode

    Returns a payload dict with ``demo_mode: True`` in every case so callers
    can verify that the data is **simulated** and must never be presented as live.
    Ported and adapted from RetroIPTVGuide v4.9.9-dev.
    """
    rotation_seconds = max(30, int(cfg.get("rotation_seconds", 120)))
    mode = cfg.get("rotation_mode", "admin_rotation")

    now_ts   = time.time()
    time_slot = int(now_ts // rotation_seconds)

    if mode == "random_pack":
        try:
            pack_ids = _json.loads(cfg.get("pack", "[]") or "[]")
        except Exception:
            pack_ids = []
        if pack_ids:
            id_set   = {c["id"] for c in cities}
            pack_ids = [pid for pid in pack_ids if pid in id_set]
            pool     = [c for c in cities if c["id"] in pack_ids and c.get("enabled", True)]
        else:
            pool = [c for c in cities if c.get("enabled", True)]
    else:
        pool = [c for c in cities if c.get("enabled", True)]

    if not pool:
        return {"no_cities": True}

    weighted: list[dict] = []
    for city in pool:
        w = max(1, int(city.get("weight", 1)))
        weighted.extend([city] * w)

    city = weighted[time_slot % len(weighted)]

    cache_key = f"demo:{city['id']}:{time_slot}"
    cached = _PAYLOAD_CACHE.get(cache_key)
    if cached:
        return cached

    # Evict stale cache entries
    for k in list(_PAYLOAD_CACHE):
        if k != cache_key:
            _PAYLOAD_CACHE.pop(k, None)

    now_dt     = datetime.now(timezone.utc)
    hour       = now_dt.hour
    is_weekend = now_dt.weekday() >= 5
    green_pct, yellow_pct, red_pct = _get_congestion_distribution(hour, is_weekend)

    if red_pct >= 20:
        congestion_level = "Heavy"
    elif red_pct >= 10 or yellow_pct >= 25:
        congestion_level = "Moderate"
    else:
        congestion_level = "Light"

    # MD5 is used purely for deterministic seeding (not for security).
    seed_hex = hashlib.md5(f"{city['id']}:{time_slot}".encode()).hexdigest()[:8]
    rng      = _random.Random(int(seed_hex, 16))

    NUM_SEGMENTS = 24
    colors = (
        ["green"]  * green_pct
        + ["yellow"] * yellow_pct
        + ["red"]    * red_pct
    )
    segments = [
        {"id": f"seg_{i}", "color": rng.choice(colors)}
        for i in range(1, NUM_SEGMENTS + 1)
    ]

    total         = len(segments)
    actual_green  = sum(1 for s in segments if s["color"] == "green")
    actual_yellow = sum(1 for s in segments if s["color"] == "yellow")
    actual_red    = sum(1 for s in segments if s["color"] == "red")

    incidents = _generate_demo_incidents(city["name"], rng, red_pct)

    payload: dict[str, Any] = {
        "updated":   now_dt.isoformat(),
        "city": {
            "id":    city["id"],
            "name":  city["name"],
            "state": city["state"],
            "lat":   city["lat"],
            "lon":   city["lon"],
        },
        "time_slot": time_slot,
        "summary": {
            "congestion_level": congestion_level,
            "green_percent":    round(actual_green  * 100 / total),
            "yellow_percent":   round(actual_yellow * 100 / total),
            "red_percent":      round(actual_red    * 100 / total),
            "incident_count":   len(incidents),
        },
        "incidents": incidents,
        "segments":  segments,
        "demo_mode": True,
        "disclaimer": CHANNEL_DISCLAIMER,
    }
    _PAYLOAD_CACHE[cache_key] = payload
    return payload


# ── Road geometry cache ───────────────────────────────────────────────────────


def _roads_cache_path(slug: str) -> Path:
    """Return a validated path for the on-disk road GeoJSON cache.

    The slug is sanitised to alphanumeric characters to prevent any path
    traversal via a crafted city name.
    """
    safe_slug = "".join(c for c in slug if c.isalnum() or c in ("-", "_"))
    if not safe_slug:
        raise ValueError(f"city slug {slug!r} produced an empty safe name")
    safe_dir = ROADS_CACHE_DIR.resolve()
    path     = (safe_dir / f"{safe_slug}.json").resolve()
    if not str(path).startswith(str(safe_dir) + os.sep):
        raise ValueError(f"slug {slug!r} would escape the cache directory")
    return path


def _load_roads_from_disk(slug: str) -> dict | None:
    """Load GeoJSON from the disk cache if it exists and is not stale."""
    try:
        path = _roads_cache_path(slug)
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > _ROADS_DISK_TTL:
            return None
        with path.open("r", encoding="utf-8") as fh:
            return _json.load(fh)
    except ValueError:
        return None
    except Exception:
        logging.warning("_load_roads_from_disk: failed for slug=%s", slug, exc_info=True)
        return None


def _save_roads_to_disk(slug: str, geojson: dict) -> None:
    """Persist GeoJSON to disk so restarts skip the Overpass call."""
    try:
        path = _roads_cache_path(slug)
    except ValueError:
        logging.warning("_save_roads_to_disk: refusing unsafe slug=%s", slug)
        return
    try:
        ROADS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            _json.dump(geojson, fh)
    except Exception:
        logging.warning("_save_roads_to_disk: failed for slug=%s", slug, exc_info=True)


def _load_bundled_roads(city_name: str) -> dict | None:
    """Load a pre-downloaded GeoJSON file from ``data/roads/<slug>.geojson``.

    These files are committed to the repository via
    ``scripts/download_road_data.py`` so the channel can display real road
    geometry offline.
    """
    slug = _city_slug(city_name)
    path = ROADS_BUNDLED_DIR / f"{slug}.geojson"
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as fh:
            data = _json.load(fh)
        return data if data.get("features") else None
    except Exception:
        logging.warning("_load_bundled_roads: failed for %s", city_name, exc_info=True)
        return None


def _overpass_to_geojson(raw: dict) -> dict:
    """Convert raw Overpass JSON to a GeoJSON FeatureCollection.

    Ported from RetroIPTVGuide v4.9.9-dev.
    """
    nodes: dict = {}
    ways: list  = []
    for el in raw.get("elements", []):
        t = el.get("type")
        if t == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])
        elif t == "way":
            ways.append(el)

    features = []
    for way in ways:
        coords = [nodes[nid] for nid in way.get("nodes", []) if nid in nodes]
        if len(coords) < 2:
            continue
        tags = way.get("tags", {})
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "way_id":  way["id"],
                    "name":    tags.get("name", ""),
                    "highway": tags.get("highway", ""),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _fetch_overpass_roads(lat: float, lon: float, radius_m: int = 80_467) -> dict:
    """Fetch motorway/trunk road geometry from the Overpass API.

    Returns an empty FeatureCollection on network failure so callers can
    always proceed without crashing the channel.
    Ported from RetroIPTVGuide v4.9.9-dev with User-Agent updated for RSMC.
    """
    global _OVERPASS_LAST_ERROR  # noqa: PLW0603
    if not _REQUESTS_AVAILABLE:
        return {"elements": []}

    query = (
        f'[out:json][timeout:60];'
        f'(way["highway"~"^(motorway|trunk)$"]'
        f'(around:{radius_m},{lat},{lon}););'
        f'out body;>;out skel qt;'
    )
    with _OVERPASS_SEMAPHORE:
        backoff = _OVERPASS_RETRY_BACKOFF_S
        for attempt in range(_OVERPASS_MAX_RETRIES + 1):
            try:
                resp = _requests.post(
                    "https://overpass-api.de/api/interpreter",
                    data={"data": query},
                    timeout=65,
                    headers={
                        "User-Agent": (
                            "RetroStation-MC/1.0 (simulated-traffic; "
                            "RetroStation MC project)"
                        )
                    },
                )
                if resp.status_code == 429 and attempt < _OVERPASS_MAX_RETRIES:
                    wait = int(resp.headers.get("Retry-After", backoff))
                    logging.warning(
                        "_fetch_overpass_roads: 429 rate-limited (attempt %d/%d), sleeping %ds",
                        attempt + 1, _OVERPASS_MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                _OVERPASS_LAST_ERROR = {}
                return resp.json()
            except Exception as exc:
                if attempt < _OVERPASS_MAX_RETRIES:
                    logging.warning(
                        "_fetch_overpass_roads: error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, _OVERPASS_MAX_RETRIES, backoff, exc,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                logging.exception("_fetch_overpass_roads failed for lat=%s lon=%s", lat, lon)
                _OVERPASS_LAST_ERROR = {
                    "lat": lat, "lon": lon, "ts": time.time(), "message": str(exc),
                }
    return {"elements": []}


def get_road_geojson(city: dict) -> dict:
    """Return cached road GeoJSON for a city.

    Lookup order:
      1. In-memory cache (24 h TTL)
      2. Disk cache (30-day TTL)
      3. Bundled static files (``data/roads/<slug>.geojson``)
      4. Overpass API (network; only when no local copy exists)

    Never raises; returns an empty FeatureCollection on complete failure.
    Adapted from RetroIPTVGuide v4.9.9-dev ``get_traffic_demo_roads()``.
    """
    slug   = _city_slug(city["name"])
    now_ts = time.time()

    cached_at = _ROADS_CACHE_TIME.get(slug, 0)
    if slug in _ROADS_CACHE and (now_ts - cached_at) < _ROADS_CACHE_TTL:
        return _ROADS_CACHE[slug]

    disk = _load_roads_from_disk(slug)
    if disk is not None:
        _ROADS_CACHE[slug]      = disk
        _ROADS_CACHE_TIME[slug] = now_ts
        return disk

    bundled = _load_bundled_roads(city["name"])
    if bundled is not None:
        _ROADS_CACHE[slug]      = bundled
        _ROADS_CACHE_TIME[slug] = now_ts
        return bundled

    raw     = _fetch_overpass_roads(city["lat"], city["lon"])
    geojson = _overpass_to_geojson(raw)
    _ROADS_CACHE[slug]      = geojson
    _ROADS_CACHE_TIME[slug] = now_ts
    if geojson["features"]:
        _save_roads_to_disk(slug, geojson)
    return geojson


# ── Basemap PNG generation ────────────────────────────────────────────────────


def _generate_basemap_png(lat: float, lon: float, out_path: Path | str) -> bool:
    """Stitch OSM tiles into a BASEMAP_W × BASEMAP_H PNG centred on lat/lon.

    Returns True on success, False if Pillow is unavailable or all tiles fail.
    Written atomically (temp file → rename).
    Ported from RetroIPTVGuide v4.9.9-dev.
    """
    if not _PILLOW_AVAILABLE or not _REQUESTS_AVAILABLE:
        return False

    out_path = Path(out_path)
    zoom = BASEMAP_ZOOM
    n    = 2 ** zoom

    cx_f   = (lon + 180.0) / 360.0 * n
    lat_r  = _math.radians(lat)
    cy_f   = (1.0 - _math.asinh(_math.tan(lat_r)) / _math.pi) / 2.0 * n

    cx_t   = int(cx_f)
    cy_t   = int(cy_f)
    off_x  = (cx_f - cx_t) * _TILE_SIZE
    off_y  = (cy_f - cy_t) * _TILE_SIZE

    half_w = BASEMAP_W / 2
    half_h = BASEMAP_H / 2

    x0 = cx_t - _math.ceil((half_w - (_TILE_SIZE - off_x)) / _TILE_SIZE) - 1
    y0 = cy_t - _math.ceil((half_h - (_TILE_SIZE - off_y)) / _TILE_SIZE) - 1
    x1 = cx_t + _math.ceil((half_w - off_x) / _TILE_SIZE) + 1
    y1 = cy_t + _math.ceil((half_h - off_y) / _TILE_SIZE) + 1

    cols   = x1 - x0 + 1
    rows   = y1 - y0 + 1
    canvas = _PilImage.new("RGB", (cols * _TILE_SIZE, rows * _TILE_SIZE))

    sess = _requests.Session()
    sess.headers.update({"User-Agent": _OSM_TILE_UA})
    any_ok     = False
    server_idx = 0

    for row_i, ty in enumerate(range(y0, y1 + 1)):
        for col_i, tx in enumerate(range(x0, x1 + 1)):
            tx_c = max(0, min(n - 1, tx))
            ty_c = max(0, min(n - 1, ty))
            fetched = False
            for attempt in range(len(_TILE_SERVERS) * 2):
                srv = _TILE_SERVERS[server_idx % len(_TILE_SERVERS)]
                url = srv.format(z=zoom, x=tx_c, y=ty_c)
                try:
                    resp = sess.get(url, timeout=15)
                    resp.raise_for_status()
                    tile = _PilImage.open(_io.BytesIO(resp.content)).convert("RGB")
                    canvas.paste(tile, (col_i * _TILE_SIZE, row_i * _TILE_SIZE))
                    any_ok  = True
                    fetched = True
                    time.sleep(0.15)
                    break
                except Exception as exc:
                    logging.debug("_generate_basemap_png: tile %s/%s attempt %s failed: %s",
                                  tx_c, ty_c, attempt + 1, exc)
                    server_idx += 1
                    if attempt < len(_TILE_SERVERS) * 2 - 1:
                        time.sleep(min(2 ** (attempt % len(_TILE_SERVERS)), 30))
            if not fetched:
                logging.warning("_generate_basemap_png: all servers failed for %s/%s", tx_c, ty_c)
            server_idx += 1

    if not any_ok:
        return False

    cx_canvas = (cx_t - x0) * _TILE_SIZE + off_x
    cy_canvas = (cy_t - y0) * _TILE_SIZE + off_y
    left    = int(cx_canvas - half_w)
    top     = int(cy_canvas - half_h)
    cropped = canvas.crop((left, top, left + BASEMAP_W, top + BASEMAP_H))

    BASEMAP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    cropped.save(str(tmp), "PNG", optimize=True)
    tmp.replace(out_path)
    return True


def _generate_placeholder_basemap_png(city_name: str, out_path: Path | str) -> bool:
    """Generate a map-inspired placeholder PNG using Pillow only (no network).

    Provides a usable basemap background even when OSM tile servers are
    unreachable.  Ported from RetroIPTVGuide v4.9.9-dev.
    """
    if not _PILLOW_AVAILABLE:
        return False

    from PIL import ImageDraw, ImageFont

    out_path = Path(out_path)
    w, h = BASEMAP_W, BASEMAP_H

    BG_LAND    = (242, 239, 233)
    GRID_MAJOR = (200, 196, 187)
    GRID_MINOR = (220, 216, 208)
    LABEL_CLR  = (130, 120, 100)

    img  = _PilImage.new("RGB", (w, h), BG_LAND)
    draw = ImageDraw.Draw(img)

    for x in range(0, w, 64):
        draw.line([(x, 0), (x, h)], fill=GRID_MINOR, width=1)
    for y in range(0, h, 64):
        draw.line([(0, y), (w, y)], fill=GRID_MINOR, width=1)
    for x in range(0, w, 256):
        draw.line([(x, 0), (x, h)], fill=GRID_MAJOR, width=2)
    for y in range(0, h, 256):
        draw.line([(0, y), (w, y)], fill=GRID_MAJOR, width=2)

    _font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    font = None
    for fp in _font_candidates:
        try:
            font = ImageFont.truetype(fp, 36)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), city_name, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) // 2, (h - th) // 2), city_name, fill=LABEL_CLR, font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    img.save(str(tmp), "PNG", optimize=True)
    tmp.replace(out_path)
    return True


def ensure_basemap(city_name: str, lat: float, lon: float) -> Path | None:
    """Ensure a basemap PNG exists for a city, generating it if absent.

    Tries real OSM tiles first; falls back to a placeholder if tile download
    fails.  Returns the path to the PNG on success, None if unavailable.
    """
    slug     = _city_slug(city_name)
    out_path = BASEMAP_DIR / f"{slug}.png"
    if out_path.is_file():
        return out_path

    BASEMAP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ok = _generate_basemap_png(lat, lon, out_path)
        if ok:
            return out_path
    except Exception:
        logging.exception("ensure_basemap: tile download failed for %s", city_name)

    try:
        ok = _generate_placeholder_basemap_png(city_name, out_path)
        if ok:
            return out_path
    except Exception:
        logging.exception("ensure_basemap: placeholder failed for %s", city_name)

    return None


def prewarm_basemaps(cities: list[dict] | None = None) -> None:
    """Background-safe: generate missing basemap PNGs for all seed cities.

    Adapted from RetroIPTVGuide v4.9.9-dev ``_prewarm_basemaps()``.
    """
    if not _PILLOW_AVAILABLE:
        return
    targets = cities if cities is not None else _TRAFFIC_DEMO_CITIES_SEED
    BASEMAP_DIR.mkdir(parents=True, exist_ok=True)
    for city in targets:
        slug     = _city_slug(city["name"])
        out_path = BASEMAP_DIR / f"{slug}.png"
        if out_path.is_file():
            continue
        logging.info("prewarm_basemaps: generating for %s", city["name"])
        try:
            ok = _generate_basemap_png(city["lat"], city["lon"], out_path)
            if ok:
                logging.info("prewarm_basemaps: saved OSM basemap for %s", slug)
                time.sleep(1)
                continue
        except Exception:
            logging.exception("prewarm_basemaps: tile exception for %s", city["name"])
        try:
            ok = _generate_placeholder_basemap_png(city["name"], out_path)
            if ok:
                logging.info("prewarm_basemaps: saved placeholder for %s", slug)
        except Exception:
            logging.exception("prewarm_basemaps: placeholder exception for %s", city["name"])


def prewarm_roads_cache(cities: list[dict]) -> None:
    """Background-safe: warm road geometry cache for all enabled cities.

    Adapted from RetroIPTVGuide v4.9.9-dev ``_prewarm_roads_cache()``.
    """
    enabled = [c for c in cities if c.get("enabled", True)]
    last_overpass = 0.0
    stagger = 12  # seconds between Overpass requests

    for city in enabled:
        slug = _city_slug(city["name"])
        needs_overpass = (
            slug not in _ROADS_CACHE
            and _load_roads_from_disk(slug) is None
            and _load_bundled_roads(city["name"]) is None
        )
        if needs_overpass:
            elapsed   = time.time() - last_overpass
            remaining = stagger - elapsed
            if remaining > 0:
                time.sleep(remaining)
        try:
            get_road_geojson(city)
            logging.info("prewarm_roads_cache: cached roads for %s", city["name"])
        except Exception:
            logging.exception("prewarm_roads_cache: failed for %s", city["name"])
        if needs_overpass:
            last_overpass = time.time()
