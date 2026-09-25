from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.guide_state import _preview_channel_metadata
from app.renderer import GuideRenderer


def test_virtual_preview_resolves_stable_guide_channel_metadata():
    cfg = {
        "guide_preview_source_type": "virtual_channels",
        "guide_preview_virtual_channel": "virtual_weather",
    }
    channels = [{"id": "rsmc-weather", "name": "Weather Channel", "number": "2", "stream_url": ""}]
    assert _preview_channel_metadata(cfg, channels) == {
        "id": "rsmc-weather",
        "name": "Weather Channel",
        "number": "2",
    }


def test_hdhomerun_preview_resolves_stable_guide_channel_metadata():
    cfg = {
        "guide_preview_source_type": "hdhomerun",
        "guide_preview_hdhomerun_channel": "abc123",
    }
    channels = [{"id": "rsmc-hdhr-physical-abc123", "name": "WJXX-HD", "number": "7.1", "stream_url": ""}]
    assert _preview_channel_metadata(cfg, channels) == {
        "id": "rsmc-hdhr-physical-abc123",
        "name": "WJXX-HD",
        "number": "7.1",
    }


def test_url_playlist_preview_matches_imported_channel_by_stream_url():
    cfg = {
        "guide_preview_source_type": "url",
        "guide_preview_url_channel": "http://example.test/live/12",
        "guide_preview_url_channel_name": "Channel Twelve",
    }
    channels = [{"id": "channel-12", "name": "Channel Twelve", "number": "12", "stream_url": "http://example.test/live/12"}]
    assert _preview_channel_metadata(cfg, channels)["id"] == "channel-12"


def test_bare_url_has_no_automatic_now_playing_identity():
    cfg = {"guide_preview_source_type": "url", "guide_preview_url": "http://example.test/video.m3u8"}
    assert _preview_channel_metadata(cfg, []) == {}


def test_renderer_builds_now_playing_from_current_program(tmp_path: Path):
    now = datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)
    state = {
        "display": {
            "preview_enabled": True,
            "preview_channel_id": "rsmc-hdhr-physical-abc123",
            "preview_channel_name": "WJXX-HD",
            "preview_channel_number": "7.1",
        },
        "pages": [[{
            "id": "rsmc-hdhr-physical-abc123",
            "name": "WJXX-HD",
            "number": "7.1",
            "programs": [{
                "title": "Local News",
                "start": (now - timedelta(minutes=30)).isoformat(),
                "stop": (now + timedelta(minutes=30)).isoformat(),
            }],
        }]],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    renderer = GuideRenderer(path)
    renderer.reload_if_needed()
    slide = renderer._preview_now_playing_slide(renderer.state["display"], now.timestamp())
    assert slide is not None
    assert slide["lines"] == ["Now Playing on WJXX-HD: 7.1 — Local News"]


def test_renderer_now_playing_program_changes_at_epg_boundary(tmp_path: Path):
    boundary = datetime(2026, 9, 21, 18, 30, tzinfo=timezone.utc)
    state = {
        "display": {
            "preview_enabled": True,
            "preview_channel_id": "rsmc-weather",
            "preview_channel_name": "Weather Channel",
            "preview_channel_number": "2",
        },
        "pages": [[{
            "id": "rsmc-weather",
            "programs": [
                {"title": "Forecast A", "start": (boundary - timedelta(hours=1)).isoformat(), "stop": boundary.isoformat()},
                {"title": "Forecast B", "start": boundary.isoformat(), "stop": (boundary + timedelta(hours=1)).isoformat()},
            ],
        }]],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    renderer = GuideRenderer(path)
    renderer.reload_if_needed()
    before = renderer._preview_now_playing_slide(renderer.state["display"], (boundary - timedelta(seconds=1)).timestamp())
    after = renderer._preview_now_playing_slide(renderer.state["display"], boundary.timestamp())
    assert before["lines"][0].endswith("Forecast A")
    assert after["lines"][0].endswith("Forecast B")


def test_automatic_now_playing_is_present_without_custom_guide_messages(tmp_path: Path):
    now = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)
    state = {
        "display": {
            "preview_enabled": True,
            "preview_channel_id": "rsmc-news",
            "preview_channel_name": "News Now",
            "preview_channel_number": "4",
            "guide_message_enabled": False,
            "guide_message_text": "",
        },
        "pages": [[{"id": "rsmc-news", "programs": [{"title": "News Now", "start": (now - timedelta(hours=1)).isoformat(), "stop": (now + timedelta(hours=1)).isoformat()}]}]],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    renderer = GuideRenderer(path)
    renderer.reload_if_needed()
    slides = renderer._guide_message_slides(renderer.state["display"], now.timestamp())
    assert [slide["lines"] for slide in slides] == [["Now Playing on News Now: 4 — News Now"]]


def test_automatic_now_playing_precedes_custom_messages_without_changing_duration(tmp_path: Path):
    now = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)
    state = {
        "display": {
            "preview_enabled": True,
            "preview_channel_id": "rsmc-weather",
            "preview_channel_name": "Weather Channel",
            "preview_channel_number": "2",
            "guide_message_enabled": True,
            "guide_message_text": "[message:45]\nTonight: Classic TV\n[/message]",
        },
        "pages": [[{"id": "rsmc-weather", "programs": [{"title": "Local Weather", "start": (now - timedelta(hours=1)).isoformat(), "stop": (now + timedelta(hours=1)).isoformat()}]}]],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    renderer = GuideRenderer(path)
    renderer.reload_if_needed()
    slides = renderer._guide_message_slides(renderer.state["display"], now.timestamp())
    assert slides[0]["automatic"] == "now_playing"
    assert slides[1] == {"lines": ["Tonight: Classic TV"], "blank": False, "duration": 45}


def test_automatic_now_playing_can_be_disabled_without_disabling_custom_messages(tmp_path: Path):
    now = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)
    state = {
        "display": {
            "preview_enabled": True,
            "preview_channel_id": "rsmc-weather",
            "preview_channel_name": "Weather Channel",
            "preview_channel_number": "2",
            "guide_message_enabled": True,
            "guide_message_now_playing_enabled": False,
            "guide_message_text": "[message:45]\nTonight: Classic TV\n[/message]",
        },
        "pages": [[{"id": "rsmc-weather", "programs": [{"title": "Local Weather", "start": (now - timedelta(hours=1)).isoformat(), "stop": (now + timedelta(hours=1)).isoformat()}]}]],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    renderer = GuideRenderer(path)
    renderer.reload_if_needed()
    slides = renderer._guide_message_slides(renderer.state["display"], now.timestamp())
    assert slides == [{"lines": ["Tonight: Classic TV"], "blank": False, "duration": 45}]


def test_automatic_now_playing_defaults_enabled_when_toggle_missing(tmp_path: Path):
    now = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)
    state = {
        "display": {
            "preview_enabled": True,
            "preview_channel_id": "rsmc-news",
            "preview_channel_name": "News Now",
            "preview_channel_number": "4",
            "guide_message_enabled": False,
            "guide_message_text": "",
        },
        "pages": [[{"id": "rsmc-news", "programs": [{"title": "News Now", "start": (now - timedelta(hours=1)).isoformat(), "stop": (now + timedelta(hours=1)).isoformat()}]}]],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    renderer = GuideRenderer(path)
    renderer.reload_if_needed()
    slides = renderer._guide_message_slides(renderer.state["display"], now.timestamp())
    assert slides[0]["automatic"] == "now_playing"


def test_build_state_persists_preview_programmes_directly(tmp_path: Path, monkeypatch):
    from app import guide_state

    now = datetime.now(timezone.utc)
    channel = {
        "id": "rsmc-weather",
        "name": "Weather Channel",
        "number": "2",
        "stream_url": "",
    }
    programmes = {
        "rsmc-weather": [{
            "title": "Local Weather",
            "desc": "Forecast",
            "start": (now - timedelta(hours=1)).isoformat(),
            "stop": (now + timedelta(hours=1)).isoformat(),
        }]
    }
    monkeypatch.setattr(guide_state, "load_theme", lambda _name: {"layout": {}})
    out = tmp_path / "guide_state.json"
    state = guide_state.build_state({
        "theme": "test",
        "resolution": "1280x720",
        "aspect_ratio": "16:9",
        "guide_preview_enabled": True,
        "guide_preview_source_type": "virtual_channels",
        "guide_preview_virtual_channel": "virtual_weather",
        "visible_rows": 8,
        "guide_minutes": 90,
    }, [channel], programmes, output_path=out)

    assert state["display"]["preview_channel_id"] == "rsmc-weather"
    assert state["display"]["preview_channel_programs"][0]["title"] == "Local Weather"


def test_hdhomerun_now_playing_does_not_require_rendered_guide_inclusion(tmp_path: Path, monkeypatch):
    from app import guide_state, hdhomerun_guide

    now = datetime.now(timezone.utc)
    xmltv = f'''<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="I7.1.test"><display-name>7.1 WJXX-HD</display-name><lcn>7.1</lcn></channel>
  <programme start="{(now - timedelta(minutes=30)).strftime('%Y%m%d%H%M%S +0000')}" stop="{(now + timedelta(minutes=30)).strftime('%Y%m%d%H%M%S +0000')}" channel="I7.1.test">
    <title>Local News</title><desc>Current programme.</desc>
  </programme>
</tv>'''
    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", lambda _auth: xmltv)
    monkeypatch.setattr(guide_state, "load_theme", lambda _name: {"layout": {}})

    config = {
        "theme": "test",
        "resolution": "1280x720",
        "aspect_ratio": "16:9",
        "guide_preview_enabled": True,
        "guide_preview_source_type": "hdhomerun",
        "guide_preview_hdhomerun_channel": "cf62e7f4474adac9f407",
        "guide_message_enabled": True,
        "guide_message_now_playing_enabled": True,
        "hdhomerun_testing_guide_enabled": False,
        "hdhomerun_testing_device": {"DeviceAuth": "test-auth"},
        "hdhomerun_testing_channels": [{
            "key": "cf62e7f4474adac9f407",
            "enabled": True,
            "GuideNumber": "7.1",
            "GuideName": "WJXX-HD",
            "XMLTVID": "I7.1.test",
            "URL": "http://hdhr/auto/v7.1",
        }],
        "visible_rows": 8,
        "guide_minutes": 90,
    }
    out = tmp_path / "guide_state.json"
    state = guide_state.build_state(config, [], {}, output_path=out)

    display = state["display"]
    assert display["preview_channel_id"] == "rsmc-hdhr-physical-cf62e7f4474adac9f407"
    assert display["preview_channel_name"] == "WJXX-HD"
    assert display["preview_channel_number"] == "7.1"
    assert display["preview_channel_programs"][0]["title"] == "Local News"
