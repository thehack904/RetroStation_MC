from app.hdhomerun_source import merge_lineup_channels
from app import hdhomerun_guide


def test_merge_preserves_rebroadcast_selection():
    existing = [{
        "key": "old-key",
        "enabled": True,
        "rebroadcast": True,
        "GuideNumber": "7.1",
        "GuideName": "ABC",
        "URL": "http://old/auto/v7.1",
    }]
    lineup = [{"GuideNumber": "7.1", "GuideName": "ABC", "URL": "http://new/auto/v7.1"}]
    merged = merge_lineup_channels(lineup, existing=existing)
    assert merged[0]["rebroadcast"] is True


def test_rebroadcast_epg_maps_upstream_programmes_to_stable_rsmc_id(monkeypatch):
    cfg = {
        "hdhomerun_testing_device": {"DeviceAuth": "abc"},
        "hdhomerun_testing_channels": [{
            "key": "station-key",
            "enabled": True,
            "rebroadcast": True,
            "GuideNumber": "7.1",
            "GuideName": "ABC",
            "XMLTVID": "upstream.station",
        }],
    }
    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", lambda auth: "ignored")
    monkeypatch.setattr(
        hdhomerun_guide,
        "_parse_silicondust_xmltv",
        lambda text: ({"7.1": "upstream.station"}, {"upstream.station": [{
            "title": "Local News",
            "desc": "Latest headlines",
            "start": "2026-09-21T18:00:00+00:00",
            "stop": "2026-09-21T18:30:00+00:00",
        }]}),
    )
    result = hdhomerun_guide.rebroadcast_export_programmes(cfg)
    assert result["rsmc-hdhr-physical-station-key"][0]["title"] == "Local News"


def test_rebroadcast_epg_requires_use_and_rebroadcast(monkeypatch):
    cfg = {
        "hdhomerun_testing_device": {"DeviceAuth": "abc"},
        "hdhomerun_testing_channels": [
            {"key": "not-used", "enabled": False, "rebroadcast": True, "GuideNumber": "2", "GuideName": "Two"},
            {"key": "not-exported", "enabled": True, "rebroadcast": False, "GuideNumber": "3", "GuideName": "Three"},
        ],
    }
    assert hdhomerun_guide.rebroadcast_export_programmes(cfg) == {}
