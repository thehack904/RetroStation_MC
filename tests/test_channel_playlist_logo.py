from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_guide_web_app_logo", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChannelPlaylistLogoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()

    def test_build_channel_m3u_content_emits_logo_attribute_when_present(self) -> None:
        content = self.web._build_channel_m3u_content(
            "Retro Guide",
            "http://example.test/hls/master.m3u8",
            "http://example.test/channel.xmltv",
            "http://example.test/static/logo.svg",
        )
        self.assertIn('tvg-logo="http://example.test/static/logo.svg"', content)

    def test_build_channel_m3u_content_omits_logo_attribute_when_empty(self) -> None:
        content = self.web._build_channel_m3u_content(
            "Retro Guide",
            "http://example.test/hls/master.m3u8",
            "http://example.test/channel.xmltv",
        )
        self.assertNotIn("tvg-logo=", content)

    def test_channel_logo_url_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logo_dir = Path(tmp_dir)
            self.web.GUIDE_LOGO_DIR = logo_dir
            (logo_dir / "default.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (logo_dir / "custom.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            default_url = self.web._channel_logo_url({}, "http://example.test")
            self.assertEqual(default_url, "http://example.test/guide-logo/default.png")

            custom_url = self.web._channel_logo_url(
                {"guide_logo_mode": "custom", "guide_logo_custom_file": "custom.png"},
                "http://example.test",
            )
            self.assertEqual(custom_url, "http://example.test/guide-logo/custom.png")

            disabled_url = self.web._channel_logo_url(
                {"guide_logo_mode": "disabled"},
                "http://example.test",
            )
            self.assertEqual(disabled_url, "")

    def test_channel_logo_url_omits_default_when_no_default_logo_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logo_dir = Path(tmp_dir)
            self.web.GUIDE_LOGO_DIR = logo_dir

            default_url = self.web._channel_logo_url({}, "http://example.test")
            self.assertEqual(default_url, "")

    def test_index_defaults_to_default_logo_mode_when_custom_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.web.GUIDE_LOGO_DIR = Path(tmp_dir)
            self.web.store = Mock()
            self.web.store.get_config.return_value = {
                "guide_logo_mode": "custom",
                "guide_logo_custom_file": "custom.png",
            }
            self.web.store.get_recent_events.return_value = []
            self.web.store.count_events.return_value = 0
            self.web.manager = Mock()
            self.web.manager.status.return_value = {
                "pipeline_active": False,
                "renderer_running": False,
                "ffmpeg_running": False,
                "guide_buffered": False,
                "current_theme": "classic_blue",
                "last_refresh_status": "ok",
                "playlist_source": "",
                "xmltv_source": "",
                "stream_url": "http://example.test/hls/master.m3u8",
                "stream_version": 0,
            }

            client = self.web.app.test_client()
            response = client.get("/")

            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('<option value="default" selected>Default guide icon</option>', html)
            self.assertNotIn('<option value="custom" selected>Use uploaded custom icon</option>', html)


if __name__ == "__main__":
    unittest.main()
