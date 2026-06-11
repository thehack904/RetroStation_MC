from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.ffmpeg_profiles import (
    DEFAULT_FFMPEG_PROFILE_NAME,
    DEFAULT_HARDWARE_ACCELERATION_MODE,
    HARDWARE_ACCELERATION_PROVIDERS,
    FFmpegProfile,
    resolve_ffmpeg_profile,
)
from app.manager import _build_audio_ffmpeg_args, _build_ffmpeg_command, _build_weather_ffmpeg_command


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
        self.assertEqual(profile.tune, "zerolatency")
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

    def test_resolve_ffmpeg_profile_prefers_software_when_mode_requests_fallback(self) -> None:
        profile = resolve_ffmpeg_profile(
            {
                "hardware_acceleration_mode": "software_fallback",
            },
            {
                "detected_hardware_providers": ["nvidia"],
                "providers": {
                    "nvidia": {
                        "available": True,
                        "ffmpeg_encoders": ["h264_nvenc"],
                    }
                },
            },
        )

        self.assertEqual(profile.video_codec, "libx264")
        self.assertEqual(profile.encoder_type, "software")

    def test_resolve_ffmpeg_profile_uses_detected_hardware_when_enabled(self) -> None:
        profile = resolve_ffmpeg_profile(
            {
                "hardware_acceleration_mode": "hardware_if_available",
            },
            {
                "detected_hardware_providers": ["nvidia"],
                "providers": {
                    "nvidia": {
                        "available": True,
                        "ffmpeg_encoders": ["h264_nvenc", "hevc_nvenc"],
                    }
                },
            },
        )

        self.assertEqual(profile.video_codec, "h264_nvenc")
        self.assertIsNone(profile.tune)
        self.assertEqual(profile.encoder_type, "hardware")
        self.assertEqual(profile.hardware_acceleration_provider, "nvidia")

    def test_invalid_hardware_acceleration_mode_falls_back_to_default_mode(self) -> None:
        profile = resolve_ffmpeg_profile(
            {
                "hardware_acceleration_mode": "unsupported",
            }
        )

        self.assertEqual(DEFAULT_HARDWARE_ACCELERATION_MODE, "software_fallback")
        self.assertEqual(profile.video_codec, "libx264")

    def test_software_profile_command_generation_uses_software_encoder_path(self) -> None:
        profile = resolve_ffmpeg_profile(
            {
                "ffmpeg_profile": DEFAULT_FFMPEG_PROFILE_NAME,
                "hardware_acceleration_mode": "software_fallback",
                "resolution": "854x480",
                "segment_seconds": 5,
            }
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "50",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")
        self.assertEqual(command[command.index("-tune") + 1], "zerolatency")
        self.assertEqual(command[command.index("-s") + 1], "854x480")
        self.assertEqual(command[command.index("-hls_time") + 1], "5")

    def test_gpu_profile_command_generation_uses_detected_hardware_encoder_path(self) -> None:
        profile = resolve_ffmpeg_profile(
            {"hardware_acceleration_mode": "hardware_if_available"},
            {
                "detected_hardware_providers": ["nvidia"],
                "providers": {
                    "nvidia": {"available": True, "ffmpeg_encoders": ["h264_nvenc"]},
                },
            },
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "75",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertEqual(command[command.index("-c:v") + 1], "h264_nvenc")
        self.assertIn("-rc", command)
        self.assertEqual(command[command.index("-rc") + 1], "vbr")
        self.assertNotIn("-preset", command)
        self.assertNotIn("-tune", command)

    def test_hardware_mode_with_no_supported_encoder_falls_back_to_software_command(self) -> None:
        profile = resolve_ffmpeg_profile(
            {"hardware_acceleration_mode": "hardware_if_available"},
            {
                "detected_hardware_providers": ["nvidia"],
                "providers": {
                    "nvidia": {"available": True, "ffmpeg_encoders": ["hevc_nvenc"]},
                },
            },
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "80",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")
        self.assertEqual(command[command.index("-tune") + 1], "zerolatency")

    def test_invalid_profile_name_falls_back_to_default_command_generation(self) -> None:
        profile = resolve_ffmpeg_profile({"ffmpeg_profile": "invalid-profile-name"})
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "90",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertEqual(profile.name, DEFAULT_FFMPEG_PROFILE_NAME)
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")
        self.assertEqual(command[command.index("-tune") + 1], "zerolatency")

    def test_build_ffmpeg_command_uses_profile_values(self) -> None:
        profile = FFmpegProfile(
            name="custom",
            resolution="640x360",
            video_codec="libx265",
            audio_codec="libopus",
            bitrate="900k",
            preset="faster",
            tune="zerolatency",
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

    def test_build_ffmpeg_command_skips_tune_when_profile_disables_it(self) -> None:
        profile = FFmpegProfile(
            name="hardware-auto",
            resolution="640x360",
            video_codec="h264_nvenc",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="nvidia",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "456",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertNotIn("-tune", command)

    def test_build_weather_ffmpeg_command_uses_profile_values(self) -> None:
        profile = FFmpegProfile(
            name="weather-hardware-auto",
            resolution="640x360",
            video_codec="h264_nvenc",
            audio_codec="aac",
            bitrate="900k",
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="nvidia",
        )

        command = _build_weather_ffmpeg_command(profile, "15", "789")

        self.assertIn("-s", command)
        self.assertEqual(command[command.index("-s") + 1], "640x360")
        self.assertEqual(command[command.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(command[command.index("-b:v") + 1], "900k")
        self.assertNotIn("-preset", command)
        self.assertNotIn("-tune", command)

    def test_build_ffmpeg_command_adds_hardware_specific_encoder_args(self) -> None:
        expected_args = {
            "h264_nvenc": ["-rc", "vbr", "-cq", "23", "-forced-idr", "1"],
            "h264_qsv": ["-look_ahead", "0", "-global_quality", "23"],
            "h264_amf": ["-quality", "balanced"],
            "h264_vaapi": ["-qp", "23"],
        }
        for codec, args in expected_args.items():
            with self.subTest(codec=codec):
                profile = FFmpegProfile(
                    name=f"{codec}-profile",
                    resolution="640x360",
                    video_codec=codec,
                    audio_codec="aac",
                    bitrate=None,
                    preset=None,
                    tune=None,
                    hls_segment_length=6,
                    encoder_type="hardware",
                    hardware_acceleration_provider="auto",
                )
                audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
                command = _build_ffmpeg_command(
                    profile,
                    "15",
                    audio_input_args,
                    audio_codec_args,
                    audio_map_args,
                    "100",
                    Path("/tmp/output/guide.m3u8"),
                )
                self.assertEqual(command[command.index("-c:v") + 1], codec)
                for index in range(0, len(args), 2):
                    flag, value = args[index], args[index + 1]
                    self.assertIn(flag, command)
                    self.assertEqual(command[command.index(flag) + 1], value)

    def test_build_ffmpeg_command_falls_back_to_libx264_for_unsupported_codec(self) -> None:
        profile = FFmpegProfile(
            name="unknown-hw",
            resolution="640x360",
            video_codec="h264_unknown",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="unknown",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "456",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertIn("-preset", command)
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")
        self.assertIn("-tune", command)
        self.assertEqual(command[command.index("-tune") + 1], "zerolatency")

    def test_build_weather_ffmpeg_command_falls_back_to_libx264_for_unsupported_codec(self) -> None:
        profile = FFmpegProfile(
            name="weather-unknown-hw",
            resolution="640x360",
            video_codec="h264_unknown",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="unknown",
        )
        command = _build_weather_ffmpeg_command(profile, "10", "123")

        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertIn("-preset", command)
        self.assertIn("-tune", command)

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
