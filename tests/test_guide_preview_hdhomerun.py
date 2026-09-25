from pathlib import Path

from app.guide_preview import normalize_preview_source_type, preview_source_cache_key, resolve_preview_source


def _config(enabled=True):
    return {
        "guide_preview_source_type": "hdhomerun",
        "guide_preview_hdhomerun_channel": "abc123",
        "hdhomerun_testing_channels": [
            {"key": "abc123", "enabled": enabled, "GuideNumber": "7.1", "GuideName": "WJXX-HD", "URL": "http://tuner/auto/v7.1"},
            {"key": "other", "enabled": True, "GuideNumber": "9.1", "GuideName": "OTHER", "URL": "http://tuner/auto/v9.1"},
        ],
    }


def test_hdhomerun_preview_source_type_is_single_hdhomerun_choice():
    assert normalize_preview_source_type("hdhomerun") == "hdhomerun"
    # Legacy flattened selections upgrade to the single HDHomeRun source.
    assert normalize_preview_source_type("hdhomerun_testing:abc123") == "hdhomerun"
    assert normalize_preview_source_type("hdhomerun_testing:bad/key") == "file"


def test_hdhomerun_preview_resolves_only_selected_enabled_channel(monkeypatch):
    cfg = _config(True)
    monkeypatch.setattr("app.guide_preview.shared_hdhomerun_source_url", lambda config, channel: "http://127.0.0.1:5555/source/shared")
    assert resolve_preview_source(cfg, Path(".")) == "http://127.0.0.1:5555/source/shared"
    assert preview_source_cache_key(cfg) == "hdhomerun:abc123|http://tuner/auto/v7.1"


def test_hdhomerun_preview_ignores_testing_output_mode_and_uses_shared_relay(monkeypatch):
    cfg = _config(True)
    monkeypatch.setattr("app.guide_preview.shared_hdhomerun_source_url", lambda config, channel: "http://127.0.0.1:5555/source/shared")
    cfg["hdhomerun_testing_output_mode"] = "hls"
    assert resolve_preview_source(cfg, Path(".")) == "http://127.0.0.1:5555/source/shared"
    cfg["hdhomerun_testing_output_mode"] = "mpegts"
    assert resolve_preview_source(cfg, Path(".")) == "http://127.0.0.1:5555/source/shared"


def test_disabled_hdhomerun_channel_cannot_be_used_as_preview():
    assert resolve_preview_source(_config(False), Path(".")) is None


def test_hdhomerun_preview_rejects_missing_or_invalid_physical_url():
    cfg = _config(True)
    cfg["hdhomerun_testing_channels"][0]["URL"] = ""
    assert resolve_preview_source(cfg, Path(".")) is None
    cfg["hdhomerun_testing_channels"][0]["URL"] = "file:///dev/video0"
    assert resolve_preview_source(cfg, Path(".")) is None


def test_hdhomerun_preview_cache_key_tracks_physical_url_change():
    cfg = _config(True)
    first = preview_source_cache_key(cfg)
    cfg["hdhomerun_testing_channels"][0]["URL"] = "http://tuner/auto/v7.1?profile=native"
    second = preview_source_cache_key(cfg)
    assert first != second
    assert second.endswith("http://tuner/auto/v7.1?profile=native")


def test_legacy_flattened_hdhomerun_selection_still_resolves_after_upgrade(monkeypatch):
    cfg = _config(True)
    cfg.pop("guide_preview_hdhomerun_channel", None)
    cfg["guide_preview_source_type"] = "hdhomerun_testing:abc123"
    monkeypatch.setattr("app.guide_preview.shared_hdhomerun_source_url", lambda config, channel: "http://127.0.0.1:5555/source/shared")
    assert resolve_preview_source(cfg, Path(".")) == "http://127.0.0.1:5555/source/shared"
