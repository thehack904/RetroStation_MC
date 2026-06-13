from __future__ import annotations

import importlib.util
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from werkzeug.datastructures import MultiDict


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_station_mc_virtual_channels", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VirtualChannelsWeatherConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()
        cls.client = cls.web.app.test_client()

    # ── Admin page ────────────────────────────────────────────────

    def test_virtual_channels_page_returns_200(self) -> None:
        resp = self.client.get("/virtual-channels")
        self.assertEqual(resp.status_code, 200)

    def test_virtual_channels_page_contains_weather_channel(self) -> None:
        resp = self.client.get("/virtual-channels")
        body = resp.data.decode()
        self.assertIn("Weather Channel", body)

    def test_index_page_has_virtual_channels_link(self) -> None:
        resp = self.client.get("/")
        body = resp.data.decode()
        self.assertIn("Virtual Channels", body)
        self.assertIn("/virtual-channels", body)

    def test_index_page_has_virtual_channels_quick_actions(self) -> None:
        body = self.client.get("/").data.decode()
        self.assertIn("Open Virtual Channels", body)
        self.assertIn("Preview Weather Page", body)

    def test_index_page_has_weather_broadcast_toggle(self) -> None:
        resp = self.client.get("/")
        body = resp.data.decode()
        self.assertIn('name="weather_channel_enabled"', body)
        self.assertIn("Include Weather Channel in the exported M3U/XMLTV", body)

    # ── Weather display page ──────────────────────────────────────

    def test_weather_page_returns_200(self) -> None:
        resp = self.client.get("/weather")
        self.assertEqual(resp.status_code, 200)

    def test_weather_page_fetches_api_weather(self) -> None:
        body = self.client.get("/weather").data.decode()
        self.assertIn("/api/weather", body)

    # ── Weather API ───────────────────────────────────────────────

    def test_api_weather_returns_expected_keys(self) -> None:
        resp = self.client.get("/api/weather")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in ("now", "five_day", "alerts", "radar_url", "segment",
                    "segment_label", "ms_until_next", "seconds_per_segment"):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_api_weather_segment_is_valid(self) -> None:
        data = self.client.get("/api/weather").get_json()
        self.assertIn(data["segment"], range(4))
        self.assertIn(data["segment_label"], ("current", "forecast", "radar", "alerts"))

    def test_api_weather_stub_when_unconfigured(self) -> None:
        # With no lat/lon configured the payload should contain stub 'now' data
        data = self.client.get("/api/weather").get_json()
        now = data["now"]
        # All keys present
        for key in ("temp", "condition", "humidity", "wind", "feels_like", "icon"):
            self.assertIn(key, now)

    # ── bg_override API ───────────────────────────────────────────

    def test_bg_override_get_returns_condition(self) -> None:
        resp = self.client.get("/api/weather/bg_override")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("condition", resp.get_json())

    def test_bg_override_post_valid_condition(self) -> None:
        resp = self.client.post(
            "/api/weather/bg_override",
            data=json.dumps({"condition": "rain"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["condition"], "rain")

    def test_bg_override_post_invalid_condition_returns_400(self) -> None:
        resp = self.client.post(
            "/api/weather/bg_override",
            data=json.dumps({"condition": "not_a_real_condition"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["ok"])

    def test_bg_override_post_auto_clears_override(self) -> None:
        # Set an override first
        self.client.post(
            "/api/weather/bg_override",
            data=json.dumps({"condition": "sunny"}),
            content_type="application/json",
        )
        # "auto" should clear it
        resp = self.client.post(
            "/api/weather/bg_override",
            data=json.dumps({"condition": "auto"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["condition"], "")

    def test_bg_override_delete_clears_override(self) -> None:
        # Set an override
        self.client.post(
            "/api/weather/bg_override",
            data=json.dumps({"condition": "snow"}),
            content_type="application/json",
        )
        resp = self.client.delete("/api/weather/bg_override")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["condition"], "")

    # ── Zip lookup API ────────────────────────────────────────────

    def test_zip_lookup_no_zip_returns_400(self) -> None:
        resp = self.client.get("/api/weather/zip-lookup")
        self.assertEqual(resp.status_code, 400)

    # ── Weather config save ───────────────────────────────────────

    def test_save_weather_config_persists_values(self) -> None:
        resp = self.client.post(
            "/virtual-channels/weather/config",
            data={
                "weather_channel_enabled": "1",
                "weather_location_name": "Portland, OR",
                "weather_lat": "45.52",
                "weather_lon": "-122.68",
                "weather_units": "F",
                "weather_seconds_per_segment": "120",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        cfg = self.web.store.get_config()
        self.assertEqual(cfg.get("weather_lat"), "45.52")
        self.assertEqual(cfg.get("weather_lon"), "-122.68")
        self.assertEqual(cfg.get("weather_location_name"), "Portland, OR")
        self.assertTrue(cfg.get("weather_channel_enabled"))

    def test_save_weather_config_invalid_lat_shows_error(self) -> None:
        resp = self.client.post(
            "/virtual-channels/weather/config",
            data={
                "weather_lat": "not-a-number",
                "weather_lon": "-80.19",
                "weather_units": "F",
                "weather_seconds_per_segment": "300",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn("Invalid", body)

    def test_main_config_save_uses_checkbox_list_for_weather_channel(self) -> None:
        self.web.store.save_config({"weather_channel_enabled": False})
        resp = self.client.post(
            "/config",
            data=MultiDict(
                [
                    ("weather_channel_enabled", "0"),
                    ("weather_channel_enabled", "1"),
                    ("action", "save"),
                ]
            ),
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.web.store.get_config().get("weather_channel_enabled"))

    # ── Exported playlist / XMLTV ───────────────────────────────────

    def test_channel_playlist_includes_weather_channel_when_enabled(self) -> None:
        self.web.store.save_config(
            {
                "title": "Guide Channel",
                "weather_channel_enabled": True,
                "weather_location_name": "Portland, OR",
            }
        )
        body = self.client.get("/channel.m3u").data.decode()
        self.assertIn('tvg-id="retro-guide-channel"', body)
        self.assertIn('tvg-id="retro-weather-channel"', body)
        self.assertIn("Weather Channel - Portland OR", body)
        self.assertIn("/hls/weather.m3u8", body)

    def test_channel_playlist_m3u8_includes_weather_channel_when_enabled(self) -> None:
        self.web.store.save_config(
            {
                "title": "Guide Channel",
                "weather_channel_enabled": True,
                "weather_location_name": "Portland, OR",
            }
        )
        resp = self.client.get("/channel.m3u8")
        body = resp.data.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn('tvg-id="retro-guide-channel"', body)
        self.assertIn('tvg-id="retro-weather-channel"', body)
        self.assertIn("Weather Channel - Portland OR", body)
        self.assertIn("/hls/weather.m3u8", body)

    def test_channel_playlist_sanitizes_quotes_and_commas_in_weather_location_name(self) -> None:
        self.web.store.save_config(
            {
                "title": 'Guide "Channel"',
                "weather_channel_enabled": True,
                "weather_location_name": 'Austin, "TX"',
            }
        )
        m3u_body = self.client.get("/channel.m3u").data.decode()
        self.assertIn('tvg-name="Guide \'Channel\'"', m3u_body)
        self.assertIn('tvg-name="Weather Channel - Austin \'TX\'"', m3u_body)
        self.assertNotIn('Austin, "TX" tvg-chno=', m3u_body)
        self.assertNotIn('Austin, ', m3u_body)

        m3u8_body = self.client.get("/channel.m3u8").data.decode()
        self.assertIn('tvg-name="Guide \'Channel\'"', m3u8_body)
        self.assertIn('tvg-name="Weather Channel - Austin \'TX\'"', m3u8_body)
        self.assertNotIn('Austin, "TX" tvg-chno=', m3u8_body)
        self.assertNotIn('Austin, ', m3u8_body)

    def test_channel_playlist_omits_weather_channel_when_disabled(self) -> None:
        self.web.store.save_config(
            {
                "title": "Guide Channel",
                "weather_channel_enabled": False,
                "weather_location_name": "Portland, OR",
            }
        )
        body = self.client.get("/channel.m3u").data.decode()
        self.assertIn('tvg-id="retro-guide-channel"', body)
        self.assertNotIn('tvg-id="retro-weather-channel"', body)

    def test_channel_xmltv_includes_weather_channel_when_enabled(self) -> None:
        self.web.store.save_config(
            {
                "title": "Guide Channel",
                "weather_channel_enabled": True,
                "weather_location_name": "Portland, OR",
            }
        )
        resp = self.client.get("/channel.xmltv")
        self.assertEqual(resp.status_code, 200)
        root = ET.fromstring(resp.data.decode())
        channel_ids = [channel.attrib.get("id") for channel in root.findall("channel")]
        self.assertIn("retro-guide-channel", channel_ids)
        self.assertIn("retro-weather-channel", channel_ids)
        weather_titles = [
            prog.findtext("title")
            for prog in root.findall("programme")
            if prog.attrib.get("channel") == "retro-weather-channel"
        ]
        self.assertTrue(weather_titles)
        self.assertTrue(all(title == "Weather Channel - Portland, OR" for title in weather_titles))

    # ── Weather helper functions ──────────────────────────────────

    def test_wmo_label_known_code(self) -> None:
        self.assertEqual(self.web._wmo_label(0), "Sunny")
        self.assertEqual(self.web._wmo_label(63), "Rain")

    def test_wmo_label_unknown_code_returns_unknown(self) -> None:
        self.assertEqual(self.web._wmo_label(999), "Unknown")

    def test_wmo_icon_known_code(self) -> None:
        self.assertEqual(self.web._wmo_icon(0), "sunny")
        self.assertEqual(self.web._wmo_icon(63), "rain")

    def test_to_night_icon_converts_sunny(self) -> None:
        self.assertEqual(self.web._to_night_icon("sunny"), "partly_cloudy_night")

    def test_to_night_icon_passthrough_for_rain(self) -> None:
        self.assertEqual(self.web._to_night_icon("rain"), "rain")

    def test_wind_dir_north(self) -> None:
        self.assertEqual(self.web._wind_dir(0), "N")
        self.assertEqual(self.web._wind_dir(360), "N")

    def test_wind_dir_south(self) -> None:
        self.assertEqual(self.web._wind_dir(180), "S")

    def test_build_radar_url_with_coords(self) -> None:
        url = self.web._build_radar_url("25.77", "-80.19")
        self.assertIn("bbox=", url)
        self.assertIn("opengeo.ncep.noaa.gov", url)

    def test_build_radar_url_fallback_to_conus(self) -> None:
        url = self.web._build_radar_url("", "")
        self.assertIn("bbox=-126,24,-66,50", url)

    def test_build_weather_payload_stub_when_no_coords(self) -> None:
        payload = self.web._build_weather_payload({
            "lat": "", "lon": "", "location_name": "Test",
            "units": "F", "bg_condition_override": "",
        })
        self.assertEqual(payload["now"]["condition"], "Not Configured")
        self.assertEqual(payload["extended"], [])
        self.assertEqual(payload["five_day"], [])

    def test_config_store_defaults_have_weather_keys(self) -> None:
        from app.config_store import DEFAULT_CONFIG
        for key in ("weather_channel_enabled", "weather_lat", "weather_lon",
                    "weather_location_name", "weather_units",
                    "weather_seconds_per_segment", "weather_bg_condition_override",
                    "weather_music_mode", "weather_music_loop",
                    "weather_music_single_file", "weather_music_playlist_files"):
            self.assertIn(key, DEFAULT_CONFIG, f"Missing key in DEFAULT_CONFIG: {key}")

    def test_config_store_default_has_weather_logo_enabled(self) -> None:
        from app.config_store import DEFAULT_CONFIG
        self.assertIn("weather_logo_enabled", DEFAULT_CONFIG)
        self.assertTrue(DEFAULT_CONFIG["weather_logo_enabled"])

    # ── Weather icon toggle ───────────────────────────────────────

    def test_virtual_channels_page_contains_weather_logo_toggle(self) -> None:
        body = self.client.get("/virtual-channels").data.decode()
        self.assertIn('name="weather_logo_enabled"', body)
        self.assertIn('name="weather_music_mode"', body)
        self.assertIn('name="weather_music_loop"', body)

    def test_channel_playlist_includes_weather_logo_when_logo_enabled(self) -> None:
        self.web.store.save_config(
            {
                "weather_channel_enabled": True,
                "weather_logo_enabled": True,
                "weather_location_name": "Miami, FL",
            }
        )
        body = self.client.get("/channel.m3u").data.decode()
        self.assertIn('tvg-id="retro-weather-channel"', body)
        self.assertIn("tvg-logo=", body)
        self.assertIn("/weather-logo/", body)

    def test_channel_playlist_omits_weather_logo_when_logo_disabled(self) -> None:
        self.web.store.save_config(
            {
                "weather_channel_enabled": True,
                "weather_logo_enabled": False,
                "weather_location_name": "Miami, FL",
            }
        )
        body = self.client.get("/channel.m3u").data.decode()
        self.assertIn('tvg-id="retro-weather-channel"', body)
        # The guide channel may still have a logo; only check weather entry has none
        lines = body.splitlines()
        weather_extinf = next((l for l in lines if "retro-weather-channel" in l), "")
        self.assertNotIn("tvg-logo=", weather_extinf)

    def test_save_weather_config_persists_logo_enabled(self) -> None:
        resp = self.client.post(
            "/virtual-channels/weather/config",
            data={
                "weather_channel_enabled": "1",
                "weather_logo_enabled": "1",
                "weather_location_name": "Denver, CO",
                "weather_lat": "39.74",
                "weather_lon": "-104.98",
                "weather_units": "F",
                "weather_seconds_per_segment": "300",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        cfg = self.web.store.get_config()
        self.assertTrue(cfg.get("weather_logo_enabled"))

    def test_save_weather_config_logo_disabled_persists(self) -> None:
        self.client.post(
            "/virtual-channels/weather/config",
            data={
                "weather_logo_enabled": "0",
                "weather_units": "F",
                "weather_seconds_per_segment": "300",
            },
            follow_redirects=True,
        )
        cfg = self.web.store.get_config()
        self.assertFalse(cfg.get("weather_logo_enabled"))

    def test_weather_logo_route_serves_default_logo(self) -> None:
        logo_dir = Path(__file__).resolve().parents[1] / "data" / "weather_logo"
        logo_files = [
            f for f in logo_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
        ] if logo_dir.is_dir() else []
        self.assertTrue(logo_files, "No weather logo file found in data/weather_logo/")
        logo_name = logo_files[0].name
        resp = self.client.get(f"/weather-logo/{logo_name}")
        self.assertEqual(resp.status_code, 200)

    def test_weather_logo_route_rejects_missing_file(self) -> None:
        resp = self.client.get("/weather-logo/nonexistent.png")
        self.assertEqual(resp.status_code, 404)

    def test_get_weather_config_includes_logo_enabled(self) -> None:
        self.web.store.save_config({"weather_logo_enabled": True})
        cfg = self.web._get_weather_config()
        self.assertIn("logo_enabled", cfg)
        self.assertTrue(cfg["logo_enabled"])

    def test_get_weather_config_includes_weather_music_fields(self) -> None:
        self.web.store.save_config(
            {
                "weather_music_mode": "playlist",
                "weather_music_loop": True,
                "weather_music_single_file": "weather.mp3",
                "weather_music_playlist_files": ["weather.mp3"],
            }
        )
        cfg = self.web._get_weather_config()
        self.assertEqual(cfg.get("music_mode"), "playlist")
        self.assertTrue(cfg.get("music_loop"))
        self.assertEqual(cfg.get("music_single_file"), "weather.mp3")
        self.assertEqual(cfg.get("music_playlist_files"), ["weather.mp3"])

    def test_save_weather_config_persists_weather_music_settings(self) -> None:
        track_name = "weather_track_test.mp3"
        track_path = self.web.WEATHER_MUSIC_DIR / track_name
        track_path.write_bytes(b"ID3demo")
        try:
            resp = self.client.post(
                "/virtual-channels/weather/config",
                data=MultiDict(
                    [
                        ("weather_music_mode", "playlist"),
                        ("weather_music_loop", "1"),
                        ("weather_music_single_file", track_name),
                        ("weather_music_playlist_files", track_name),
                    ]
                ),
                follow_redirects=True,
            )
            self.assertEqual(resp.status_code, 200)
            cfg = self.web.store.get_config()
            self.assertEqual(cfg.get("weather_music_mode"), "playlist")
            self.assertTrue(cfg.get("weather_music_loop"))
            self.assertEqual(cfg.get("weather_music_single_file"), track_name)
            self.assertEqual(cfg.get("weather_music_playlist_files"), [track_name])
        finally:
            track_path.unlink(missing_ok=True)

    def test_weather_logo_url_returns_empty_when_disabled(self) -> None:
        config = {"weather_logo_enabled": False}
        url = self.web._weather_logo_url(config, "http://localhost")
        self.assertEqual(url, "")

    # ── Virtual channels template ─────────────────────────────────

    def test_virtual_channels_template_exists(self) -> None:
        tmpl = Path(__file__).resolve().parents[1] / "app" / "templates" / "virtual_channels.html"
        self.assertTrue(tmpl.exists())

    def test_weather_template_exists(self) -> None:
        tmpl = Path(__file__).resolve().parents[1] / "app" / "templates" / "weather.html"
        self.assertTrue(tmpl.exists())

    def test_virtual_channels_template_contains_zip_lookup(self) -> None:
        tmpl = Path(__file__).resolve().parents[1] / "app" / "templates" / "virtual_channels.html"
        html = tmpl.read_text(encoding="utf-8")
        self.assertIn("zip-lookup", html)
        self.assertIn("weather_lat", html)
        self.assertIn("weather_lon", html)


if __name__ == "__main__":
    unittest.main()
