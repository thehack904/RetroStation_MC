from pathlib import Path


def test_playlist_always_emits_logo_endpoint():
    text = Path("app.py").read_text(encoding="utf-8")
    start = text.index("def hdhomerun_testing_playlist")
    end = text.index("def _hdhomerun_fallback_logo")
    playlist = text[start:end]
    assert 'tvg-logo="{base_url}/hdhomerun-testing/logo/{key}"' in playlist
    assert 'if str(item.get("logo") or "").strip()' not in playlist


def test_logo_route_has_generated_fallback():
    text = Path("app.py").read_text(encoding="utf-8")
    assert "def _hdhomerun_fallback_logo(channel: dict) -> bytes:" in text
    assert 'response.headers["X-RSMC-Logo-Source"] = "fallback"' in text
    assert 'Response(_hdhomerun_fallback_logo(matched), mimetype="image/png")' in text


def test_testing_ui_always_renders_logo_endpoint():
    text = Path("app/templates/index.html").read_text(encoding="utf-8")
    marker = "url_for('hdhomerun_testing_logo', channel_key=channel.key)"
    assert marker in text
    area = text[text.index(marker)-120:text.index(marker)+220]
    assert "{% if channel.logo %}" not in area
