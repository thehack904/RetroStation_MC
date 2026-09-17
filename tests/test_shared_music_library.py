from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hdhomerun_admin_controls_are_hidden_and_feature_disabled():
    index = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "HDHomeRun Export" not in index
    assert "Source Rebroadcast" not in index
    assert "HDHOMERUN_FEATURE_AVAILABLE = False" in app


def test_virtual_channels_use_shared_music_library():
    template = (ROOT / "app/templates/virtual_channels.html").read_text(encoding="utf-8")
    assert 'channel_music("weather_", weather, music_files' in template
    assert 'channel_music("traffic_", traffic, music_files' in template
    assert 'channel_music("news_", news, music_files' in template
    assert "All shared music" in template


def test_traffic_and_news_hls_builders_have_audio_support():
    manager = (ROOT / "app/manager.py").read_text(encoding="utf-8")
    assert 'key_prefix="traffic_music_"' in manager
    assert 'key_prefix="news_music_"' in manager
    # Neither generated channel should remain hard-coded video-only.
    traffic_block = manager.split("def _build_traffic_ffmpeg_command", 1)[1].split("class TrafficChannelManager", 1)[0]
    news_block = manager.split("def _build_news_ffmpeg_command", 1)[1].split("class NewsChannelManager", 1)[0]
    assert '"-an"' not in traffic_block
    assert '"-an"' not in news_block


def test_shared_library_is_central_data_music_directory():
    manager = (ROOT / "app/manager.py").read_text(encoding="utf-8")
    assert 'MUSIC_DIR = DATA_DIR / "music"' in manager
    assert "music_dir=MUSIC_DIR" in manager
