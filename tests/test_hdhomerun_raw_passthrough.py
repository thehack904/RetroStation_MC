from pathlib import Path


def test_raw_passthrough_route_is_present_and_does_not_invoke_ffmpeg():
    text = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = text.index('@app.get("/hdhomerun-testing/raw/<channel_key>.ts")')
    end = text.index('@app.post("/config")', start)
    route = text[start:end]
    assert '_requests.get(' in route
    assert 'stream=True' in route
    assert 'video/mp2t' in route
    assert 'subprocess.Popen' not in route
    assert 'build_hdhomerun_mpegts_command' not in route
    assert 'h264_vaapi' not in route
    assert 'libx264' not in route


def test_aggregate_playlist_supports_raw_output_mode():
    text = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert 'output_mode not in {"hls", "mpegts", "raw"}' in text
    assert '/hdhomerun-testing/stream/{key}' in text
    assert '@app.get("/hdhomerun-testing/raw/<channel_key>.ts")' in text
