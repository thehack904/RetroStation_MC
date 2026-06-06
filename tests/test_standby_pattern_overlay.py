from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.manager import GuideManager, _build_custom_standby_image

OVERLAY_BAND_COLOR = (15, 15, 15)


class StandbyPatternOverlayTests(unittest.TestCase):
    def test_custom_standby_image_uses_default_50_percent_overlay_band(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "custom.png"
            Image.new("RGB", (320, 180), (250, 250, 250)).save(src)

            frame = _build_custom_standby_image(src, 320, 180, "Guide is Loading...")
            alpha = int(255 * 0.5)
            expected_channel = int(
                round(((OVERLAY_BAND_COLOR[0] * alpha) + (250 * (255 - alpha))) / 255)
            )
            self.assertEqual(frame.getpixel((5, 170)), (expected_channel, expected_channel, expected_channel))

    def test_custom_standby_image_supports_adjustable_overlay_opacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "custom.png"
            Image.new("RGB", (320, 180), (250, 250, 250)).save(src)

            lighter = _build_custom_standby_image(
                src,
                320,
                180,
                "Guide is Loading...",
                overlay_opacity_percent=20,
            )
            darker = _build_custom_standby_image(
                src,
                320,
                180,
                "Guide is Loading...",
                overlay_opacity_percent=80,
            )

            self.assertGreater(lighter.getpixel((5, 170))[0], darker.getpixel((5, 170))[0])

    def test_custom_standby_image_can_disable_overlay_entirely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "custom.png"
            Image.new("RGB", (320, 180), (250, 250, 250)).save(src)

            frame = _build_custom_standby_image(
                src,
                320,
                180,
                "Guide is Loading...",
                overlay_enabled=False,
            )

            self.assertEqual(frame.getpixel((5, 170)), (250, 250, 250))

    def test_image_validation_accepts_valid_image_and_rejects_invalid_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            valid_path = Path(tmp_dir) / "valid.png"
            invalid_path = Path(tmp_dir) / "invalid.png"
            Image.new("RGB", (16, 16), (255, 255, 255)).save(valid_path)
            invalid_path.write_bytes(b"not-an-image")

            manager = GuideManager.__new__(GuideManager)
            self.assertTrue(manager._is_valid_image_file(valid_path))
            self.assertFalse(manager._is_valid_image_file(invalid_path))


if __name__ == "__main__":
    unittest.main()
