from pathlib import Path

from app.config_store import DEFAULT_CONFIG
from app.source_channel_export import build_source_export_entries, remap_source_programmes

ROOT = Path(__file__).resolve().parents[1]


def test_source_export_defaults_off():
    assert DEFAULT_CONFIG["source_channels_export_enabled"] is False


def test_source_entries_get_collision_safe_ids_and_preserve_stream_metadata():
    channels = [
        {
            "id": "abc.xmltv",
            "name": "ABC",
            "number": "7.1",
            "group": "Local TV",
            "logo": "http://logos/abc.png",
            "stream_url": "http://upstream/live/abc.m3u8",
        }
    ]
    entries = build_source_export_entries(channels)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["id"].startswith("rsmc-source-")
    assert entry["upstream_xmltv_id"] == "abc.xmltv"
    assert entry["channel_number"] == "7.1"
    assert entry["stream_url"] == "http://upstream/live/abc.m3u8"
    assert entry["group_title"] == "Local TV"
    assert entry["advertise_h264_aac"] is False


def test_duplicate_source_metadata_does_not_collapse_channels():
    channel = {
        "id": "abc.xmltv",
        "name": "ABC",
        "number": "7.1",
        "group": "Local TV",
        "stream_url": "http://upstream/live/abc.m3u8",
    }
    entries = build_source_export_entries([channel, {**channel, "stream_url": "http://backup/abc.m3u8"}])
    assert len(entries) == 2
    assert entries[0]["id"] != entries[1]["id"]


def test_source_xmltv_programmes_are_remapped_to_public_id():
    entries = [{"id": "rsmc-source-deadbeef", "upstream_xmltv_id": "abc.xmltv"}]
    rows = [{
        "title": "Evening News",
        "desc": "Latest local news.",
        "start": "2026-09-21T23:00:00+00:00",
        "stop": "2026-09-21T23:30:00+00:00",
    }]
    result = remap_source_programmes(entries, {"abc.xmltv": rows})
    assert result == {"rsmc-source-deadbeef": rows}
    assert result["rsmc-source-deadbeef"] is not rows


def test_source_export_is_wired_into_combined_public_export():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _build_exported_source_entries" in text
    assert "_build_exported_virtual_channel_entries(config, base_url)" in text
    assert "+ _build_exported_source_entries(config)" in text
    assert "+ _build_exported_hdhomerun_entries(config, base_url)" in text
    assert "programme_overrides.update(_source_export_programmes(config, source_entries))" in text


def test_source_export_ui_exists():
    text = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")
    assert "Source Channel Export" in text
    assert 'name="source_channels_export_enabled"' in text
    assert "Include Playlist Source channels and XMLTV listings" in text


def test_source_export_form_is_persisted():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    start = text.index("def coerce_form")
    end = text.index("def _coerce_int", start)
    block = text[start:end]
    assert 'source_channels_export_enabled' in block
    assert 'source_channel_values' in block
