from app.guide_lineup import (
    VIRTUAL_CHANNEL_MIX_ID,
    VIRTUAL_GUIDE_CHANNEL_ID,
    VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID,
    VIRTUAL_NEWS_CHANNEL_ID,
    VIRTUAL_TRAFFIC_CHANNEL_ID,
    VIRTUAL_WEATHER_CHANNEL_ID,
    build_rsmc_guide_channels,
    merge_rsmc_into_guide,
)


def test_enabled_virtual_channels_are_added_to_rendered_guide_lineup():
    config = {
        "title": "TV Guide",
        "guide_secondary_enabled": True,
        "weather_channel_enabled": True,
        "weather_location_name": "Jacksonville FL",
        "traffic_channel_enabled": True,
        "news_channel_enabled": True,
        "channel_mix_enabled": True,
        "channel_mix_name": "Channel Mix",
        # Export is intentionally off: rendered Guide inclusion is independent.
        "virtual_channels_export_enabled": False,
    }
    imported = [{"id": "source.10", "name": "Movies", "number": "10", "group": "", "logo": "", "stream_url": "http://example/10"}]
    programmes = {"source.10": [{"title": "Feature", "desc": "", "start": "2026-09-21T00:00:00+00:00", "stop": "2026-09-22T00:00:00+00:00"}]}

    channels, merged_programmes = merge_rsmc_into_guide(config, imported, programmes)
    ids = [channel["id"] for channel in channels]

    assert ids == [
        "source.10",
        VIRTUAL_GUIDE_CHANNEL_ID,
        VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID,
        VIRTUAL_WEATHER_CHANNEL_ID,
        VIRTUAL_TRAFFIC_CHANNEL_ID,
        VIRTUAL_NEWS_CHANNEL_ID,
        VIRTUAL_CHANNEL_MIX_ID,
    ]
    assert channels[3]["name"] == "Weather Channel - Jacksonville FL"
    for channel_id in ids[1:]:
        assert merged_programmes[channel_id]
        assert merged_programmes[channel_id][0]["title"]


def test_disabled_optional_virtual_channels_are_not_added():
    channels = build_rsmc_guide_channels({"title": "Guide"})
    assert [channel["id"] for channel in channels] == [VIRTUAL_GUIDE_CHANNEL_ID]


def test_duplicate_rsmc_channel_id_is_not_added_twice_and_source_programme_wins():
    imported = [{"id": VIRTUAL_WEATHER_CHANNEL_ID, "name": "Imported Weather", "number": "88", "group": "", "logo": "", "stream_url": "http://example/weather"}]
    existing_programme = [{"title": "Imported Forecast", "desc": "Source XMLTV", "start": "2026-09-21T00:00:00+00:00", "stop": "2026-09-22T00:00:00+00:00"}]

    channels, programmes = merge_rsmc_into_guide(
        {"weather_channel_enabled": True},
        imported,
        {VIRTUAL_WEATHER_CHANNEL_ID: existing_programme},
    )

    assert sum(1 for channel in channels if channel["id"] == VIRTUAL_WEATHER_CHANNEL_ID) == 1
    assert programmes[VIRTUAL_WEATHER_CHANNEL_ID] == existing_programme


def test_generated_virtual_programmes_use_specific_descriptions():
    channels, programmes = merge_rsmc_into_guide(
        {"news_channel_enabled": True, "traffic_channel_enabled": True},
        [],
        {},
    )
    assert programmes[VIRTUAL_NEWS_CHANNEL_ID][0]["title"] == "News Now"
    assert programmes[VIRTUAL_NEWS_CHANNEL_ID][0]["desc"] == "Current headlines and news information."
    assert programmes[VIRTUAL_TRAFFIC_CHANNEL_ID][0]["title"] == "Traffic Conditions"
    assert programmes[VIRTUAL_TRAFFIC_CHANNEL_ID][0]["desc"] == "Simulated traffic conditions and travel information."
    assert channels[0]["number"] == "1"
