from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from app.weather_renderer import WeatherRenderer, _BG


class WeatherRendererIconTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_path = Path(self.temp_dir.name) / "weather_state.json"
        state_path.write_text("{}", encoding="utf-8")
        self.renderer = WeatherRenderer(state_path, 1280, 720)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _non_bg_pixels(self, image: Image.Image) -> int:
        px = image.load()
        w, h = image.size
        count = 0
        for y in range(h):
            for x in range(w):
                if px[x, y] != _BG:
                    count += 1
        return count

    def test_draw_weather_icon_renders_supported_icons(self) -> None:
        icons = [
            "sunny",
            "partly_cloudy",
            "partly_cloudy_night",
            "rain",
            "showers",
            "thunderstorm",
            "drizzle",
            "snow",
            "foggy",
            "windy",
        ]
        for icon in icons:
            with self.subTest(icon=icon):
                img = Image.new("RGB", (120, 120), _BG)
                draw = ImageDraw.Draw(img)
                self.renderer._draw_weather_icon(draw, icon, 60, 60, 64)
                self.assertGreater(self._non_bg_pixels(img), 0)

    def test_draw_weather_icon_unknown_key_falls_back_to_cloudy(self) -> None:
        img = Image.new("RGB", (120, 120), _BG)
        draw = ImageDraw.Draw(img)
        self.renderer._draw_weather_icon(draw, "unknown_icon", 60, 60, 64)
        self.assertGreater(self._non_bg_pixels(img), 0)

    def test_apply_display_tz_uses_browser_timezone_for_local(self) -> None:
        self.renderer._state = {"timezone": "local", "browser_timezone": "America/New_York"}
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        converted = self.renderer._apply_display_tz(dt)
        self.assertEqual(converted.strftime("%H:%M"), "07:00")

    def test_apply_display_tz_respects_utc_mode(self) -> None:
        self.renderer._state = {"timezone": "utc", "browser_timezone": "America/New_York"}
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        converted = self.renderer._apply_display_tz(dt)
        self.assertEqual(converted.strftime("%H:%M"), "12:00")


if __name__ == "__main__":
    unittest.main()
