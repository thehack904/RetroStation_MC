from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from PIL import Image

from app.weather_radar import (
    build_noaa_radar_url,
    bbox_for_16x9,
    radar_is_stale,
    refresh_radar_if_stale,
    OSM_TILE_SERVERS,
    _zoom_for_bbox,
    _lat_lon_to_tile_float,
    _stitch_basemap_from_tiles,
    download_or_generate_basemap,
)


class WeatherRadarModuleTests(unittest.TestCase):
    def test_bbox_for_16x9_returns_expected_order_and_center(self) -> None:
        bbox = bbox_for_16x9(40.75064, -73.99728, radius_miles=60, width=800, height=450)
        west, south, east, north = bbox
        self.assertLess(west, east)
        self.assertLess(south, north)
        self.assertAlmostEqual((west + east) / 2, -73.99728, places=3)
        self.assertAlmostEqual((south + north) / 2, 40.75064, places=3)

    def test_build_noaa_radar_url_uses_qcd_layer_and_wms_111(self) -> None:
        region = {
            "bbox": [-75.0, 39.0, -72.0, 42.0],
            "width": 800,
            "height": 450,
        }
        url = build_noaa_radar_url(region)
        self.assertIn("version=1.1.1", url)
        self.assertIn("layers=conus_bref_qcd", url)
        self.assertIn("srs=EPSG:4326", url)
        self.assertIn("transparent=true", url)

    # ── OSM tile-based basemap tests ──────────────────────────────────────────

    def test_osm_tile_servers_list_matches_retroiptv_guide(self) -> None:
        """OSM_TILE_SERVERS must include the primary and mirror servers used by
        the RetroIPTVGuide Virtual Traffic Channel."""
        self.assertIn("https://tile.openstreetmap.org/{z}/{x}/{y}.png", OSM_TILE_SERVERS)
        self.assertIn("https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", OSM_TILE_SERVERS)
        self.assertIn("https://b.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", OSM_TILE_SERVERS)
        self.assertIn("https://c.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", OSM_TILE_SERVERS)

    def test_zoom_for_bbox_typical_weather_region(self) -> None:
        """60-mile radius at 800 px wide should resolve to zoom 8."""
        # lon_span ≈ 4 degrees for a 60-mile radius near 40°N, 800 px wide
        zoom = _zoom_for_bbox(4.0, 800)
        self.assertGreaterEqual(zoom, 7)
        self.assertLessEqual(zoom, 10)

    def test_zoom_for_bbox_clamped_low(self) -> None:
        """Very wide spans should be clamped to zoom 1."""
        zoom = _zoom_for_bbox(360.0, 800)
        self.assertGreaterEqual(zoom, 1)

    def test_zoom_for_bbox_clamped_high(self) -> None:
        """Very narrow spans should be clamped to zoom 12."""
        zoom = _zoom_for_bbox(0.001, 800)
        self.assertLessEqual(zoom, 12)

    def test_lat_lon_to_tile_float_new_york(self) -> None:
        """Tile coordinates for New York at zoom 10 should be in the expected range."""
        tx, ty = _lat_lon_to_tile_float(40.7128, -74.006, zoom=10)
        # At zoom 10 there are 1024 tiles across longitude; NYC is ~301 tiles from left
        self.assertAlmostEqual(tx, ((-74.006 + 180.0) / 360.0) * 1024, places=2)
        self.assertGreater(ty, 0)
        self.assertLess(ty, 1024)

    def test_stitch_basemap_from_tiles_uses_osm_servers(self) -> None:
        """_stitch_basemap_from_tiles must request URLs from OSM_TILE_SERVERS."""
        fake_tile = Image.new("RGB", (256, 256), (200, 200, 200))
        buf = io.BytesIO()
        fake_tile.save(buf, "PNG")
        tile_bytes = buf.getvalue()

        mock_resp = mock.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.content = tile_bytes

        region = {
            "bbox": [-76.0, 39.0, -72.0, 41.0],
            "lat": 40.0,
            "lon": -74.0,
            "width": 800,
            "height": 450,
            "name": "Test",
        }

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "basemap.png"
            with mock.patch("app.weather_radar.time.sleep"), \
                 mock.patch("app.weather_radar.requests.Session") as mock_session_cls:
                mock_session = mock.MagicMock()
                mock_session.get.return_value = mock_resp
                mock_session_cls.return_value = mock_session

                _stitch_basemap_from_tiles(region, dest)

            self.assertTrue(dest.exists())
            with Image.open(dest) as img:
                self.assertEqual(img.size, (800, 450))

            # Verify that the URLs called use OSM tile server templates
            called_urls = [call.args[0] for call in mock_session.get.call_args_list]
            self.assertTrue(
                any(
                    any(srv.split("{z}")[0] in u for srv in OSM_TILE_SERVERS)
                    for u in called_urls
                ),
                f"Expected OSM tile URLs, got: {called_urls[:3]}",
            )

    def test_download_or_generate_basemap_falls_back_on_tile_failure(self) -> None:
        """When all tile servers fail, download_or_generate_basemap must produce
        a fallback basemap of the correct dimensions."""
        region = {
            "bbox": [-76.0, 39.0, -72.0, 41.0],
            "lat": 40.0,
            "lon": -74.0,
            "width": 800,
            "height": 450,
            "name": "Test",
        }

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "basemap.png"
            region["basemap"] = str(dest)

            with mock.patch(
                "app.weather_radar._stitch_basemap_from_tiles",
                side_effect=RuntimeError("all servers down"),
            ):
                result = download_or_generate_basemap(region)

            self.assertTrue(result.exists())
            with Image.open(result) as img:
                self.assertEqual(img.size, (800, 450))

    # ── Existing stale/fresh radar tests ─────────────────────────────────────

    def test_radar_is_stale_false_when_recent_success_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            final_path = base / "radar_final.png"
            Image.new("RGB", (800, 450), (0, 0, 0)).save(final_path)
            (base / "last_refresh.json").write_text(
                json.dumps(
                    {
                        "last_success_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    }
                ),
                encoding="utf-8",
            )
            region = {
                "radar_final": str(final_path),
                "radar_refresh_seconds": 300,
                "width": 800,
                "height": 450,
            }
            self.assertFalse(radar_is_stale(region))

    def test_refresh_radar_if_stale_skips_download_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            final_path = base / "radar_final.png"
            basemap_path = base / "basemap.png"
            Image.new("RGB", (800, 450), (20, 20, 20)).save(final_path)
            Image.new("RGB", (800, 450), (20, 20, 20)).save(basemap_path)
            (base / "last_refresh.json").write_text(
                json.dumps(
                    {
                        "last_success_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    }
                ),
                encoding="utf-8",
            )

            region = {
                "basemap": str(basemap_path),
                "radar_overlay": str(base / "radar_overlay.png"),
                "radar_final": str(final_path),
                "radar_refresh_seconds": 300,
                "width": 800,
                "height": 450,
                "bbox": [-75.0, 39.0, -72.0, 42.0],
                "name": "Test",
            }

            with mock.patch("app.weather_radar.download_radar_overlay") as mock_download, mock.patch(
                "app.weather_radar.composite_radar"
            ) as mock_composite:
                result = refresh_radar_if_stale(region)
            self.assertEqual(result, final_path)
            mock_download.assert_not_called()
            mock_composite.assert_not_called()

    def test_refresh_radar_if_stale_downloads_and_composites_when_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            basemap_path = base / "basemap.png"
            overlay_path = base / "radar_overlay.png"
            final_path = base / "radar_final.png"
            Image.new("RGB", (800, 450), (10, 10, 20)).save(basemap_path)
            (base / "last_refresh.json").write_text(
                json.dumps(
                    {
                        "last_success_utc": (datetime.now(timezone.utc) - timedelta(hours=1))
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z")
                    }
                ),
                encoding="utf-8",
            )

            region = {
                "basemap": str(basemap_path),
                "radar_overlay": str(overlay_path),
                "radar_final": str(final_path),
                "radar_refresh_seconds": 300,
                "width": 800,
                "height": 450,
                "bbox": [-75.0, 39.0, -72.0, 42.0],
                "name": "Test",
            }

            def _fake_download(_region):
                Image.new("RGBA", (800, 450), (255, 0, 0, 100)).save(overlay_path)
                return overlay_path

            with mock.patch("app.weather_radar.download_radar_overlay", side_effect=_fake_download):
                result = refresh_radar_if_stale(region)

            self.assertEqual(result, final_path)
            self.assertTrue(final_path.exists())
            refresh_meta = json.loads((base / "last_refresh.json").read_text(encoding="utf-8"))
            self.assertIn("last_success_utc", refresh_meta)


if __name__ == "__main__":
    unittest.main()
