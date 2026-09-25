from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_testing_playlist_uses_stable_dispatch_url():
    text = APP.read_text()
    start = text.index('@app.get("/hdhomerun-testing/playlist.m3u")')
    end = text.index('def _hdhomerun_fallback_logo', start)
    block = text[start:end]
    assert '/hdhomerun-testing/stream/{key}' in block
    assert 'lines.append(f"{base_url}/hdhomerun-testing/raw/{key}.ts")' not in block
    assert 'lines.append(f"{base_url}/hdhomerun-testing/hls/{key}.m3u8")' not in block
    assert 'lines.append(f"{base_url}/hdhomerun-testing/mpegts/{key}.ts")' not in block


def test_dispatch_route_resolves_all_modes_at_tune_time():
    text = APP.read_text()
    start = text.index('@app.get("/hdhomerun-testing/stream/<channel_key>")')
    end = text.index('def _hdhomerun_fallback_logo', start)
    block = text[start:end]
    assert 'hdhomerun_testing_output_mode' in block
    assert 'url_for("hdhomerun_testing_raw"' in block
    assert 'url_for("hdhomerun_testing_mpegts"' in block
    assert 'url_for("hdhomerun_testing_hls"' in block
    assert 'no-cache, no-store, must-revalidate' in block
