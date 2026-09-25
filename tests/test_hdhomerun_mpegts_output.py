from pathlib import Path

from app.hdhomerun_hls import build_hdhomerun_mpegts_command


SD_INTERLACED_MPEG2 = {
    "codec": "mpeg2video",
    "width": 704,
    "height": 480,
    "field_order": "tt",
    "interlaced": True,
    "fps": 30000 / 1001,
    "avg_frame_rate": "30000/1001",
    "r_frame_rate": "30000/1001",
}

H264_INTERLACED = {
    "codec": "h264",
    "width": 640,
    "height": 480,
    "field_order": "tt",
    "interlaced": True,
    "fps": 30000 / 1001,
    "avg_frame_rate": "30000/1001",
    "r_frame_rate": "60000/1001",
}


def test_mpegts_interlaced_mpeg2_keeps_validated_vaapi_timing_pipeline():
    command = build_hdhomerun_mpegts_command(
        "http://hdhr/auto/v11.4",
        use_vaapi=True,
        source_profile=SD_INTERLACED_MPEG2,
    )
    joined = " ".join(command)
    assert "yadif=mode=send_field:parity=auto:deint=all" in joined
    assert "scale=720:480" in joined
    assert "-c:v h264_vaapi" in joined
    assert "-r 60000/1001" in joined
    assert "-fps_mode cfr" in joined
    assert "aresample=async=1:first_pts=0" in joined
    assert command[-3:] == ["-f", "mpegts", "pipe:1"]


def test_mpegts_h264_uses_video_copy():
    command = build_hdhomerun_mpegts_command(
        "http://hdhr/auto/v23.4",
        source_profile=H264_INTERLACED,
        video_copy=True,
    )
    joined = " ".join(command)
    assert "-c:v copy" in joined
    assert "h264_vaapi" not in joined
    assert "yadif=" not in joined
    assert command[-3:] == ["-f", "mpegts", "pipe:1"]


def test_testing_tab_exposes_mpegts_mode_and_direct_urls():
    text = (Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'name="hdhomerun_testing_output_mode"' in text
    assert 'value="mpegts"' in text
    assert 'value="raw"' in text
    assert "url_for('hdhomerun_testing_mpegts', channel_key=channel.key, _external=True)" in text
    assert 'HDHomeRun Diagnostics' in text
    assert "url_for('hdhomerun_testing_raw', channel_key=channel.key, _external=True)" in text
    assert 'diag-hdhr-ts-' in text
    assert 'diag-hdhr-raw-' in text
