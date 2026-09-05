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
from app.manager import _build_audio_ffmpeg_args, _build_ffmpeg_command, _build_guide_preview_ffmpeg_args, _build_weather_ffmpeg_command, _resolve_hw_device_init_args


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

    def test_resolve_ffmpeg_profile_prefers_vaapi_over_amf_when_both_are_available(self) -> None:
        profile = resolve_ffmpeg_profile(
            {
                "hardware_acceleration_mode": "hardware_if_available",
            },
            {
                "detected_hardware_providers": ["amd", "vaapi"],
                "providers": {
                    "amd": {
                        "available": True,
                        "ffmpeg_encoders": ["h264_amf"],
                    },
                    "vaapi": {
                        "available": True,
                        "ffmpeg_encoders": ["h264_vaapi"],
                    },
                },
            },
        )

        self.assertEqual(profile.video_codec, "h264_vaapi")
        self.assertEqual(profile.hardware_acceleration_provider, "vaapi")

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

    def test_build_weather_ffmpeg_command_uses_looped_single_track_when_configured(self) -> None:
        profile = FFmpegProfile(
            name="weather-single-music",
            resolution="640x360",
            video_codec="libx264",
            audio_codec="aac",
            bitrate=None,
            preset="veryfast",
            tune="zerolatency",
            hls_segment_length=6,
            encoder_type="software",
            hardware_acceleration_provider="software",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            music_dir = base_dir / "weather_music"
            music_dir.mkdir()
            track = music_dir / "forecast.mp3"
            track.write_bytes(b"demo")
            with patch("app.manager.WEATHER_MUSIC_DIR", music_dir):
                command = _build_weather_ffmpeg_command(
                    profile,
                    "10",
                    "123",
                    {
                        "weather_music_mode": "single",
                        "weather_music_single_file": "forecast.mp3",
                        "weather_music_loop": True,
                    },
                )

        self.assertIn("-stream_loop", command)
        self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
        self.assertIn(str(track), command)
        self.assertIn("-map", command)
        self.assertEqual(command[command.index("-map") + 1], "0:v")
        self.assertEqual(command[command.index("-map", command.index("-map") + 1) + 1], "1:a")

    def test_build_weather_ffmpeg_command_uses_playlist_mode_when_configured(self) -> None:
        profile = FFmpegProfile(
            name="weather-playlist-music",
            resolution="640x360",
            video_codec="libx264",
            audio_codec="aac",
            bitrate=None,
            preset="veryfast",
            tune="zerolatency",
            hls_segment_length=6,
            encoder_type="software",
            hardware_acceleration_provider="software",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            data_dir = base_dir / "data"
            weather_music_dir = base_dir / "weather_music"
            data_dir.mkdir()
            weather_music_dir.mkdir()
            track1 = weather_music_dir / "w1.mp3"
            track2 = weather_music_dir / "w2.mp3"
            track1.write_bytes(b"demo")
            track2.write_bytes(b"demo")
            with patch("app.manager.DATA_DIR", data_dir), patch("app.manager.WEATHER_MUSIC_DIR", weather_music_dir):
                command = _build_weather_ffmpeg_command(
                    profile,
                    "10",
                    "123",
                    {
                        "weather_music_mode": "playlist",
                        "weather_music_playlist_files": ["w1.mp3", "w2.mp3"],
                        "weather_music_loop": False,
                    },
                )

        self.assertIn("-f", command)
        self.assertIn("concat", command)
        self.assertIn("-filter_complex", command)
        self.assertIn("amix=inputs=2:duration=longest:normalize=0[outa]", " ".join(command))

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

    def test_intel_qsv_command_uses_nv12_pixel_format(self) -> None:
        """Intel QSV (h264_qsv) requires nv12 as the output pixel format, not
        yuv420p.  A mismatch causes FFmpeg to reject the stream with an
        'invalid or not supported pixel format' error."""
        profile = FFmpegProfile(
            name="intel_hardware_auto",
            resolution="1280x720",
            video_codec="h264_qsv",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="intel",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "200",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertEqual(command[command.index("-c:v") + 1], "h264_qsv")
        # The command has two -pix_fmt flags: the input (-pix_fmt rgb24) and
        # the output encoder format.  Verify the output (second) one is nv12.
        pix_fmt_indices = [i for i, arg in enumerate(command) if arg == "-pix_fmt"]
        self.assertGreaterEqual(len(pix_fmt_indices), 2, "Expected both an input and an output -pix_fmt")
        output_pix_fmt = command[pix_fmt_indices[-1] + 1]
        self.assertEqual(output_pix_fmt, "nv12")

    def test_intel_qsv_weather_command_uses_nv12_pixel_format(self) -> None:
        """Intel QSV requires nv12 in the weather pipeline as well."""
        profile = FFmpegProfile(
            name="intel_hardware_auto",
            resolution="1280x720",
            video_codec="h264_qsv",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="intel",
        )
        command = _build_weather_ffmpeg_command(profile, "15", "300")

        self.assertEqual(command[command.index("-c:v") + 1], "h264_qsv")
        pix_fmt_indices = [i for i, arg in enumerate(command) if arg == "-pix_fmt"]
        self.assertGreaterEqual(len(pix_fmt_indices), 2, "Expected both an input and an output -pix_fmt")
        output_pix_fmt = command[pix_fmt_indices[-1] + 1]
        self.assertEqual(output_pix_fmt, "nv12")

    def test_software_encoder_command_uses_yuv420p_pixel_format(self) -> None:
        """Software (libx264) path must keep yuv420p to avoid colour/format regressions."""
        profile = resolve_ffmpeg_profile(
            {
                "ffmpeg_profile": DEFAULT_FFMPEG_PROFILE_NAME,
                "hardware_acceleration_mode": "software_fallback",
            }
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "400",
            Path("/tmp/output/guide.m3u8"),
        )

        pix_fmt_indices = [i for i, arg in enumerate(command) if arg == "-pix_fmt"]
        self.assertGreaterEqual(len(pix_fmt_indices), 2, "Expected both an input and an output -pix_fmt")
        output_pix_fmt = command[pix_fmt_indices[-1] + 1]
        self.assertEqual(output_pix_fmt, "yuv420p")

    def test_hardware_encoder_pixel_format_per_provider(self) -> None:
        """Each hardware encoder uses the correct pixel format for its API."""
        expected_pix_fmts = {
            "h264_nvenc": "yuv420p",
            "h264_qsv":   "nv12",
            "h264_amf":   "nv12",
        }
        for codec, expected_fmt in expected_pix_fmts.items():
            with self.subTest(codec=codec):
                profile = FFmpegProfile(
                    name=f"{codec}-profile",
                    resolution="1280x720",
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
                    "500",
                    Path("/tmp/output/guide.m3u8"),
                )
                pix_fmt_indices = [i for i, arg in enumerate(command) if arg == "-pix_fmt"]
                self.assertGreaterEqual(len(pix_fmt_indices), 2, f"{codec}: expected input and output -pix_fmt")
                output_pix_fmt = command[pix_fmt_indices[-1] + 1]
                self.assertEqual(
                    output_pix_fmt,
                    expected_fmt,
                    msg=f"{codec} should use {expected_fmt!r}",
                )


    def test_resolve_hw_device_init_args_returns_qsv_init_for_intel_codecs(self) -> None:
        """All QSV codec variants must trigger -init_hw_device qsv=hw."""
        for codec in ("h264_qsv", "hevc_qsv", "av1_qsv"):
            with self.subTest(codec=codec):
                args = _resolve_hw_device_init_args(codec)
                self.assertEqual(args, ["-init_hw_device", "qsv=hw"])

    def test_resolve_hw_device_init_args_returns_empty_for_non_qsv(self) -> None:
        """Non-QSV encoders must not inject any device init arguments."""
        for codec in ("libx264", "h264_nvenc", "h264_amf", ""):
            with self.subTest(codec=codec):
                args = _resolve_hw_device_init_args(codec)
                self.assertEqual(args, [])

    def test_vaapi_device_and_hwupload_are_added_for_guide(self) -> None:
        profile = FFmpegProfile(
            name="vaapi", resolution="1280x720", video_codec="h264_vaapi",
            audio_codec="aac", bitrate=None, preset=None, tune=None,
            hls_segment_length=6, encoder_type="hardware",
            hardware_acceleration_provider="vaapi",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile, "15", audio_input_args, audio_codec_args, audio_map_args,
            "600", Path("/tmp/output/guide.m3u8"),
        )
        self.assertIn("-vaapi_device", command)
        self.assertEqual(command[command.index("-vaapi_device") + 1], "/dev/dri/renderD128")
        self.assertLess(command.index("-vaapi_device"), command.index("-i"))
        self.assertIn("-vf", command)
        self.assertEqual(command[command.index("-vf") + 1], "format=nv12,hwupload")
        # VA-API consumes a hardware surface; do not force an output -pix_fmt nv12.
        pix_fmt_indices = [i for i, arg in enumerate(command) if arg == "-pix_fmt"]
        self.assertEqual(len(pix_fmt_indices), 1)
        self.assertEqual(command[pix_fmt_indices[0] + 1], "rgb24")

    def test_external_guide_preview_can_use_shared_normalized_source(self) -> None:
        cfg = {
            "guide_preview_enabled": True,
            "guide_preview_source_type": "url",
            "guide_preview_url": "http://media.lan:8409/iptv/channel/2.m3u8",
            "resolution": "1280x720",
            "aspect_ratio": "16:9",
            "fps": 15,
        }
        normalized = "/tmp/normalized-preview.m3u8"
        with patch("app.manager.resolve_preview_source", return_value=cfg["guide_preview_url"]):
            input_args, filter_args, _, active = _build_guide_preview_ffmpeg_args(cfg, source_override=normalized)
        self.assertTrue(active)
        self.assertIn(normalized, input_args)
        graph = filter_args[filter_args.index("-filter_complex") + 1]
        self.assertNotIn("fps=15", graph)
        self.assertIn("[1:v]scale=", graph)
        self.assertIn("setsar=1", graph)
        self.assertIn("[guidepreview]", graph)
        self.assertIn("overlay=", graph)

    def test_local_guide_preview_does_not_add_live_source_normalization(self) -> None:
        cfg = {
            "guide_preview_enabled": True,
            "guide_preview_source_type": "file",
            "resolution": "1280x720",
            "aspect_ratio": "16:9",
            "fps": 15,
        }
        with patch("app.manager.resolve_preview_source", return_value="/tmp/bbb.mp4"):
            _, filter_args, _, active = _build_guide_preview_ffmpeg_args(cfg)
        self.assertTrue(active)
        graph = filter_args[filter_args.index("-filter_complex") + 1]
        self.assertNotIn("fps=15,format=yuv420p", graph)
        self.assertIn("[1:v]scale=", graph)

    def test_vaapi_preview_filter_uploads_filter_complex_output(self) -> None:
        profile = FFmpegProfile(
            name="vaapi", resolution="1280x720", video_codec="h264_vaapi",
            audio_codec="aac", bitrate=None, preset=None, tune=None,
            hls_segment_length=6, encoder_type="hardware",
            hardware_acceleration_provider="vaapi",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile, "15", audio_input_args, audio_codec_args, audio_map_args,
            "601", Path("/tmp/output/guide.m3u8"),
            preview_input_args=["-f", "lavfi", "-i", "testsrc=size=320x180"],
            video_filter_args=["-filter_complex", "[0:v][1:v]overlay=0:0[vout]"],
            video_map_args=["-map", "[vout]"],
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[vout]format=nv12,hwupload[vout_hw]", graph)
        self.assertIn("[vout_hw]", command)

    def test_resolve_hw_device_init_args_returns_vaapi_device(self) -> None:
        args = _resolve_hw_device_init_args("h264_vaapi")
        self.assertEqual(args, ["-vaapi_device", "/dev/dri/renderD128"])

    def test_intel_qsv_guide_command_includes_init_hw_device_before_first_input(self) -> None:
        """Intel QSV guide pipeline must include -init_hw_device qsv=hw before -i."""
        profile = FFmpegProfile(
            name="intel_hardware_auto",
            resolution="1280x720",
            video_codec="h264_qsv",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="intel",
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "200",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertIn("-init_hw_device", command)
        init_idx = command.index("-init_hw_device")
        self.assertEqual(command[init_idx + 1], "qsv=hw")
        # Must appear before the first -i (stdin video input).
        first_input_idx = command.index("-i")
        self.assertLess(init_idx, first_input_idx,
                        "-init_hw_device must precede the first -i")

    def test_intel_qsv_weather_command_includes_init_hw_device_before_first_input(self) -> None:
        """Intel QSV weather pipeline must include -init_hw_device qsv=hw before -i."""
        profile = FFmpegProfile(
            name="intel_hardware_auto",
            resolution="1280x720",
            video_codec="h264_qsv",
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=6,
            encoder_type="hardware",
            hardware_acceleration_provider="intel",
        )
        command = _build_weather_ffmpeg_command(profile, "15", "300")

        self.assertIn("-init_hw_device", command)
        init_idx = command.index("-init_hw_device")
        self.assertEqual(command[init_idx + 1], "qsv=hw")
        first_input_idx = command.index("-i")
        self.assertLess(init_idx, first_input_idx,
                        "-init_hw_device must precede the first -i")

    def test_software_guide_command_has_no_init_hw_device(self) -> None:
        """Software (libx264) guide pipeline must NOT include -init_hw_device."""
        profile = resolve_ffmpeg_profile(
            {
                "ffmpeg_profile": DEFAULT_FFMPEG_PROFILE_NAME,
                "hardware_acceleration_mode": "software_fallback",
            }
        )
        audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args({}, profile.audio_codec)
        command = _build_ffmpeg_command(
            profile,
            "15",
            audio_input_args,
            audio_codec_args,
            audio_map_args,
            "400",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertNotIn("-init_hw_device", command)

    def test_nvidia_guide_command_has_no_init_hw_device(self) -> None:
        """NVIDIA NVENC guide pipeline must NOT include -init_hw_device."""
        profile = FFmpegProfile(
            name="nvidia_hardware_auto",
            resolution="1280x720",
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
            "500",
            Path("/tmp/output/guide.m3u8"),
        )

        self.assertNotIn("-init_hw_device", command)


if __name__ == "__main__":
    unittest.main()
