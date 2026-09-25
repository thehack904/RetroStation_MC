from app import hdhomerun_guide


XMLTV = '''<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="I7.1.12345.schedulesdirect.org">
    <display-name>7.1 WJXX-HD</display-name>
    <lcn>7.1</lcn>
  </channel>
  <channel id="I12.1.98765.schedulesdirect.org">
    <display-name>12.1 WTLV-HD</display-name>
    <lcn>12.1</lcn>
  </channel>
  <programme start="20260921100000 -0500" stop="20260921110000 -0500" channel="I7.1.12345.schedulesdirect.org">
    <title>Morning News</title><desc>Local news and weather.</desc>
  </programme>
  <programme start="20260921110000 -0500" stop="20260921120000 -0500" channel="I7.1.12345.schedulesdirect.org">
    <title>Daytime Show</title><desc>Today's guests.</desc>
  </programme>
  <programme start="20260921100000 -0500" stop="20260921113000 -0500" channel="I12.1.98765.schedulesdirect.org">
    <title>Network Program</title><desc>Network programming.</desc>
  </programme>
</tv>'''


def _config(enabled=True):
    return {
        "hdhomerun_testing_guide_enabled": enabled,
        "hdhomerun_testing_device": {"DeviceAuth": "test-auth"},
        "hdhomerun_testing_channels": [
            {
                "key": "abc",
                "enabled": True,
                "GuideNumber": "7.1",
                "GuideName": "WJXX-HD",
                "XMLTVID": "I7.1.12345.schedulesdirect.org",
                "URL": "http://hdhr/auto/v7.1",
            },
            {
                "key": "def",
                "enabled": False,
                "GuideNumber": "12.1",
                "GuideName": "WTLV-HD",
                "XMLTVID": "I12.1.98765.schedulesdirect.org",
                "URL": "http://hdhr/auto/v12.1",
            },
        ],
    }


def test_hdhomerun_testing_guide_master_switch_off_adds_nothing(monkeypatch):
    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", lambda auth: XMLTV)
    channels, programmes = hdhomerun_guide.merge_hdhomerun_testing_into_guide(_config(False), [], {})
    assert channels == []
    assert programmes == {}


def test_only_selected_hdhomerun_testing_channels_are_added(monkeypatch):
    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", lambda auth: XMLTV)
    channels, programmes = hdhomerun_guide.merge_hdhomerun_testing_into_guide(_config(True), [], {})
    assert [channel["number"] for channel in channels] == ["7.1"]
    assert [channel["name"] for channel in channels] == ["WJXX-HD"]
    channel_id = channels[0]["id"]
    assert [item["title"] for item in programmes[channel_id]] == ["Morning News", "Daytime Show"]
    assert programmes[channel_id][0]["desc"] == "Local news and weather."


def test_xmltv_lcn_mapping_is_used_when_saved_xmltv_id_is_missing(monkeypatch):
    config = _config(True)
    config["hdhomerun_testing_channels"][0]["XMLTVID"] = ""
    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", lambda auth: XMLTV)
    channels, programmes = hdhomerun_guide.merge_hdhomerun_testing_into_guide(config, [], {})
    channel_id = channels[0]["id"]
    assert programmes[channel_id][0]["title"] == "Morning News"


def test_existing_imported_xmltv_channel_is_not_duplicated(monkeypatch):
    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", lambda auth: XMLTV)
    imported = [{"id": "I7.1.12345.schedulesdirect.org", "name": "WJXX-HD", "number": "7.1"}]
    channels, _ = hdhomerun_guide.merge_hdhomerun_testing_into_guide(_config(True), imported, {})
    assert channels == imported


def test_missing_silicondust_guide_keeps_selected_channel_with_fallback(monkeypatch):
    def fail(_auth):
        raise OSError("offline")

    monkeypatch.setattr(hdhomerun_guide, "_fetch_xmltv", fail)
    channels, programmes = hdhomerun_guide.merge_hdhomerun_testing_into_guide(_config(True), [], {})
    assert len(channels) == 1
    channel_id = channels[0]["id"]
    assert programmes[channel_id][0]["title"] == "WJXX-HD"
    assert programmes[channel_id][0]["desc"] == "Programming information unavailable."
