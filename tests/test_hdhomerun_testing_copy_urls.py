from pathlib import Path


def _template_text() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html").read_text(encoding="utf-8")


def test_hdhomerun_testing_tab_exposes_copyable_m3u_url():
    text = _template_text()
    assert 'id="url-hdhr-test-m3u"' in text
    assert "url_for('hdhomerun_testing_playlist', _external=True)" in text
    assert "Copy M3U" in text


def test_hdhomerun_testing_tab_exposes_copyable_per_channel_m3u8_url():
    text = _template_text()
    assert 'id="url-hdhr-hls-{{ loop.index0 }}"' in text
    assert "url_for('hdhomerun_testing_hls', channel_key=channel.key, _external=True)" in text
    assert "Copy M3U8" in text
