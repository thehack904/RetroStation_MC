from pathlib import Path


def test_virtual_channel_default_logos_are_png():
    root = Path(__file__).resolve().parents[1]
    for rel in [
        "data/weather_logo/default.png",
        "data/traffic_logo/default.png",
        "data/news_logo/default.png",
        "data/channel_mix_logo/default.png",
    ]:
        path = root / rel
        assert path.is_file()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_virtual_channel_export_uses_raster_logo_routes():
    app_py = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert '"traffic-logo", TRAFFIC_LOGO_DIR' in app_py
    assert '"news-logo", NEWS_LOGO_DIR' in app_py
    assert '"channel-mix-logo", CHANNEL_MIX_LOGO_DIR' in app_py
    assert 'DEFAULT_VIRTUAL_LOGO_EXTENSION_ORDER = (".png",' in app_py
