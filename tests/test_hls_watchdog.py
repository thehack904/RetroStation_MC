from __future__ import annotations

import tempfile
import time
import unittest
import os
from pathlib import Path
from unittest.mock import Mock, patch

from app.manager import GuideManager


class HlsWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Mock()
        self.store.get_config.return_value = {"segment_seconds": 6, "diag_min_buffer_secs": 18, "diag_min_buffer_segments": 3}
        self.manager = GuideManager(self.store)
        self.manager.logger = Mock()
        self.manager._pipeline_active = True

    def test_watchdog_reports_healthy_when_playlist_and_segment_are_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            playlist = output_dir / "guide.m3u8"
            segment = output_dir / "guide_1.ts"
            segment.write_bytes(b"ok")
            playlist.write_text("#EXTM3U\n#EXTINF:6.0,\nguide_1.ts\n", encoding="utf-8")
            now = time.time()
            segment.touch()
            playlist.touch()

            with patch("app.manager.OUTPUT_DIR", output_dir), patch("app.manager.time.time", return_value=now):
                result = self.manager._hls_watchdog_status(self.store.get_config())

        self.assertTrue(result["healthy"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["latest_segment"], "guide_1.ts")

    def test_watchdog_flags_stalled_playlist_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            playlist = output_dir / "guide.m3u8"
            segment = output_dir / "guide_2.ts"
            segment.write_bytes(b"ok")
            playlist.write_text("#EXTM3U\n#EXTINF:6.0,\nguide_2.ts\n", encoding="utf-8")
            old = time.time() - 30
            os.utime(segment, (old, old))
            os.utime(playlist, (old, old))
            with patch("app.manager.OUTPUT_DIR", output_dir), patch("app.manager.time.time", return_value=time.time()):
                result = self.manager._hls_watchdog_status(self.store.get_config())

        self.assertFalse(result["healthy"])
        self.assertIn("playlist_updates_stalled", result["warnings"])
        self.assertIn("segment_generation_stalled", result["warnings"])
        self.assertTrue(result["restart_hook_ready"])
        self.assertTrue(result["restart_recommended"])

    def test_watchdog_logs_warning_when_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            playlist = output_dir / "guide.m3u8"
            playlist.write_text("#EXTM3U\n", encoding="utf-8")
            now = time.time()
            with patch("app.manager.OUTPUT_DIR", output_dir), patch("app.manager.time.time", return_value=now):
                result = self.manager._hls_watchdog_status(self.store.get_config())

        self.assertFalse(result["healthy"])
        self.manager.logger.warning.assert_called()

    def test_status_includes_gpu_capabilities(self) -> None:
        self.manager._pipeline_active = False
        self.manager._renderer_pid = None
        self.manager._ffmpeg_pid = None
        with patch.object(self.manager, "_hls_watchdog_status", return_value={"healthy": True, "warnings": []}), patch(
            "app.manager.detect_gpu_capabilities",
            return_value={"message": "No hardware acceleration detected; software fallback is active."},
        ):
            status = self.manager.status()

        self.assertIn("gpu_capabilities", status)
        self.assertEqual(
            status["gpu_capabilities"]["message"],
            "No hardware acceleration detected; software fallback is active.",
        )

    def test_status_reports_software_active_when_detected_hardware_is_not_encoder_ready(self) -> None:
        self.manager._pipeline_active = False
        self.manager._renderer_pid = None
        self.manager._ffmpeg_pid = None
        self.store.get_config.return_value = {
            "hardware_acceleration_mode": "hardware_if_available",
            "segment_seconds": 6,
            "diag_min_buffer_secs": 18,
            "diag_min_buffer_segments": 3,
        }
        gpu_capabilities = {
            "device_detected_providers": ["intel"],
            "detected_hardware_providers": [],
            "providers": {
                "intel": {
                    "label": "Intel QuickSync / QSV",
                    "available": False,
                }
            },
            "message": "Hardware was detected, but no usable hardware encoder passed validation; software fallback is active.",
        }
        with patch.object(self.manager, "_hls_watchdog_status", return_value={"healthy": True, "warnings": []}), patch(
            "app.manager.detect_gpu_capabilities",
            return_value=gpu_capabilities,
        ):
            status = self.manager.status()

        self.assertEqual(status["gpu_capabilities"]["active_path"]["provider"], "software")
        self.assertEqual(status["gpu_capabilities"]["active_path"]["label"], "Software fallback (libx264)")
        self.assertIn("hardware was detected but could not be validated for encoding", status["gpu_capabilities"]["active_path"]["reason"].lower())


if __name__ == "__main__":
    unittest.main()
