from pathlib import Path


def _app_text():
    return (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def test_output_mode_change_stops_all_previous_testing_streams():
    text = _app_text()
    assert "previous_output_mode" in text
    assert "if output_mode != previous_output_mode:" in text
    assert "_stop_all_hdhomerun_testing_streams(" in text
    assert "released tuner resources" in text


def test_raw_passthrough_is_registered_for_mode_switch_cleanup():
    text = _app_text()
    start = text.index('@app.get("/hdhomerun-testing/raw/<channel_key>.ts")')
    end = text.index('@app.post("/config")', start)
    route = text[start:end]
    assert '_register_hdhomerun_testing_live_stream("raw"' in route
    assert "stop_event.is_set()" in route
    assert "_unregister_hdhomerun_testing_live_stream" in route


def test_transcoded_mpegts_is_registered_for_mode_switch_cleanup():
    text = _app_text()
    start = text.index('@app.get("/hdhomerun-testing/mpegts/<channel_key>.ts")')
    end = text.index('@app.get("/hdhomerun-testing/raw/<channel_key>.ts")', start)
    route = text[start:end]
    assert '_register_hdhomerun_testing_live_stream("mpegts"' in route
    assert "stop_event.is_set()" in route
    assert "_unregister_hdhomerun_testing_live_stream" in route


def test_atexit_stops_hls_and_long_lived_streams():
    text = _app_text()
    assert "atexit.register(_stop_all_hdhomerun_testing_streams)" in text
