from pathlib import Path


def test_hdhomerun_testing_playlist_uses_real_newlines():
    text = Path('app.py').read_text(encoding='utf-8')
    assert 'Response("\\n".join(lines) + "\\n", mimetype="audio/x-mpegurl")' in text
    assert 'Response("\\\\n".join(lines) + "\\\\n", mimetype="audio/x-mpegurl")' not in text


def test_hdhomerun_testing_playlist_emits_logo_and_silicondust_tvg_id():
    text = Path('app.py').read_text(encoding='utf-8')
    assert 'attrs.append(f\'tvg-logo="{base_url}/hdhomerun-testing/logo/{key}"\')' in text
    assert 'tvg_id = xmltv_id or f"hdhr-test-{key}"' in text
