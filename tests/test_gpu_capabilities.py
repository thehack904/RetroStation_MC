from __future__ import annotations

import unittest
from unittest.mock import patch

from app.gpu_capabilities import detect_gpu_capabilities, _provider_device_visible


class GpuCapabilitiesTests(unittest.TestCase):
    def test_missing_ffmpeg_reports_non_fatal_software_fallback(self) -> None:
        with patch("app.gpu_capabilities.shutil.which", return_value=None):
            capabilities = detect_gpu_capabilities(ffmpeg_bin="ffmpeg")

        self.assertFalse(capabilities["hardware_available"])
        self.assertFalse(capabilities["ffmpeg_available"])
        self.assertTrue(capabilities["software_fallback"]["available"])
        self.assertIn("software fallback", capabilities["message"].lower())

    def test_detects_provider_when_device_and_encoder_are_available(self) -> None:
        fake_ffmpeg = {"available": True, "error": "", "encoders": frozenset({"h264_nvenc"})}
        with patch("app.gpu_capabilities._probe_ffmpeg_encoders", return_value=fake_ffmpeg), patch(
            "app.gpu_capabilities._running_in_docker",
            return_value=False,
        ), patch("app.gpu_capabilities._provider_device_visible", return_value=(True, "ok")):
            capabilities = detect_gpu_capabilities()

        self.assertIn("nvidia", capabilities["detected_hardware_providers"])
        self.assertTrue(capabilities["providers"]["nvidia"]["available"])
        self.assertEqual(capabilities["providers"]["nvidia"]["ffmpeg_encoders"], ["h264_nvenc"])

    def test_reports_docker_device_visibility(self) -> None:
        fake_ffmpeg = {"available": True, "error": "", "encoders": frozenset()}
        with patch("app.gpu_capabilities._probe_ffmpeg_encoders", return_value=fake_ffmpeg), patch(
            "app.gpu_capabilities._running_in_docker",
            return_value=True,
        ), patch(
            "app.gpu_capabilities._provider_device_visible",
            side_effect=[(True, "nvidia"), (False, "intel"), (False, "amd"), (False, "vaapi")],
        ):
            capabilities = detect_gpu_capabilities()

        self.assertTrue(capabilities["running_in_docker"])
        self.assertEqual(capabilities["docker_visible_providers"], ["nvidia"])
        self.assertFalse(capabilities["hardware_available"])

    def test_filters_encoder_report_to_visible_hardware_providers(self) -> None:
        fake_ffmpeg = {"available": True, "error": "", "encoders": frozenset({"h264_nvenc", "h264_vaapi", "h264_qsv"})}
        with patch("app.gpu_capabilities._probe_ffmpeg_encoders", return_value=fake_ffmpeg), patch(
            "app.gpu_capabilities._running_in_docker",
            return_value=False,
        ), patch(
            "app.gpu_capabilities._provider_device_visible",
            side_effect=[(False, "nvidia"), (False, "intel"), (False, "amd"), (True, "vaapi")],
        ):
            capabilities = detect_gpu_capabilities()

        self.assertEqual(capabilities["ffmpeg_detected_encoders"], ["h264_vaapi"])

    def test_intel_requires_intel_drm_driver(self) -> None:
        with patch("app.gpu_capabilities._dri_device_visible", return_value=True), patch(
            "app.gpu_capabilities._drm_kernel_drivers",
            return_value=frozenset({"radeon"}),
        ):
            visible, _ = _provider_device_visible("intel", in_docker=False)

        self.assertFalse(visible)


if __name__ == "__main__":
    unittest.main()
