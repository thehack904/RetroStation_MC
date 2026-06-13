from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[1]
    # Ensure the repo root is on sys.path so that the script's top-level
    # ``from app.gpu_capabilities import ...`` import resolves when this test
    # file is run directly (e.g. ``python tests/test_gpu_hwaccel_detect_v3.py``).
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    script_path = repo_root / "gpu_hwaccel_detect_v3.py"
    spec = importlib.util.spec_from_file_location("gpu_hwaccel_detect_v3", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GpuHwAccelDetectV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script_module()

    def test_json_output_prints_detected_capabilities(self) -> None:
        capabilities = {
            "detected_hardware_providers": ["nvidia"],
            "device_detected_providers": ["nvidia"],
            "ffmpeg_detected_encoders": ["h264_nvenc"],
            "message": "Hardware acceleration is ready.",
        }
        stdout = io.StringIO()
        with patch.object(self.module, "detect_gpu_capabilities", return_value=capabilities):
            with redirect_stdout(stdout):
                rc = self.module.main(["--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue()), capabilities)

    def test_text_output_reports_when_hardware_is_not_usable(self) -> None:
        capabilities = {
            "detected_hardware_providers": [],
            "device_detected_providers": ["intel"],
            "ffmpeg_detected_encoders": ["h264_qsv"],
            "message": "Hardware was detected, but no usable hardware encoder passed validation; software fallback is active.",
        }
        stdout = io.StringIO()
        with patch.object(self.module, "detect_gpu_capabilities", return_value=capabilities):
            with redirect_stdout(stdout):
                rc = self.module.main([])
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("Hardware acceleration usable: no", output)
        self.assertIn("Detected GPU providers: intel", output)
        self.assertIn("Hardware-ready providers: none", output)

    def test_text_output_reports_when_hardware_is_usable(self) -> None:
        capabilities = {
            "detected_hardware_providers": ["nvidia"],
            "device_detected_providers": ["nvidia"],
            "ffmpeg_detected_encoders": ["h264_nvenc"],
            "message": "Hardware acceleration is ready.",
        }
        stdout = io.StringIO()
        with patch.object(self.module, "detect_gpu_capabilities", return_value=capabilities):
            with redirect_stdout(stdout):
                rc = self.module.main([])
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("Hardware acceleration usable: yes", output)
        self.assertIn("Hardware-ready providers: nvidia", output)

    def test_returns_non_zero_when_detection_errors(self) -> None:
        stderr = io.StringIO()
        with patch.object(self.module, "detect_gpu_capabilities", side_effect=RuntimeError("boom")):
            with redirect_stderr(stderr):
                rc = self.module.main([])
        self.assertEqual(rc, 1)
        self.assertIn("GPU hardware acceleration detection failed: boom", stderr.getvalue())

    def test_test_mode_returns_zero_when_hardware_is_usable(self) -> None:
        capabilities = {
            "detected_hardware_providers": ["nvidia"],
            "device_detected_providers": ["nvidia"],
            "ffmpeg_detected_encoders": ["h264_nvenc"],
            "message": "Hardware acceleration is ready.",
            "providers": {
                "nvidia": {
                    "label": "NVIDIA NVENC",
                    "available": True,
                    "device_visible": True,
                    "visibility_reason": "NVIDIA device nodes are visible.",
                    "ffmpeg_encoders": ["h264_nvenc"],
                    "functional_probe_reason": "Functional probe for 'h264_nvenc' succeeded.",
                }
            },
        }
        stdout = io.StringIO()
        with patch.object(self.module, "detect_gpu_capabilities", return_value=capabilities):
            with redirect_stdout(stdout):
                rc = self.module.main(["--test"])
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("Test Mode", output)
        self.assertIn("PASS", output)
        self.assertIn("NVIDIA NVENC", output)
        self.assertIn("Hardware acceleration usable: yes", output)

    def test_test_mode_returns_nonzero_when_hardware_not_usable(self) -> None:
        capabilities = {
            "detected_hardware_providers": [],
            "device_detected_providers": ["intel"],
            "ffmpeg_detected_encoders": ["h264_qsv"],
            "message": "Hardware was detected, but no usable hardware encoder passed validation; software fallback is active.",
            "providers": {
                "intel": {
                    "label": "Intel QuickSync / QSV",
                    "available": False,
                    "device_visible": True,
                    "visibility_reason": "DRI render device with Intel DRM driver is visible.",
                    "ffmpeg_encoders": ["h264_qsv"],
                    "functional_probe_reason": "Functional probe for 'h264_qsv' exited with status 1.",
                }
            },
        }
        stdout = io.StringIO()
        with patch.object(self.module, "detect_gpu_capabilities", return_value=capabilities):
            with redirect_stdout(stdout):
                rc = self.module.main(["--test"])
        self.assertEqual(rc, 1)
        output = stdout.getvalue()
        self.assertIn("FAIL", output)
        self.assertIn("Hardware acceleration usable: no", output)


if __name__ == "__main__":
    unittest.main()
