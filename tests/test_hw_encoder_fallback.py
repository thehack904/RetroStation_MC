"""Tests for runtime hardware-encoder fallback logic.

Both GuideManager and WeatherChannelManager must switch to software (libx264)
after HW_ENCODER_MAX_CONSECUTIVE_FAILURES quick crashes while a hardware
encoder is selected.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from app.manager import (
    GuideManager,
    WeatherChannelManager,
    HW_ENCODER_MAX_CONSECUTIVE_FAILURES,
    HW_ENCODER_QUICK_FAILURE_WINDOW_SECS,
)
from app.ffmpeg_profiles import FFmpegProfile


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _hw_profile(codec: str = "h264_qsv") -> FFmpegProfile:
    return FFmpegProfile(
        name="intel_hardware_auto",
        resolution="1280x720",
        video_codec=codec,
        audio_codec="aac",
        bitrate=None,
        preset=None,
        tune=None,
        hls_segment_length=6,
        encoder_type="hardware",
        hardware_acceleration_provider="intel",
    )


def _sw_profile() -> FFmpegProfile:
    return FFmpegProfile(
        name="software_default",
        resolution="1280x720",
        video_codec="libx264",
        audio_codec="aac",
        bitrate=None,
        preset="veryfast",
        tune="zerolatency",
        hls_segment_length=6,
        encoder_type="software",
        hardware_acceleration_provider="software",
    )


def _make_fake_store(config: dict | None = None) -> MagicMock:
    store = MagicMock()
    store.get_config.return_value = config or {"hardware_acceleration_mode": "hardware_if_available"}
    return store


# ---------------------------------------------------------------------------
# GuideManager tests
# ---------------------------------------------------------------------------

class GuideManagerHwFallbackTests(unittest.TestCase):

    def _make_manager(self) -> GuideManager:
        return GuideManager(_make_fake_store())

    def test_hw_failure_count_initialises_to_zero(self) -> None:
        mgr = self._make_manager()
        self.assertEqual(mgr._hw_failure_count, 0)
        self.assertFalse(mgr._hw_fallback_forced)

    def test_quick_hw_crash_increments_failure_count(self) -> None:
        mgr = self._make_manager()
        mgr._last_encoder_type = "hardware"
        mgr._pipeline_started_at = time.time()  # started just now → quick failure
        mgr._pipeline_active = True

        # Simulate a dead ffmpeg process without actually spawning one.
        with patch.object(mgr, "start_pipeline"):
            with patch.object(mgr, "_renderer_popen", None), \
                 patch.object(mgr, "_ffmpeg_popen", None), \
                 patch.object(mgr, "_renderer_pid", 1), \
                 patch.object(mgr, "_ffmpeg_pid", 2), \
                 patch("app.manager._pid_alive", side_effect=[True, False]):
                mgr.ensure_pipeline_running()

        self.assertEqual(mgr._hw_failure_count, 1)
        self.assertFalse(mgr._hw_fallback_forced)

    def test_long_lived_hw_run_resets_failure_count(self) -> None:
        mgr = self._make_manager()
        mgr._last_encoder_type = "hardware"
        mgr._hw_failure_count = 2
        # Simulate a run that lasted well beyond the quick-failure window.
        mgr._pipeline_started_at = time.time() - (HW_ENCODER_QUICK_FAILURE_WINDOW_SECS + 60)
        mgr._pipeline_active = True

        with patch.object(mgr, "start_pipeline"):
            with patch.object(mgr, "_renderer_popen", None), \
                 patch.object(mgr, "_ffmpeg_popen", None), \
                 patch.object(mgr, "_renderer_pid", 1), \
                 patch.object(mgr, "_ffmpeg_pid", 2), \
                 patch("app.manager._pid_alive", side_effect=[True, False]):
                mgr.ensure_pipeline_running()

        self.assertEqual(mgr._hw_failure_count, 0)
        self.assertFalse(mgr._hw_fallback_forced)

    def test_sw_crash_does_not_increment_hw_failure_count(self) -> None:
        mgr = self._make_manager()
        mgr._last_encoder_type = "software"
        mgr._pipeline_started_at = time.time()
        mgr._pipeline_active = True

        with patch.object(mgr, "start_pipeline"):
            with patch.object(mgr, "_renderer_popen", None), \
                 patch.object(mgr, "_ffmpeg_popen", None), \
                 patch.object(mgr, "_renderer_pid", 1), \
                 patch.object(mgr, "_ffmpeg_pid", 2), \
                 patch("app.manager._pid_alive", side_effect=[True, False]):
                mgr.ensure_pipeline_running()

        self.assertEqual(mgr._hw_failure_count, 0)
        self.assertFalse(mgr._hw_fallback_forced)

    def test_fallback_forced_after_threshold_failures(self) -> None:
        mgr = self._make_manager()
        mgr._last_encoder_type = "hardware"
        mgr._pipeline_active = True

        for _ in range(HW_ENCODER_MAX_CONSECUTIVE_FAILURES):
            mgr._pipeline_started_at = time.time()  # always a fresh quick failure
            with patch.object(mgr, "start_pipeline"):
                with patch.object(mgr, "_renderer_popen", None), \
                     patch.object(mgr, "_ffmpeg_popen", None), \
                     patch.object(mgr, "_renderer_pid", 1), \
                     patch.object(mgr, "_ffmpeg_pid", 2), \
                     patch("app.manager._pid_alive", side_effect=[True, False]):
                    mgr.ensure_pipeline_running()

        self.assertTrue(mgr._hw_fallback_forced)
        self.assertEqual(mgr._hw_failure_count, HW_ENCODER_MAX_CONSECUTIVE_FAILURES)

    def test_start_pipeline_overrides_config_when_fallback_forced(self) -> None:
        mgr = self._make_manager()
        mgr._hw_fallback_forced = True
        captured_config: dict = {}

        def fake_resolve(config, _gpu):
            captured_config.update(config)
            return _sw_profile()

        with patch("app.manager.detect_gpu_capabilities", return_value={}), \
             patch("app.manager.resolve_ffmpeg_profile", side_effect=fake_resolve), \
             patch("app.manager._resolve_video_encoder_path", return_value=("libx264", [], "veryfast", "zerolatency", "software:libx264", "yuv420p")), \
             patch.object(mgr, "_generate_standby_segment"), \
             patch.object(mgr, "_generate_static_segment"), \
             patch.object(mgr, "_stop_pipeline_locked"), \
             patch.object(mgr, "_clean_output_dir"), \
             patch("app.manager.subprocess.Popen") as mock_popen, \
             patch("app.manager._save_pid"), \
             patch.object(mgr, "_start_hls_telemetry_monitor"):
            mock_proc = MagicMock()
            mock_proc.pid = 999
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc
            mgr.start_pipeline()

        self.assertEqual(captured_config.get("hardware_acceleration_mode"), "software_fallback")

    def test_start_pipeline_records_encoder_type(self) -> None:
        mgr = self._make_manager()

        with patch("app.manager.detect_gpu_capabilities", return_value={}), \
             patch("app.manager.resolve_ffmpeg_profile", return_value=_hw_profile()), \
             patch("app.manager._resolve_video_encoder_path", return_value=("h264_qsv", [], None, None, "hardware:intel", "nv12")), \
             patch.object(mgr, "_generate_standby_segment"), \
             patch.object(mgr, "_generate_static_segment"), \
             patch.object(mgr, "_stop_pipeline_locked"), \
             patch.object(mgr, "_clean_output_dir"), \
             patch("app.manager.subprocess.Popen") as mock_popen, \
             patch("app.manager._save_pid"), \
             patch.object(mgr, "_start_hls_telemetry_monitor"):
            mock_proc = MagicMock()
            mock_proc.pid = 999
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc
            mgr.start_pipeline()

        self.assertEqual(mgr._last_encoder_type, "hardware")


# ---------------------------------------------------------------------------
# WeatherChannelManager tests
# ---------------------------------------------------------------------------

class WeatherManagerHwFallbackTests(unittest.TestCase):

    def _make_manager(self) -> WeatherChannelManager:
        return WeatherChannelManager(_make_fake_store(), data_fetcher=lambda: None)

    def test_hw_failure_count_initialises_to_zero(self) -> None:
        mgr = self._make_manager()
        self.assertEqual(mgr._hw_failure_count, 0)
        self.assertFalse(mgr._hw_fallback_forced)

    def test_quick_hw_crash_increments_failure_count(self) -> None:
        mgr = self._make_manager()
        mgr._last_encoder_type = "hardware"
        mgr._pipeline_started_at = time.time()
        mgr._pipeline_active = True

        with patch.object(mgr, "start_pipeline"):
            with patch.object(mgr, "_renderer_popen", None), \
                 patch.object(mgr, "_ffmpeg_popen", None), \
                 patch.object(mgr, "_renderer_pid", 1), \
                 patch.object(mgr, "_ffmpeg_pid", 2), \
                 patch("app.manager._pid_alive", side_effect=[True, False]):
                mgr._ensure_pipeline_running()

        self.assertEqual(mgr._hw_failure_count, 1)

    def test_fallback_forced_after_threshold_failures(self) -> None:
        mgr = self._make_manager()
        mgr._last_encoder_type = "hardware"
        mgr._pipeline_active = True

        for _ in range(HW_ENCODER_MAX_CONSECUTIVE_FAILURES):
            mgr._pipeline_started_at = time.time()
            with patch.object(mgr, "start_pipeline"):
                with patch.object(mgr, "_renderer_popen", None), \
                     patch.object(mgr, "_ffmpeg_popen", None), \
                     patch.object(mgr, "_renderer_pid", 1), \
                     patch.object(mgr, "_ffmpeg_pid", 2), \
                     patch("app.manager._pid_alive", side_effect=[True, False]):
                    mgr._ensure_pipeline_running()

        self.assertTrue(mgr._hw_fallback_forced)

    def test_start_pipeline_overrides_config_when_fallback_forced(self) -> None:
        mgr = self._make_manager()
        mgr._hw_fallback_forced = True
        captured_config: dict = {}

        def fake_resolve(config, _gpu):
            captured_config.update(config)
            return _sw_profile()

        with patch("app.manager.detect_gpu_capabilities", return_value={}), \
             patch("app.manager.resolve_ffmpeg_profile", side_effect=fake_resolve), \
             patch("app.manager._resolve_video_encoder_path", return_value=("libx264", [], "veryfast", "zerolatency", "software:libx264", "yuv420p")), \
             patch("app.manager._build_weather_ffmpeg_command", return_value=["ffmpeg"]), \
             patch.object(mgr, "_stop_pipeline_locked"), \
             patch.object(mgr, "_clean_weather_output"), \
             patch.object(mgr, "_maybe_fetch_state"), \
             patch("app.manager.subprocess.Popen") as mock_popen, \
             patch("app.manager._save_pid"):
            mock_proc = MagicMock()
            mock_proc.pid = 999
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc
            mgr.start_pipeline()

        self.assertEqual(captured_config.get("hardware_acceleration_mode"), "software_fallback")

    def test_start_pipeline_records_encoder_type_and_start_time(self) -> None:
        mgr = self._make_manager()
        before = time.time()

        with patch("app.manager.detect_gpu_capabilities", return_value={}), \
             patch("app.manager.resolve_ffmpeg_profile", return_value=_hw_profile()), \
             patch("app.manager._resolve_video_encoder_path", return_value=("h264_qsv", [], None, None, "hardware:intel", "nv12")), \
             patch("app.manager._build_weather_ffmpeg_command", return_value=["ffmpeg"]), \
             patch.object(mgr, "_stop_pipeline_locked"), \
             patch.object(mgr, "_clean_weather_output"), \
             patch.object(mgr, "_maybe_fetch_state"), \
             patch("app.manager.subprocess.Popen") as mock_popen, \
             patch("app.manager._save_pid"):
            mock_proc = MagicMock()
            mock_proc.pid = 999
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc
            mgr.start_pipeline()

        self.assertEqual(mgr._last_encoder_type, "hardware")
        self.assertGreaterEqual(mgr._pipeline_started_at, before)


if __name__ == "__main__":
    unittest.main()
