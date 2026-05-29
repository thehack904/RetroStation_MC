from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from app.config_store import DEFAULT_CONFIG
from werkzeug.datastructures import MultiDict


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_guide_web_app_off_air", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_app = _load_app_module()


def _cfg(**overrides) -> dict:
    """Return a config dict with off-air defaults plus any overrides."""
    base = {
        "off_air_enabled": True,
        "off_air_start": "00:00",
        "off_air_end": "06:00",
        "off_air_static_enabled": False,
    }
    base.update(overrides)
    return base


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 1, 1, hour, minute, 0)


class CoerceTimeStrTests(unittest.TestCase):
    def test_valid_time_is_returned_normalised(self) -> None:
        self.assertEqual(_app._coerce_time_str("23:45", "00:00"), "23:45")

    def test_single_digit_hour_is_zero_padded(self) -> None:
        self.assertEqual(_app._coerce_time_str("6:00", "00:00"), "06:00")

    def test_invalid_time_returns_default(self) -> None:
        self.assertEqual(_app._coerce_time_str("99:99", "05:30"), "05:30")

    def test_none_returns_default(self) -> None:
        self.assertEqual(_app._coerce_time_str(None, "12:00"), "12:00")

    def test_empty_string_returns_default(self) -> None:
        self.assertEqual(_app._coerce_time_str("", "07:00"), "07:00")


class IsOffAirTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # Feature disabled
    # ------------------------------------------------------------------
    def test_disabled_is_never_off_air(self) -> None:
        cfg = _cfg(off_air_enabled=False)
        self.assertFalse(_app._is_off_air(cfg, _now=_at(2)))

    # ------------------------------------------------------------------
    # Normal (same-day) window: 02:00 – 06:00
    # ------------------------------------------------------------------
    def test_inside_daytime_window(self) -> None:
        cfg = _cfg(off_air_start="02:00", off_air_end="06:00")
        for h, m in [(2, 0), (3, 30), (5, 59)]:
            self.assertTrue(
                _app._is_off_air(cfg, _now=_at(h, m)),
                f"{h:02d}:{m:02d} should be off-air",
            )

    def test_outside_daytime_window(self) -> None:
        cfg = _cfg(off_air_start="02:00", off_air_end="06:00")
        for h, m in [(0, 0), (1, 59), (6, 0), (6, 1), (23, 59)]:
            self.assertFalse(
                _app._is_off_air(cfg, _now=_at(h, m)),
                f"{h:02d}:{m:02d} should be on-air",
            )

    # ------------------------------------------------------------------
    # Overnight window: 23:00 – 06:00
    # ------------------------------------------------------------------
    def test_inside_overnight_window(self) -> None:
        cfg = _cfg(off_air_start="23:00", off_air_end="06:00")
        for h, m in [(23, 0), (23, 59), (0, 0), (3, 30), (5, 59)]:
            self.assertTrue(
                _app._is_off_air(cfg, _now=_at(h, m)),
                f"{h:02d}:{m:02d} should be off-air",
            )

    def test_outside_overnight_window(self) -> None:
        cfg = _cfg(off_air_start="23:00", off_air_end="06:00")
        for h, m in [(6, 0), (12, 0), (22, 59)]:
            self.assertFalse(
                _app._is_off_air(cfg, _now=_at(h, m)),
                f"{h:02d}:{m:02d} should be on-air",
            )

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------
    def test_start_equals_end_is_never_off_air(self) -> None:
        cfg = _cfg(off_air_start="03:00", off_air_end="03:00")
        self.assertFalse(_app._is_off_air(cfg, _now=_at(3)))

    def test_invalid_start_falls_back_to_default(self) -> None:
        # "bad:time" is coerced to default "00:00"; end is "06:00"
        cfg = _cfg(off_air_start="bad:time", off_air_end="06:00")
        # Should still be off-air at 03:00 within 00:00-06:00
        self.assertTrue(_app._is_off_air(cfg, _now=_at(3)))

    def test_default_config_has_off_air_keys(self) -> None:
        self.assertIn("off_air_enabled", DEFAULT_CONFIG)
        self.assertIn("off_air_start", DEFAULT_CONFIG)
        self.assertIn("off_air_end", DEFAULT_CONFIG)
        self.assertIn("off_air_static_enabled", DEFAULT_CONFIG)
        self.assertFalse(DEFAULT_CONFIG["off_air_enabled"])
        self.assertEqual(DEFAULT_CONFIG["off_air_start"], "00:00")
        self.assertEqual(DEFAULT_CONFIG["off_air_end"], "06:00")
        self.assertFalse(DEFAULT_CONFIG["off_air_static_enabled"])

    def test_midnight_boundary_start_of_window(self) -> None:
        # 00:00 is the first minute of the window 00:00-06:00
        cfg = _cfg(off_air_start="00:00", off_air_end="06:00")
        self.assertTrue(_app._is_off_air(cfg, _now=_at(0, 0)))

    def test_midnight_boundary_end_of_window_is_exclusive(self) -> None:
        # 06:00 is not included in the window 00:00-06:00
        cfg = _cfg(off_air_start="00:00", off_air_end="06:00")
        self.assertFalse(_app._is_off_air(cfg, _now=_at(6, 0)))


class StandbySegmentSelectionTests(unittest.TestCase):
    def test_off_air_static_enabled_uses_static_segment(self) -> None:
        cfg = _cfg(off_air_static_enabled=True)
        segment = _app._resolve_standby_segment(cfg, off_air_now=True)
        self.assertEqual(segment.name, "static.ts")

    def test_off_air_static_disabled_uses_standby_segment(self) -> None:
        cfg = _cfg(off_air_static_enabled=False)
        segment = _app._resolve_standby_segment(cfg, off_air_now=True)
        self.assertEqual(segment.name, "standby.ts")

    def test_on_air_ignores_static_toggle(self) -> None:
        cfg = _cfg(off_air_static_enabled=True)
        segment = _app._resolve_standby_segment(cfg, off_air_now=False)
        self.assertEqual(segment.name, "standby.ts")


class OffAirSettingsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()

    def setUp(self) -> None:
        self.web.store = Mock()
        self.web.store.get_config.return_value = {
            "off_air_enabled": False,
            "off_air_start": "00:00",
            "off_air_end": "06:00",
            "off_air_static_enabled": False,
        }
        self.web.manager = Mock()
        self.client = self.web.app.test_client()

    def test_hidden_fallback_and_checked_box_keep_off_air_enabled(self) -> None:
        response = self.client.post(
            "/off-air/settings",
            data=MultiDict(
                [
                    ("off_air_enabled", "0"),
                    ("off_air_enabled", "1"),
                    ("off_air_start", "01:30"),
                    ("off_air_end", "07:45"),
                    ("off_air_static_enabled", "0"),
                    ("off_air_static_enabled", "1"),
                ]
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("#tab-off-air"))
        saved = self.web.store.save_config.call_args.args[0]
        self.assertTrue(saved["off_air_enabled"])
        self.assertEqual(saved["off_air_start"], "01:30")
        self.assertEqual(saved["off_air_end"], "07:45")
        self.assertTrue(saved["off_air_static_enabled"])


if __name__ == "__main__":
    unittest.main()
