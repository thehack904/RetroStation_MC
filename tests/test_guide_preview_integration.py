from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config_store import DEFAULT_CONFIG
from app.guide_preview import (
    SUPPORTED_GUIDE_RESOLUTIONS,
    SUPPORTED_PREVIEW_AUDIO_MODES,
    calculate_preview_layout,
    normalize_preview_audio_mode,
)


@pytest.mark.parametrize(
    ("resolution", "aspect"),
    [
        ("1280x720", "16:9"),
        ("1920x1080", "16:9"),
        ("960x720", "4:3"),
        ("1440x1080", "4:3"),
    ],
)
def test_preview_layout_supports_all_guide_output_profiles(resolution: str, aspect: str) -> None:
    layout = calculate_preview_layout(resolution, aspect, {"header_height": 88, "footer_height": 42, "row_height": 68})
    width, height = map(int, resolution.split("x"))

    assert layout["resolution"] == resolution
    assert layout["aspect_ratio"] == aspect
    assert layout["width"] == width
    assert layout["height"] == height
    assert layout["guide_top"] > layout["header_height"]
    assert layout["preview_y"] >= layout["header_height"]
    assert layout["preview_y"] + layout["preview_max_height"] < layout["guide_top"]
    assert layout["content_height"] == height - layout["guide_top"] - layout["footer_height"]
    assert layout["max_rows"] >= 1


def test_supported_resolutions_are_exactly_the_four_guide_profiles() -> None:
    assert SUPPORTED_GUIDE_RESOLUTIONS == {
        "1280x720",
        "1920x1080",
        "960x720",
        "1440x1080",
        "720x480",
        "640x480",
    }


def test_preview_audio_modes_are_guide_preview_and_silent() -> None:
    assert SUPPORTED_PREVIEW_AUDIO_MODES == {"guide", "preview", "silent"}
    assert normalize_preview_audio_mode("guide") == "guide"
    assert normalize_preview_audio_mode("preview") == "preview"
    assert normalize_preview_audio_mode("silent") == "silent"
    assert normalize_preview_audio_mode("bogus") == "guide"
    assert DEFAULT_CONFIG["guide_preview_audio_mode"] == "guide"


def test_admin_ui_contains_preview_controls_audio_modes_and_all_resolutions() -> None:
    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'data-tab="guide-preview"' in html
    assert 'id="tab-guide-preview"' in html
    assert 'name="guide_preview_enabled"' in html
    assert 'name="guide_preview_source_type"' in html
    assert 'name="guide_preview_audio_mode"' in html
    assert 'value="guide"' in html
    assert 'value="preview"' in html
    assert 'value="silent"' in html
    for resolution in ("1280x720", "1920x1080", "960x720", "1440x1080"):
        assert f'value="{resolution}"' in html


def test_four_by_three_preview_is_not_wider_relative_to_canvas_than_widescreen() -> None:
    wide = calculate_preview_layout("1280x720", "16:9")
    standard = calculate_preview_layout("960x720", "4:3")
    # The absolute standard-mode window may be similar, but it must remain inside
    # the narrower 4:3 canvas and leave useful room for future information text.
    assert standard["preview_max_width"] < standard["width"] // 2
    assert wide["preview_max_width"] < wide["width"] // 2


def test_virtual_preview_source_types_are_supported() -> None:
    from app.guide_preview import SUPPORTED_PREVIEW_SOURCE_TYPES
    assert {"virtual_weather", "virtual_traffic", "virtual_news", "virtual_channel_mix"} <= SUPPORTED_PREVIEW_SOURCE_TYPES


def test_enabled_virtual_channel_resolves_to_local_hls(monkeypatch, tmp_path) -> None:
    from app.guide_preview import resolve_preview_source
    monkeypatch.setenv("RETROGUIDE_PORT", "9876")
    cfg = {
        "guide_preview_source_type": "virtual_weather",
        "weather_channel_enabled": True,
    }
    assert resolve_preview_source(cfg, tmp_path) == "http://127.0.0.1:9876/hls/weather.m3u8"


def test_disabled_virtual_channel_does_not_resolve(tmp_path) -> None:
    from app.guide_preview import resolve_preview_source
    cfg = {
        "guide_preview_source_type": "virtual_news",
        "news_channel_enabled": False,
    }
    assert resolve_preview_source(cfg, tmp_path) is None


def test_admin_ui_offers_enabled_virtual_channels_as_preview_sources() -> None:
    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    for value in ("virtual_weather", "virtual_traffic", "virtual_news", "virtual_channel_mix"):
        assert f'value="{value}"' in html
    assert "Virtual Channel — Weather Channel" in html
    assert "Virtual Channel — Simulated Traffic" in html
    assert "Virtual Channel — News Now" in html

@pytest.mark.parametrize(
    ("resolution", "aspect"),
    [
        ("1280x720", "16:9"),
        ("1920x1080", "16:9"),
        ("960x720", "4:3"),
        ("1440x1080", "4:3"),
    ],
)
def test_raised_preview_frame_is_centered_and_clock_aligned(resolution: str, aspect: str) -> None:
    layout = calculate_preview_layout(resolution, aspect, {"header_height": 88, "footer_height": 42, "row_height": 68})
    width, _height = map(int, resolution.split("x"))

    frame_right = int(layout["preview_frame_x"]) + int(layout["preview_frame_width"])
    assert frame_right == width - int(layout["preview_right_margin"])

    visual_center = (int(layout["clock_visual_bottom"]) + int(layout["guide_top"])) / 2
    frame_center = int(layout["preview_frame_y"]) + int(layout["preview_frame_height"]) / 2
    assert abs(frame_center - visual_center) <= 2

    assert int(layout["preview_x"]) > int(layout["preview_frame_x"])
    assert int(layout["preview_y"]) > int(layout["preview_frame_y"])
    assert int(layout["preview_x"]) + int(layout["preview_max_width"]) < frame_right


def test_preview_m3u_parser_exposes_channel_metadata_and_resolves_relative_urls() -> None:
    from app.guide_preview import parse_preview_m3u
    text = '''#EXTM3U
#EXTINF:-1 tvg-id="wretro" tvg-name="WRETRO" tvg-chno="2" group-title="Retro",WRETRO
stream/2.m3u8
#EXTINF:-1 tvg-id="news" group-title="News",News Now
http://media.example/live/news.m3u8
'''
    channels = parse_preview_m3u(text, "http://media.lan:8409/iptv/channels.m3u")
    assert channels == [
        {
            "id": "wretro",
            "name": "WRETRO",
            "number": "2",
            "group": "Retro",
            "stream_url": "http://media.lan:8409/iptv/stream/2.m3u8",
        },
        {
            "id": "news",
            "name": "News Now",
            "number": "",
            "group": "News",
            "stream_url": "http://media.example/live/news.m3u8",
        },
    ]


def test_url_preview_prefers_selected_m3u_channel_over_playlist_url(tmp_path) -> None:
    from app.guide_preview import resolve_preview_source
    cfg = {
        "guide_preview_source_type": "url",
        "guide_preview_url": "http://media.lan:8409/iptv/channels.m3u",
        "guide_preview_url_channel": "http://media.lan:8409/iptv/channel/7.m3u8",
    }
    assert resolve_preview_source(cfg, tmp_path) == "http://media.lan:8409/iptv/channel/7.m3u8"


def test_url_preview_keeps_direct_url_behavior_without_channel_selection(tmp_path) -> None:
    from app.guide_preview import resolve_preview_source
    cfg = {
        "guide_preview_source_type": "url",
        "guide_preview_url": "http://media.lan:8409/live/direct.m3u8",
        "guide_preview_url_channel": "",
    }
    assert resolve_preview_source(cfg, tmp_path) == "http://media.lan:8409/live/direct.m3u8"


def test_admin_ui_contains_m3u_channel_loader_controls() -> None:
    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'id="guide-preview-load-channels"' in html
    assert 'id="guide-preview-url-channel"' in html
    assert 'name="guide_preview_url_channel"' in html
    assert 'Load Channels' in html


def test_preview_aspect_modes_default_and_manual_overrides() -> None:
    from app.guide_preview import effective_preview_aspect_ratio, normalize_preview_aspect_mode

    assert normalize_preview_aspect_mode(None) == "auto"
    assert normalize_preview_aspect_mode("16:9") == "16:9"
    assert normalize_preview_aspect_mode("4:3") == "4:3"
    assert normalize_preview_aspect_mode("bogus") == "auto"
    assert DEFAULT_CONFIG["guide_preview_aspect_mode"] == "auto"

    cfg = {"guide_preview_aspect_mode": "16:9"}
    assert effective_preview_aspect_ratio(cfg) == "16:9"
    cfg["guide_preview_aspect_mode"] = "4:3"
    assert effective_preview_aspect_ratio(cfg) == "4:3"


def test_auto_preview_aspect_uses_16_9_until_matching_cache_exists() -> None:
    from app.guide_preview import effective_preview_aspect_ratio, preview_source_cache_key

    cfg = {
        "guide_preview_aspect_mode": "auto",
        "guide_preview_source_type": "url",
        "guide_preview_url": "http://media.lan/live/channel.m3u8",
        "guide_preview_detected_aspect_ratio": "",
        "guide_preview_detected_source_key": "",
    }
    assert effective_preview_aspect_ratio(cfg) == "16:9"
    cfg["guide_preview_detected_aspect_ratio"] = "4:3"
    cfg["guide_preview_detected_source_key"] = preview_source_cache_key(cfg)
    assert effective_preview_aspect_ratio(cfg) == "4:3"

    cfg["guide_preview_url"] = "http://media.lan/live/other.m3u8"
    assert effective_preview_aspect_ratio(cfg) == "16:9"


def test_four_by_three_preview_window_keeps_right_edge_and_vertical_center() -> None:
    wide = calculate_preview_layout("1280x720", "16:9", {}, "16:9")
    standard = calculate_preview_layout("1280x720", "16:9", {}, "4:3")

    wide_right = int(wide["preview_frame_x"]) + int(wide["preview_frame_width"])
    standard_right = int(standard["preview_frame_x"]) + int(standard["preview_frame_width"])
    assert wide_right == standard_right
    assert int(standard["preview_frame_width"]) < int(wide["preview_frame_width"])

    wide_center = int(wide["preview_frame_y"]) + int(wide["preview_frame_height"]) / 2
    standard_center = int(standard["preview_frame_y"]) + int(standard["preview_frame_height"]) / 2
    assert abs(wide_center - standard_center) <= 2


def test_known_virtual_channel_aspect_does_not_require_probe() -> None:
    from app.guide_preview import known_preview_aspect_ratio

    assert known_preview_aspect_ratio({
        "guide_preview_source_type": "virtual_weather",
        "weather_aspect_ratio": "4:3",
    }) == "4:3"
    assert known_preview_aspect_ratio({
        "guide_preview_source_type": "virtual_news",
        "news_aspect_ratio": "16:9",
    }) == "16:9"


def test_aspect_detection_classifies_display_aspect_ratio(monkeypatch) -> None:
    import subprocess
    from app.guide_preview import detect_preview_aspect_ratio

    class Result:
        returncode = 0
        stdout = json.dumps({"streams": [{"width": 720, "height": 480, "display_aspect_ratio": "4:3", "sample_aspect_ratio": "8:9"}]})

    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 3.0
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert detect_preview_aspect_ratio("http://example/live.m3u8", timeout_seconds=3.0) == "4:3"


def test_admin_ui_contains_preview_aspect_auto_and_manual_controls() -> None:
    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'name="guide_preview_aspect_mode"' in html
    assert 'value="auto"' in html
    assert 'value="16:9"' in html
    assert 'value="4:3"' in html
    assert "Guide startup never waits for ffprobe" in html
