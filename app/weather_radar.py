from __future__ import annotations

import io
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

BASE_DIR = Path(__file__).resolve().parent.parent
WEATHER_REGIONS_DIR = BASE_DIR / "runtime" / "weather" / "regions"

DEFAULT_RADIUS_MILES = 60
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 450
DEFAULT_REFRESH_SECONDS = 300

NOAA_RADAR_WMS_ENDPOINT = "https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows"
CONUS_FALLBACK_BBOX = [-126.0, 24.0, -66.0, 50.0]

# OpenStreetMap tile servers — same sources used by the RetroIPTVGuide Virtual Traffic Channel.
# Multiple mirrors are tried in order to improve reliability from cloud/datacenter hosts that
# may be rate-limited by the primary tile.openstreetmap.org server.
OSM_TILE_SERVERS = [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "https://b.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "https://c.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
]
_TILE_SIZE = 256  # standard OSM slippy-map tile size in pixels
_OSM_TILE_UA = (
    "RetroStation-MC/1.0 (weather radar basemap; "
    "see RetroStation MC project)"
)

_USER_AGENT = "RetroStation-MC/1.0"
_LOG = logging.getLogger(__name__)
_FALLBACK_BASEMAP_METADATA_KEY = "retrostation_basemap_kind"
_FALLBACK_HEADER_COLOR = (8, 12, 24)
_FALLBACK_BODY_COLOR = (16, 22, 42)
_FALLBACK_GRID_COLOR = (38, 58, 96)
_FALLBACK_BOX_COLOR = (90, 120, 180)
_FALLBACK_HEADER_SAMPLE_Y = 20
_FALLBACK_BODY_SAMPLE_X = 5
_FALLBACK_BODY_SAMPLE_OFFSET_Y = 20
_FALLBACK_FOOTER_SAMPLE_X = 10
_FALLBACK_HEADER_HEIGHT = 40
_FALLBACK_GRID_COLUMNS = 8
_FALLBACK_GRID_ROWS = 6


def bbox_for_16x9(
    lat: float,
    lon: float,
    radius_miles: int = DEFAULT_RADIUS_MILES,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> list[float]:
    """Return a 16:9 bbox in west/south/east/north order centered on lat/lon."""
    miles_per_degree_lat = 69.0
    miles_per_degree_lon = max(1e-6, 69.0 * math.cos(math.radians(lat)))

    aspect = width / max(1, height)

    lat_radius = float(radius_miles)
    lon_radius = float(radius_miles) * aspect

    lat_delta = lat_radius / miles_per_degree_lat
    lon_delta = lon_radius / miles_per_degree_lon

    return [
        round(lon - lon_delta, 5),
        round(lat - lat_delta, 5),
        round(lon + lon_delta, 5),
        round(lat + lat_delta, 5),
    ]


def region_cache_key(region_identifier: str, radius_miles: int, width: int, height: int) -> str:
    return f"{_slugify_region_id(region_identifier)}/radar_{int(radius_miles)}mi_{int(width)}x{int(height)}"


def load_region_config(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_noaa_radar_url(region: dict[str, Any]) -> str:
    bbox = region.get("bbox") or CONUS_FALLBACK_BBOX
    width = int(region.get("width") or DEFAULT_WIDTH)
    height = int(region.get("height") or DEFAULT_HEIGHT)
    bbox_str = ",".join(str(v) for v in bbox)
    return (
        f"{NOAA_RADAR_WMS_ENDPOINT}"
        f"?service=WMS"
        f"&version=1.1.1"
        f"&request=GetMap"
        f"&layers=conus_bref_qcd"
        f"&styles="
        f"&bbox={bbox_str}"
        f"&width={width}"
        f"&height={height}"
        f"&srs=EPSG:4326"
        f"&format=image/png"
        f"&transparent=true"
    )


def _zoom_for_bbox(lon_span_deg: float, width_px: int) -> int:
    """Return the OSM zoom level that best matches the required pixel density.

    Computes the zoom such that the stitched tiles provide enough resolution to
    cover *lon_span_deg* degrees at *width_px* pixels wide.
    Clamped to the range [1, 12] to avoid excessively fine or coarse tiles.
    """
    z_float = math.log2(
        max(1.0, (width_px / _TILE_SIZE) * (360.0 / max(0.001, lon_span_deg)))
    )
    return max(1, min(12, int(z_float)))


def _lat_lon_to_tile_float(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Convert a geographic coordinate to fractional OSM tile coordinates."""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _stitch_basemap_from_tiles(region: dict[str, Any], destination: Path) -> None:
    """Download and stitch OSM slippy-map tiles into a basemap PNG.

    Uses the same tile servers as the RetroIPTVGuide Virtual Traffic Channel.
    The zoom level is derived automatically from the region's bounding box and
    output dimensions.  Tiles are fetched from multiple mirrors in round-robin
    order; blank tiles are substituted when all servers fail for a given tile so
    that a partial basemap is still produced rather than raising an error.
    The PNG is written atomically (temp file → rename).
    """
    bbox = region.get("bbox") or CONUS_FALLBACK_BBOX
    width = int(region.get("width") or DEFAULT_WIDTH)
    height = int(region.get("height") or DEFAULT_HEIGHT)
    lat = float(region.get("lat") or ((bbox[1] + bbox[3]) / 2))
    lon = float(region.get("lon") or ((bbox[0] + bbox[2]) / 2))

    lon_span = bbox[2] - bbox[0]
    zoom = _zoom_for_bbox(lon_span, width)
    n = 2 ** zoom

    cx_f, cy_f = _lat_lon_to_tile_float(lat, lon, zoom)
    cx_tile = int(cx_f)
    cy_tile = int(cy_f)
    off_x = (cx_f - cx_tile) * _TILE_SIZE
    off_y = (cy_f - cy_tile) * _TILE_SIZE

    half_w = width / 2
    half_h = height / 2

    tiles_left = math.ceil((half_w - (_TILE_SIZE - off_x)) / _TILE_SIZE) + 1
    tiles_right = math.ceil((half_w - off_x) / _TILE_SIZE) + 1
    tiles_up = math.ceil((half_h - (_TILE_SIZE - off_y)) / _TILE_SIZE) + 1
    tiles_down = math.ceil((half_h - off_y) / _TILE_SIZE) + 1

    x0 = cx_tile - tiles_left
    y0 = cy_tile - tiles_up
    x1 = cx_tile + tiles_right
    y1 = cy_tile + tiles_down
    cols = x1 - x0 + 1
    rows = y1 - y0 + 1

    canvas = Image.new("RGB", (cols * _TILE_SIZE, rows * _TILE_SIZE))
    session = requests.Session()
    session.headers.update({"User-Agent": _OSM_TILE_UA})
    any_ok = False
    server_idx = 0

    for row_i, ty in enumerate(range(y0, y1 + 1)):
        for col_i, tx in enumerate(range(x0, x1 + 1)):
            tx_c = max(0, min(n - 1, tx))
            ty_c = max(0, min(n - 1, ty))
            fetched = False
            for attempt in range(len(OSM_TILE_SERVERS) * 2):
                srv = OSM_TILE_SERVERS[server_idx % len(OSM_TILE_SERVERS)]
                url = srv.format(z=zoom, x=tx_c, y=ty_c)
                try:
                    resp = session.get(url, timeout=15)
                    resp.raise_for_status()
                    tile = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    canvas.paste(tile, (col_i * _TILE_SIZE, row_i * _TILE_SIZE))
                    any_ok = True
                    fetched = True
                    time.sleep(0.15)  # respect tile server fair-use policy
                    break
                except Exception as exc:
                    _LOG.debug(
                        "OSM tile %s %d/%d attempt %d failed: %s",
                        srv, tx_c, ty_c, attempt + 1, exc,
                    )
                    server_idx += 1
                    if attempt < len(OSM_TILE_SERVERS) * 2 - 1:
                        time.sleep(min(2 ** (attempt % len(OSM_TILE_SERVERS)), 30))
            if not fetched:
                _LOG.warning("All OSM tile servers failed for %d/%d — leaving blank", tx_c, ty_c)
            server_idx += 1  # distribute load across servers between tiles

    if not any_ok:
        raise RuntimeError("No OSM tiles could be fetched for weather basemap")

    center_x = (cx_tile - x0) * _TILE_SIZE + off_x
    center_y = (cy_tile - y0) * _TILE_SIZE + off_y
    left = int(center_x - half_w)
    top = int(center_y - half_h)
    cropped = canvas.crop((left, top, left + width, top + height))

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    cropped.save(tmp_path, "PNG")
    tmp_path.replace(destination)


def create_or_update_weather_region(
    *,
    lat: str | float,
    lon: str | float,
    location_name: str = "",
    zip_code: str = "",
    radius_miles: int = DEFAULT_RADIUS_MILES,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
) -> dict[str, Any]:
    lat_f = float(lat)
    lon_f = float(lon)
    zip_token = _extract_zip(zip_code) or _extract_zip(location_name)
    region_identifier = zip_token or (location_name.strip() or f"{lat_f:.4f}_{lon_f:.4f}")
    cache_key = region_cache_key(region_identifier, radius_miles, width, height)
    region_dir = WEATHER_REGIONS_DIR / cache_key
    region_dir.mkdir(parents=True, exist_ok=True)

    bbox = bbox_for_16x9(lat_f, lon_f, radius_miles=radius_miles, width=width, height=height)

    region = {
        "zip": zip_token or "",
        "name": location_name.strip() or "Local Weather",
        "lat": lat_f,
        "lon": lon_f,
        "radius_miles": int(radius_miles),
        "bbox": bbox,
        "width": int(width),
        "height": int(height),
        "basemap": _repo_relative(region_dir / "basemap.png"),
        "radar_overlay": _repo_relative(region_dir / "radar_overlay.png"),
        "radar_final": _repo_relative(region_dir / "radar_final.png"),
        "radar_refresh_seconds": int(refresh_seconds),
        "region_key": region_identifier,
    }

    region_path = region_dir / "region.json"
    existing = load_region_config(region_path)
    if existing != region:
        region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")
        _LOG.info("Weather region cache updated")

    ensure_basemap_cached(region)
    return region


def ensure_basemap_cached(region: dict[str, Any]) -> Path:
    path = _path_from_region(region, "basemap")
    expected_size = (int(region["width"]), int(region["height"]))
    if validate_basemap(path, expected_size):
        return path
    return download_or_generate_basemap(region)


def download_or_generate_basemap(region: dict[str, Any]) -> Path:
    path = _path_from_region(region, "basemap")
    path.parent.mkdir(parents=True, exist_ok=True)
    expected_size = (int(region["width"]), int(region["height"]))
    had_cached_real_basemap = validate_basemap(path, expected_size)
    try:
        _stitch_basemap_from_tiles(region, path)
        if not validate_basemap(path, expected_size):
            raise ValueError("Stitched basemap failed validation")
        _LOG.info("Weather basemap cached (OSM tiles)")
        return path
    except Exception as exc:
        if had_cached_real_basemap:
            _LOG.warning("Basemap tile stitching failed (%s); keeping existing cached basemap", exc)
            return path
        _LOG.warning("Basemap tile stitching failed (%s); generating fallback basemap", exc)
        _generate_fallback_basemap(region, path)
        return path


def validate_basemap(path: Path, expected_size: tuple[int, int]) -> bool:
    try:
        if not path.exists():
            return False
        with Image.open(path) as img:
            if img.size != expected_size:
                return False
            return not _looks_like_fallback_basemap(img)
    except Exception:
        return False


def download_radar_overlay(region: dict[str, Any]) -> Path:
    path = _path_from_region(region, "radar_overlay")
    path.parent.mkdir(parents=True, exist_ok=True)
    _download_image(build_noaa_radar_url(region), path)
    return path


def radar_is_stale(region: dict[str, Any], now_utc: datetime | None = None) -> bool:
    now_utc = now_utc or datetime.now(timezone.utc)
    refresh_seconds = int(region.get("radar_refresh_seconds") or DEFAULT_REFRESH_SECONDS)
    final_path = _path_from_region(region, "radar_final")
    refresh_meta = _load_refresh_meta(region)
    last_success = _parse_utc(refresh_meta.get("last_success_utc"))

    if not final_path.exists() or last_success is None:
        return True
    return (now_utc - last_success).total_seconds() >= refresh_seconds


def composite_radar(region: dict[str, Any]) -> Path:
    basemap_path = ensure_basemap_cached(region)
    overlay_path = _path_from_region(region, "radar_overlay")
    final_path = _path_from_region(region, "radar_final")
    final_path.parent.mkdir(parents=True, exist_ok=True)

    expected_size = (int(region["width"]), int(region["height"]))

    with Image.open(basemap_path) as basemap_src, Image.open(overlay_path) as overlay_src:
        basemap = basemap_src.convert("RGBA")
        overlay = overlay_src.convert("RGBA")
    if basemap.size != expected_size:
        basemap = basemap.resize(expected_size, Image.Resampling.LANCZOS)
    if overlay.size != expected_size:
        overlay = overlay.resize(expected_size, Image.Resampling.LANCZOS)

    try:
        final = Image.alpha_composite(basemap, overlay)
        draw = ImageDraw.Draw(final)
        width, _ = expected_size
        title = f"{region.get('name') or 'Local'} Radar"
        stamp = _utc_now_z()

        draw.rectangle((0, 0, width, 40), fill=(0, 0, 0, 180))
        font = ImageFont.load_default()
        draw.text((12, 12), title, fill=(255, 255, 255, 255), font=font)
        draw.text((max(12, width - 150), 12), stamp, fill=(255, 255, 255, 255), font=font)

        final.convert("RGB").save(final_path, "PNG")
    finally:
        basemap.close()
        overlay.close()

    return final_path


def write_fallback_radar_image(region: dict[str, Any], message: str = "RADAR TEMPORARILY UNAVAILABLE") -> Path:
    final_path = _path_from_region(region, "radar_final")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    width = int(region.get("width") or DEFAULT_WIDTH)
    height = int(region.get("height") or DEFAULT_HEIGHT)

    img = Image.new("RGB", (width, height), (14, 24, 60))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    title = f"{(region.get('name') or 'LOCAL').upper()} RADAR"
    draw.rectangle((0, 0, width, 42), fill=(0, 0, 0))
    draw.text((14, 14), title, fill=(255, 255, 255), font=font)

    message_w = draw.textlength(message, font=font)
    draw.text((int((width - message_w) / 2), int(height / 2 - 10)), message, fill=(255, 210, 80), font=font)
    draw.text((14, height - 26), _utc_now_z(), fill=(200, 200, 200), font=font)

    img.save(final_path, "PNG")
    return final_path


def refresh_radar_if_stale(region: dict[str, Any]) -> Path:
    final_path = _path_from_region(region, "radar_final")
    refresh_meta = _load_refresh_meta(region)
    now_utc = datetime.now(timezone.utc)

    if not radar_is_stale(region, now_utc=now_utc):
        return final_path

    retry_backoff = min(60, int(region.get("radar_refresh_seconds") or DEFAULT_REFRESH_SECONDS))
    last_attempt = _parse_utc(refresh_meta.get("last_attempt_utc"))
    if last_attempt is not None and (now_utc - last_attempt).total_seconds() < retry_backoff:
        if final_path.exists():
            return final_path

    try:
        ensure_basemap_cached(region)
        download_radar_overlay(region)
        final = composite_radar(region)
        _write_refresh_meta(
            region,
            {
                "last_success_utc": _utc_iso(now_utc),
                "last_attempt_utc": _utc_iso(now_utc),
                "source": "NOAA OpenGeo WMS",
                "refresh_seconds": int(region.get("radar_refresh_seconds") or DEFAULT_REFRESH_SECONDS),
            },
        )
        _LOG.info("Radar refresh complete")
        return final
    except Exception as exc:
        _LOG.warning("Radar refresh failed: %s", exc)
        _write_refresh_meta(
            region,
            {
                "last_attempt_utc": _utc_iso(now_utc),
                "source": "NOAA OpenGeo WMS",
                "refresh_seconds": int(region.get("radar_refresh_seconds") or DEFAULT_REFRESH_SECONDS),
                "error": str(exc),
            },
        )
        if final_path.exists():
            return final_path
        return write_fallback_radar_image(region)


def _download_image(url: str, destination: Path) -> None:
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": _USER_AGENT, "Accept": "image/png,image/*;q=0.8,*/*;q=0.2"},
    )
    response.raise_for_status()

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "image" not in content_type:
        snippet = response.text[:120] if response.text else ""
        raise ValueError(f"Expected image response but received {content_type or 'unknown'}: {snippet!r}")

    blob = response.content
    with Image.open(io.BytesIO(blob)) as img:
        img.load()

    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    tmp_path.write_bytes(blob)
    tmp_path.replace(destination)


def _generate_fallback_basemap(region: dict[str, Any], destination: Path) -> None:
    width = int(region.get("width") or DEFAULT_WIDTH)
    height = int(region.get("height") or DEFAULT_HEIGHT)
    bbox = region.get("bbox") or CONUS_FALLBACK_BBOX

    img = Image.new("RGB", (width, height), (16, 22, 42))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    draw.rectangle((0, 0, width, 40), fill=_FALLBACK_HEADER_COLOR)
    draw.text((12, 12), f"{region.get('name', 'Local')} basemap", fill=(220, 230, 255), font=font)

    for i in range(1, 8):
        x = int(width * (i / 8))
        draw.line((x, 40, x, height), fill=_FALLBACK_GRID_COLOR, width=1)
    for j in range(1, 6):
        y = int(40 + (height - 40) * (j / 6))
        draw.line((0, y, width, y), fill=_FALLBACK_GRID_COLOR, width=1)

    draw.rectangle((10, height - 54, width - 10, height - 10), outline=_FALLBACK_BOX_COLOR, width=1)
    draw.text(
        (16, height - 44),
        f"BBOX W/S/E/N: {bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f}",
        fill=(200, 210, 240),
        font=font,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text(_FALLBACK_BASEMAP_METADATA_KEY, "fallback")
    img.save(destination, "PNG", pnginfo=pnginfo)


def _looks_like_fallback_basemap(img: Image.Image) -> bool:
    if (img.info or {}).get(_FALLBACK_BASEMAP_METADATA_KEY) == "fallback":
        return True

    rgb = img.convert("RGB")
    width, height = rgb.size
    if width < 32 or height < 96:
        return False

    body_top = _FALLBACK_HEADER_HEIGHT if height > _FALLBACK_HEADER_HEIGHT else 0
    body_height = max(1, height - body_top)
    header_sample_y = min(_FALLBACK_HEADER_HEIGHT - 1, _FALLBACK_HEADER_SAMPLE_Y)
    header_sample_y = min(height - 1, header_sample_y)
    grid_x = min(width - 1, int(width * (1 / _FALLBACK_GRID_COLUMNS)))
    grid_y = min(height - 1, int(body_top + body_height * (1 / _FALLBACK_GRID_ROWS)))
    footer_y = height - 54
    body_sample_pos = (
        min(width - 1, _FALLBACK_BODY_SAMPLE_X),
        min(height - 1, body_top + _FALLBACK_BODY_SAMPLE_OFFSET_Y),
    )
    footer_sample_pos = (
        min(width - 1, _FALLBACK_FOOTER_SAMPLE_X),
        min(height - 1, footer_y),
    )
    samples = (
        ((width // 2, header_sample_y), _FALLBACK_HEADER_COLOR),
        (body_sample_pos, _FALLBACK_BODY_COLOR),
        ((grid_x, grid_y), _FALLBACK_GRID_COLOR),
        (footer_sample_pos, _FALLBACK_BOX_COLOR),
    )
    pixels = rgb.load()
    return all(pixels[x, y] == expected for (x, y), expected in samples)


def _path_from_region(region: dict[str, Any], key: str) -> Path:
    value = str(region.get(key) or "")
    if not value:
        raise ValueError(f"Region missing path key: {key}")
    path = Path(value)
    return path if path.is_absolute() else (BASE_DIR / path)


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except Exception:
        return str(path)


def _load_refresh_meta(region: dict[str, Any]) -> dict[str, Any]:
    refresh_path = _path_from_region(region, "radar_final").with_name("last_refresh.json")
    if not refresh_path.exists():
        return {}
    try:
        return json.loads(refresh_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_refresh_meta(region: dict[str, Any], payload: dict[str, Any]) -> None:
    refresh_path = _path_from_region(region, "radar_final").with_name("last_refresh.json")
    refresh_path.parent.mkdir(parents=True, exist_ok=True)

    merged = _load_refresh_meta(region)
    merged.update(payload)
    refresh_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def _parse_utc(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _utc_iso(value: datetime) -> str:
    return value.replace(microsecond=0).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_z() -> str:
    return _utc_iso(datetime.now(timezone.utc))


def _extract_zip(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", value)
    return match.group(1) if match else ""


def _slugify_region_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "region"
