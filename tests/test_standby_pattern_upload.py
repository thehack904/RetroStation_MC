from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from werkzeug.datastructures import MultiDict


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_guide_web_app_standby_pattern", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StandbyPatternUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.web.STANDBY_PATTERN_DIR = Path(self.tmp_dir.name)
        self.web.store = Mock()
        self.web.store.get_config.return_value = {"title": "Guide Channel", "standby_custom_file": ""}
        self.web.manager = Mock()
        self.web.manager._is_valid_image_file.return_value = True
        self.client = self.web.app.test_client()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_upload_keeps_multiple_files_and_selects_uploaded_file(self) -> None:
        response1 = self.client.post(
            "/standby-pattern/upload",
            data={"standby_pattern_file": (io.BytesIO(b"first"), "pattern.png")},
            content_type="multipart/form-data",
        )
        response2 = self.client.post(
            "/standby-pattern/upload",
            data={"standby_pattern_file": (io.BytesIO(b"second"), "pattern.png")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response1.status_code, 302)
        self.assertEqual(response2.status_code, 302)
        self.assertTrue((self.web.STANDBY_PATTERN_DIR / "pattern.png").exists())
        self.assertTrue((self.web.STANDBY_PATTERN_DIR / "pattern-1.png").exists())
        saved = self.web.store.save_config.call_args.args[0]
        self.assertEqual(saved["standby_custom_file"], "pattern-1.png")
        self.assertEqual(saved["title"], "Guide Channel")
        self.web.manager._generate_standby_segment.assert_called_with("Guide Channel")

    def test_select_default_clears_custom_pattern(self) -> None:
        self.web.store.get_config.return_value = {"title": "Guide Channel", "standby_custom_file": "pattern.png"}

        response = self.client.post("/standby-pattern/select-default")

        self.assertEqual(response.status_code, 302)
        saved = self.web.store.save_config.call_args.args[0]
        self.assertEqual(saved["standby_custom_file"], "")
        self.assertEqual(saved["title"], "Guide Channel")
        self.web.manager._generate_standby_segment.assert_called_with("Guide Channel")

    def test_select_route_with_blank_value_uses_default_pattern(self) -> None:
        self.web.store.get_config.return_value = {"title": "Guide Channel", "standby_custom_file": "pattern.png"}

        response = self.client.post("/standby-pattern/select", data={"standby_pattern_file": ""})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("#tab-standby-pattern"))
        saved = self.web.store.save_config.call_args.args[0]
        self.assertEqual(saved["standby_custom_file"], "")
        self.web.manager._generate_standby_segment.assert_called_with("Guide Channel")

    def test_standby_pattern_settings_updates_overlay_opacity(self) -> None:
        self.web.store.get_config.return_value = {
            "title": "Guide Channel",
            "standby_custom_file": "pattern.png",
            "standby_overlay_enabled": True,
            "standby_overlay_opacity": 50,
        }

        response = self.client.post(
            "/standby-pattern/settings",
            data={"standby_overlay_opacity": "75"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("#tab-standby-pattern"))
        saved = self.web.store.save_config.call_args.args[0]
        self.assertTrue(saved["standby_overlay_enabled"])
        self.assertEqual(saved["standby_overlay_opacity"], 75)
        self.web.manager._generate_standby_segment.assert_called_with("Guide Channel")

    def test_standby_pattern_settings_can_disable_overlay(self) -> None:
        self.web.store.get_config.return_value = {
            "title": "Guide Channel",
            "standby_custom_file": "pattern.png",
            "standby_overlay_enabled": True,
            "standby_overlay_opacity": 50,
        }

        response = self.client.post(
            "/standby-pattern/settings",
            data={"standby_overlay_enabled": "0", "standby_overlay_opacity": "30"},
        )

        self.assertEqual(response.status_code, 302)
        saved = self.web.store.save_config.call_args.args[0]
        self.assertFalse(saved["standby_overlay_enabled"])
        self.assertEqual(saved["standby_overlay_opacity"], 30)

    def test_standby_pattern_settings_can_reenable_overlay_with_hidden_fallback(self) -> None:
        self.web.store.get_config.return_value = {
            "title": "Guide Channel",
            "standby_custom_file": "pattern.png",
            "standby_overlay_enabled": False,
            "standby_overlay_opacity": 50,
        }

        response = self.client.post(
            "/standby-pattern/settings",
            data=MultiDict(
                [
                    ("standby_overlay_enabled", "0"),
                    ("standby_overlay_enabled", "1"),
                    ("standby_overlay_opacity", "40"),
                ]
            ),
        )

        self.assertEqual(response.status_code, 302)
        saved = self.web.store.save_config.call_args.args[0]
        self.assertTrue(saved["standby_overlay_enabled"])
        self.assertEqual(saved["standby_overlay_opacity"], 40)


if __name__ == "__main__":
    unittest.main()
