from types import SimpleNamespace

from app.guide_preview import cached_preview_transport, detect_preview_transport


def test_transport_local_file_is_file():
    assert detect_preview_transport('/tmp/example.mp4') == 'file'


def test_transport_m3u8_is_hls_without_probe(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('ffprobe should not run for an explicit .m3u8 URL')
    monkeypatch.setattr('app.guide_preview.subprocess.run', unexpected)
    assert detect_preview_transport('http://example.test/live/channel.m3u8?token=1') == 'hls'


def test_transport_generic_url_uses_ffprobe_for_mpegts(monkeypatch):
    monkeypatch.setattr(
        'app.guide_preview.subprocess.run',
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='mpegts\n'),
    )
    assert detect_preview_transport('http://example.test/channel/12') == 'mpegts'


def test_cached_transport_is_bound_to_selected_source():
    cfg = {
        'guide_preview_source_type': 'url',
        'guide_preview_url': 'http://example.test/playlist.m3u',
        'guide_preview_url_channel': 'http://example.test/channel/12',
        'guide_preview_detected_transport': 'mpegts',
        'guide_preview_transport_source_key': 'url:http://example.test/channel/12',
    }
    assert cached_preview_transport(cfg) == 'mpegts'
    cfg['guide_preview_url_channel'] = 'http://example.test/channel/13'
    assert cached_preview_transport(cfg) == ''
