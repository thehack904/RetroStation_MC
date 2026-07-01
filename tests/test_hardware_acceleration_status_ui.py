from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_station_mc_hardware_ui", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HardwareAccelerationStatusUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()

    def test_index_shows_detected_hardware_and_active_software_fallback(self) -> None:
        self.web.store = Mock()
        self.web.store.get_config.return_value = self.web.DEFAULT_CONFIG | {
            "hardware_acceleration_mode": "hardware_if_available",
        }
        self.web.store.get_recent_events.return_value = []
        self.web.store.count_events.return_value = 0
        self.web.manager = Mock()
        self.web.manager.status.return_value = {
            "pipeline_active": False,
            "renderer_running": False,
            "ffmpeg_running": False,
            "guide_buffered": False,
            "current_theme": "retrostation_mc",
            "last_refresh_status": "ok",
            "playlist_source": "",
            "xmltv_source": "",
            "stream_url": "http://example.test/hls/master.m3u8",
            "stream_version": 0,
            "gpu_capabilities": {
                "device_detected_providers": ["intel"],
                "detected_hardware_providers": [],
                "docker_visible_providers": [],
                "ffmpeg_detected_encoders": ["h264_qsv"],
                "providers": {
                    "intel": {
                        "label": "Intel QuickSync / QSV",
                        "available": False,
                    }
                },
                "active_path": {
                    "provider": "software",
                    "label": "Software fallback (libx264)",
                    "codec": "libx264",
                    "using_hardware": False,
                    "reason": (
                        "Hardware was detected but could not be validated for encoding; "
                        "software fallback is active. "
                        "See the GPU Providers section below for details."
                    ),
                },
                "message": "Hardware was detected, but no usable hardware encoder passed validation; software fallback is active.",
            },
        }

        client = self.web.app.test_client()
        response = client.get("/")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Intel QuickSync / QSV", html)
        self.assertIn("+ Software fallback (libx264)", html)
        self.assertIn("Use encoder-ready hardware when available (auto software fallback)", html)
        self.assertIn("Detected hardware means a device was found.", html)
        self.assertIn("Selection Mode", html)
        self.assertIn("Auto: use encoder-ready hardware; otherwise software fallback", html)
        self.assertIn("Active Path", html)
        self.assertIn("Software fallback (libx264)", html)
        self.assertIn("Hardware-Ready Providers", html)
        self.assertIn("Hardware was detected, but no usable hardware encoder passed validation; software fallback is active.", html)


    def test_index_shows_hardware_available_hint_when_software_fallback_configured(self) -> None:
        """When software_fallback is explicitly configured but hardware is ready,
        the Active Path and Active Status should mention the available hardware."""
        self.web.store = Mock()
        self.web.store.get_config.return_value = self.web.DEFAULT_CONFIG | {
            "hardware_acceleration_mode": "software_fallback",
        }
        self.web.store.get_recent_events.return_value = []
        self.web.store.count_events.return_value = 0
        self.web.manager = Mock()
        self.web.manager.status.return_value = {
            "pipeline_active": False,
            "renderer_running": False,
            "ffmpeg_running": False,
            "guide_buffered": False,
            "current_theme": "retrostation_mc",
            "last_refresh_status": "ok",
            "playlist_source": "",
            "xmltv_source": "",
            "stream_url": "http://example.test/hls/master.m3u8",
            "stream_version": 0,
            "gpu_capabilities": {
                "device_detected_providers": ["nvidia"],
                "detected_hardware_providers": ["nvidia"],
                "docker_visible_providers": [],
                "ffmpeg_detected_encoders": ["av1_nvenc", "h264_nvenc", "hevc_nvenc"],
                "providers": {
                    "nvidia": {
                        "label": "NVIDIA NVENC",
                        "available": True,
                    }
                },
                "active_path": {
                    "provider": "software",
                    "label": "Software fallback (libx264) — NVIDIA NVENC encoder-ready",
                    "codec": "libx264",
                    "using_hardware": False,
                    "reason": (
                        "Hardware Acceleration setting is configured, Hardware (NVIDIA NVENC) is encoder-ready. "
                        "Will use software fallback if/when needed."
                    ),
                },
                "message": "Hardware acceleration is ready.",
            },
        }

        client = self.web.app.test_client()
        response = client.get("/")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("NVIDIA NVENC", html)
        self.assertIn("NVIDIA NVENC encoder-ready", html)
        self.assertIn(
            "Hardware Acceleration setting is configured, Hardware (NVIDIA NVENC) is encoder-ready. Will use software fallback if/when needed.",
            html,
        )
        self.assertIn("Forced software fallback (libx264)", html)
        self.assertIn("Active Path", html)
        self.assertIn("Active Status", html)


if __name__ == "__main__":
    unittest.main()
