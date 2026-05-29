from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.ffmpeg_profiles import (
    DEFAULT_FFMPEG_PROFILE_NAME,
    HARDWARE_ACCELERATION_PROVIDERS,
    FFmpegProfile,
    resolve_ffmpeg_profile,
)
from app.manager import _build_audio_ffmpeg_args, _build_ffmpeg_command


class FFmpegProfileTests(unittest.TestCase):
    def test_resolve_ffmpeg_profile_uses_selected_name(self) -> None:
        profile = resolve_ffmpeg_profile(
            {
                "ffmpeg_profile": DEFAULT_FFMPEG_PROFILE_NAME,
                "resolution": "1920x1080",
                "segment_seconds": 4,
            }
        )

        self.assertEqual(profile.name, DEFAULT_FFMPEG_PROFILE_NAME)
        self.assertEqual(profile.resolution, "1920x1080")
        self.assertEqual(profile.video_codec, "libx264")
        self.assertEqual(profile.audio_codec, "aac")
        self.assertIsNone(profile.bitrate)
        self.assertEqual(profile.preset, "veryfast")
        self.assertEqual(profile.hls_segment_length, 4)
        self.assertEqual(profile.encoder_type, "software")
        self.assertEqual(profile.hardware_acceleration_provider, "software")

    def test_resolve_ffmpeg_profile_falls_back_to_default(self) -> None:
        profile = resolve_ffmpeg_profile({"ffmpeg_profile": "missing"})

        self.assertEqual(profile.name, DEFAULT_FFMPEG_PROFILE_NAME)

    def test_placeholder_hardware_acceleration_providers_exist(self) -> None:
        for name in ("nvidia", "intel", "amd", "vaapi"):
            with self.subTest(provider=name):
                provider = HARDWARE_ACCELERATION_PROVIDERS[name]
                self.assertEqual(provider.name, name)
                self.assertTrue(provider.placeholder)

    def test_build_ffmpeg_command_uses_profile_values(self) -> None:
        profile = FFmpegProfile(
            name="custom",
            resolution="640x360",
            video_codec="libx265",
            audio_codec="libopus",
            bitrate="900k",
            preset="faster",
            hls_segment_length=4,
            encoder_type="software",
            hardware_acceleration_provider="software",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)

        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "123",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertIn("-s", command)
        self.assertEqual(command[command.index("-s") + 1], "640x360")
        self.assertEqual(command[command.index("-c:v") + 1], "libx265")
        self.assertEqual(command[command.index("-preset") + 1], "faster")
        self.assertEqual(command[command.index("-b:v") + 1], "900k")
        self.assertEqual(command[command.index("-c:a") + 1], "libopus")
        self.assertEqual(command[command.index("-hls_time") + 1], "4")

    def test_audio_args_use_profile_codec_for_single_track_music(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            music_dir = Path(temp_dir) / "music"
            music_dir.mkdir()
            (music_dir / "track.mp3").write_bytes(b"demo")
            with patch("app.manager.MUSIC_DIR", music_dir):
                _, codec_args, _ = _build_audio_ffmpeg_args(
                    {
                        "music_mode": "single",
                        "music_single_file": "track.mp3",
                        "music_loop": True,
                    },
                    "libopus",
                )

        self.assertEqual(codec_args, ["-c:a", "libopus", "-b:a", "128k"])

    def test_audio_args_use_profile_codec_for_playlist_music(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            music_dir = base_dir / "music"
            data_dir = base_dir / "data"
            music_dir.mkdir()
            data_dir.mkdir()
            (music_dir / "track.mp3").write_bytes(b"demo")
            with patch("app.manager.MUSIC_DIR", music_dir), patch("app.manager.DATA_DIR", data_dir):
                _, codec_args, _ = _build_audio_ffmpeg_args(
                    {
                        "music_mode": "playlist",
                        "music_playlist_files": ["track.mp3"],
                        "music_loop": False,
                    },
                    "libopus",
                )

        self.assertEqual(codec_args, ["-c:a", "libopus", "-b:a", "128k"])


if __name__ == "__main__":
    unittest.main()
