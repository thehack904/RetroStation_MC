from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.hdhomerun_hls import HDHomeRunHLSSession, build_hdhomerun_hls_command


SD_INTERLACED_MPEG2 = {
    "codec": "mpeg2video", "width": 704, "height": 480,
    "field_order": "tt", "interlaced": True, "fps": 30000 / 1001,
}
HD_INTERLACED_MPEG2 = {
    "codec": "mpeg2video", "width": 1920, "height": 1080,
    "field_order": "tt", "interlaced": True, "fps": 30000 / 1001,
}
HD_PROGRESSIVE_MPEG2 = {
    "codec": "mpeg2video", "width": 1280, "height": 720,
    "field_order": "progressive", "interlaced": False, "fps": 60000 / 1001,
}
H264_INTERLACED = {
    "codec": "h264", "width": 640, "height": 480,
    "field_order": "tt", "interlaced": True, "fps": 30000 / 1001,
}


class HDHomeRunHLSPipelineTests(unittest.TestCase):
    def test_software_command_matches_rsmc_hls_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            command = build_hdhomerun_hls_command(
                "http://hdhr.local:5004/auto/v4.1",
                out / "hdhr_test.m3u8",
                out / "hdhr_test_%d.ts",
                use_vaapi=False,
                source_profile=SD_INTERLACED_MPEG2,
            )
        joined = " ".join(command)
        self.assertIn("-c:v libx264", joined)
        self.assertIn("yadif=mode=send_field:parity=auto:deint=all,scale=720:480", joined)
        self.assertIn("-c:a aac", joined)
        self.assertIn("-hls_time 6", joined)
        self.assertIn("-hls_segment_type mpegts", joined)
        self.assertIn("-hls_list_size 10", joined)

    def test_interlaced_sd_vaapi_deinterlaces_and_normalizes_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            command = build_hdhomerun_hls_command(
                "http://hdhr.local:5004/auto/v11.4",
                out / "test.m3u8", out / "test_%d.ts",
                use_vaapi=True, vaapi_device="/dev/dri/renderD128",
                hardware_decode=False, source_profile=SD_INTERLACED_MPEG2,
            )
        joined = " ".join(command)
        self.assertIn("-vaapi_device /dev/dri/renderD128", joined)
        self.assertIn("-vf yadif=mode=send_field:parity=auto:deint=all,scale=720:480,format=nv12,hwupload", joined)
        self.assertIn("-c:v h264_vaapi", joined)
        self.assertIn("-qp 23", joined)
        self.assertIn("-g 120", joined)
        self.assertIn("-r 60000/1001 -fps_mode cfr", joined)
        self.assertIn("-af aresample=async=1:first_pts=0", joined)
        self.assertNotIn("-hwaccel vaapi", joined)

    def test_interlaced_1080i_vaapi_deinterlaces_at_native_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            command = build_hdhomerun_hls_command(
                "http://hdhr.local:5004/auto/v21.1",
                out / "test.m3u8", out / "test_%d.ts",
                use_vaapi=True, source_profile=HD_INTERLACED_MPEG2,
            )
        joined = " ".join(command)
        self.assertIn("-vf yadif=mode=send_field:parity=auto:deint=all,format=nv12,hwupload", joined)
        self.assertNotIn("scale=", joined)
        self.assertIn("-c:v h264_vaapi", joined)
        self.assertIn("-g 120", joined)
        self.assertIn("-r 60000/1001 -fps_mode cfr", joined)
        self.assertIn("-af aresample=async=1:first_pts=0", joined)

    def test_progressive_mpeg2_can_use_full_vaapi_decode_encode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            command = build_hdhomerun_hls_command(
                "http://hdhr.local:5004/auto/v4.1",
                out / "test.m3u8", out / "test_%d.ts",
                use_vaapi=True, vaapi_device="/dev/dri/renderD128",
                hardware_decode=True, source_profile=HD_PROGRESSIVE_MPEG2,
            )
        joined = " ".join(command)
        self.assertIn("-hwaccel vaapi", joined)
        self.assertIn("-hwaccel_device /dev/dri/renderD128", joined)
        self.assertIn("-hwaccel_output_format vaapi", joined)
        self.assertIn("-c:v h264_vaapi", joined)
        self.assertIn("-g 120", joined)
        self.assertNotIn("hwupload", joined)

    def test_h264_source_is_video_copy_with_aac_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            command = build_hdhomerun_hls_command(
                "http://hdhr.local:5004/auto/v23.4",
                out / "test.m3u8", out / "test_%d.ts",
                source_profile=H264_INTERLACED,
                video_copy=True,
            )
        joined = " ".join(command)
        self.assertIn("-c:v copy", joined)
        self.assertIn("-c:a aac", joined)
        self.assertNotIn("h264_vaapi", joined)
        self.assertNotIn("libx264", joined)
        self.assertNotIn("yadif", joined)
        self.assertNotIn("aresample=async=1:first_pts=0", joined)
        self.assertNotIn("-fps_mode cfr", joined)

    def test_session_uses_stable_safe_output_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = HDHomeRunHLSSession(
                "7.1/Alpha Station", "Alpha Station",
                "http://hdhr.local:5004/auto/v7.1", Path(tmp),
            )
            self.assertTrue(session.playlist_path.name.startswith("hdhr_"))
            self.assertNotIn("/", session.playlist_path.name)
            self.assertTrue(session.segment_pattern.name.endswith("_%d.ts"))

    def test_playlist_ready_rejects_zero_duration_startup_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            session = HDHomeRunHLSSession("alpha", "Alpha", "http://hdhr/auto/v7.1", out)
            session.playlist_path.write_text(
                "#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXTINF:0.000000,\n"
                f"{session.file_prefix}_1.ts\n", encoding="utf-8",
            )
            (out / f"{session.file_prefix}_1.ts").write_bytes(b"x")
            self.assertFalse(session._playlist_ready())

    def test_playlist_ready_requires_three_completed_positive_duration_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            session = HDHomeRunHLSSession("alpha", "Alpha", "http://hdhr/auto/v7.1", out)
            playlist = ["#EXTM3U", "#EXT-X-TARGETDURATION:6"]
            for index in range(1, 4):
                segment = out / f"{session.file_prefix}_{index}.ts"
                segment.write_bytes(b"mpegts")
                playlist.extend(["#EXTINF:6.006000,", segment.name])
                session.playlist_path.write_text("\n".join(playlist) + "\n", encoding="utf-8")
                self.assertEqual(session._playlist_ready(), index >= 3)

    def test_h264_session_prefers_copy_even_when_vaapi_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = HDHomeRunHLSSession("23.4", "MVSGLD", "http://hdhr/auto/v23.4", Path(tmp))
            session.source_profile = dict(H264_INTERLACED)
            attempts = []

            def fake_spawn(*, use_vaapi=False, hardware_decode=False, video_copy=False):
                attempts.append((use_vaapi, hardware_decode, video_copy))
                session.encoder = "H.264 video copy" if video_copy else "other"
                session.proc = _FakeProc()

            with patch.object(session, "_spawn", side_effect=fake_spawn), \
                 patch.object(session, "_wait_until_ready", return_value=True):
                self.assertTrue(session.start(prefer_vaapi=True))
            self.assertEqual(attempts, [(False, False, True)])
            self.assertEqual(session.encoder, "H.264 video copy")
            session.stop(remove_files=False)

    def test_interlaced_mpeg2_skips_hardware_decode_but_uses_vaapi_encode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = HDHomeRunHLSSession("21.1", "KTXA", "http://hdhr/auto/v21.1", Path(tmp))
            session.source_profile = dict(HD_INTERLACED_MPEG2)
            attempts = []

            def fake_spawn(*, use_vaapi=False, hardware_decode=False, video_copy=False):
                attempts.append((use_vaapi, hardware_decode, video_copy))
                session.encoder = "test"
                session.proc = _FakeProc()

            with patch.object(session, "_spawn", side_effect=fake_spawn), \
                 patch.object(session, "_wait_until_ready", return_value=True):
                self.assertTrue(session.start(prefer_vaapi=True))
            self.assertEqual(attempts, [(True, False, False)])
            session.stop(remove_files=False)

    def test_progressive_mpeg2_tries_full_vaapi_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = HDHomeRunHLSSession("4.1", "KDFW", "http://hdhr/auto/v4.1", Path(tmp))
            session.source_profile = dict(HD_PROGRESSIVE_MPEG2)
            attempts = []

            def fake_spawn(*, use_vaapi=False, hardware_decode=False, video_copy=False):
                attempts.append((use_vaapi, hardware_decode, video_copy))
                session.encoder = "test"
                session.proc = _FakeProc()

            with patch.object(session, "_spawn", side_effect=fake_spawn), \
                 patch.object(session, "_wait_until_ready", return_value=True):
                self.assertTrue(session.start(prefer_vaapi=True))
            self.assertEqual(attempts, [(True, True, False)])
            session.stop(remove_files=False)

    def test_interlaced_vaapi_failure_falls_back_to_progressive_libx264(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = HDHomeRunHLSSession("11.4", "Outlaw", "http://hdhr/auto/v11.4", Path(tmp))
            session.source_profile = dict(SD_INTERLACED_MPEG2)
            attempts = []

            def fake_spawn(*, use_vaapi=False, hardware_decode=False, video_copy=False):
                attempts.append((use_vaapi, hardware_decode, video_copy))
                session.encoder = "test"
                session.proc = _FakeProc()

            def fake_wait(_timeout):
                return len(attempts) >= 2

            with patch.object(session, "_spawn", side_effect=fake_spawn), \
                 patch.object(session, "_wait_until_ready", side_effect=fake_wait):
                self.assertTrue(session.start(prefer_vaapi=True))
            self.assertEqual(attempts, [(True, False, False), (False, False, False)])
            session.stop(remove_files=False)


class _FakeProc:
    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


if __name__ == "__main__":
    unittest.main()
