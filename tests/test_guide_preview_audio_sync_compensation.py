from pathlib import Path

from app.config_store import DEFAULT_CONFIG
from app.manager import _guide_preview_audio_sync_filter


def test_audio_sync_defaults_are_zero():
    assert DEFAULT_CONFIG["guide_preview_hdhr_audio_offset_ms"] == 0
    assert DEFAULT_CONFIG["guide_preview_iptv_audio_offset_ms"] == 0
    assert DEFAULT_CONFIG["guide_preview_file_audio_offset_ms"] == 0


def test_hdhr_negative_offset_advances_audio():
    cfg = {"guide_preview_source_type": "hdhomerun", "guide_preview_hdhr_audio_offset_ms": -1200}
    assert _guide_preview_audio_sync_filter(cfg) == (
        "asetpts=PTS-STARTPTS,atrim=start=1.2,asetpts=PTS-STARTPTS"
    )


def test_hdhr_positive_offset_delays_audio():
    cfg = {"guide_preview_source_type": "hdhomerun", "guide_preview_hdhr_audio_offset_ms": 850}
    assert _guide_preview_audio_sync_filter(cfg) == "adelay=850:all=1"


def test_iptv_has_independent_offset():
    cfg = {"guide_preview_source_type": "url", "guide_preview_iptv_audio_offset_ms": -250}
    assert _guide_preview_audio_sync_filter(cfg) == (
        "asetpts=PTS-STARTPTS,atrim=start=0.25,asetpts=PTS-STARTPTS"
    )


def test_uploaded_file_has_independent_offset():
    cfg = {"guide_preview_source_type": "file", "guide_preview_file_audio_offset_ms": 3000}
    assert _guide_preview_audio_sync_filter(cfg) == "adelay=3000:all=1"


def test_unrelated_sources_do_not_get_compensation():
    assert _guide_preview_audio_sync_filter({"guide_preview_source_type": "virtual_channels"}) is None


def test_diagnostics_template_exposes_both_offsets_and_seconds_display():
    html = (Path(__file__).parents[1] / "app" / "templates" / "index.html").read_text()
    assert 'name="guide_preview_hdhr_audio_offset_ms"' in html
    assert 'name="guide_preview_iptv_audio_offset_ms"' in html
    assert 'name="guide_preview_file_audio_offset_ms"' in html
    assert 'seconds.toFixed(2)' in html
    assert 'Negative values advance audio; positive values delay audio.' in html


def test_shared_preview_normalizer_applies_sync_filter_in_audio_relay():
    manager = (Path(__file__).parents[1] / "app" / "manager.py").read_text()
    assert 'sync_filter = _guide_preview_audio_sync_filter(config)' in manager
    assert 'audio_filter = f"{audio_filter},{sync_filter}"' in manager
    assert '"-af", audio_filter' in manager
