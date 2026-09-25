from app.channel_registry import finalize_guide_lineup


def test_unified_lineup_sorts_numeric_subchannels_naturally():
    channels = [
        {"id": "iptv-10", "name": "Ten", "number": "10"},
        {"id": "hdhr-2-2", "name": "Two Dot Two", "number": "2.2", "source_kind": "hdhomerun-testing"},
        {"id": "rsmc-weather", "name": "Weather", "number": "2", "source_kind": "rsmc-virtual"},
        {"id": "iptv-2-1", "name": "Two Dot One", "number": "2.1"},
        {"id": "iptv-1", "name": "One", "number": "1"},
    ]
    result = finalize_guide_lineup(channels)
    assert [row["number"] for row in result] == ["1", "2", "2.1", "2.2", "10"]


def test_duplicate_numbers_keep_real_number_and_receive_source_badges():
    channels = [
        {"id": "iptv-two", "name": "ABC", "number": "2"},
        {"id": "rsmc-weather", "name": "Weather", "number": "2", "source_kind": "rsmc-virtual"},
        {"id": "hdhr-two", "name": "WJXX", "number": "2", "source_kind": "hdhomerun-testing"},
    ]
    result = finalize_guide_lineup(channels)
    assert [row["id"] for row in result] == ["rsmc-weather", "iptv-two", "hdhr-two"]
    by_id = {row["id"]: row for row in result}
    assert by_id["rsmc-weather"]["number"] == "2"
    assert by_id["rsmc-weather"]["guide_number_label"] == "2 [R]"
    assert by_id["iptv-two"]["guide_number_label"] == "2 [I]"
    assert by_id["hdhr-two"]["guide_number_label"] == "2 [H]"


def test_unique_numbers_do_not_receive_collision_badge():
    result = finalize_guide_lineup([
        {"id": "a", "name": "One", "number": "1"},
        {"id": "b", "name": "Two", "number": "2", "source_kind": "hdhomerun-testing"},
    ])
    assert result[0]["guide_number_label"] == "1"
    assert result[1]["guide_number_label"] == "2"


def test_exported_lineup_sorts_all_sources_by_channel_number():
    from app.channel_registry import sort_exported_lineup

    channels = [
        {"id": "rsmc-guide", "channel_number": "1", "name": "Guide"},
        {"id": "rsmc-source-10", "channel_number": "10", "name": "Ersatz 10", "source_kind": "source"},
        {"id": "rsmc-source-6", "channel_number": "6", "name": "Ersatz 6", "source_kind": "source"},
        {"id": "rsmc-hdhr-physical-a", "channel_number": "7.2", "name": "HDHR 7.2", "source_kind": "hdhomerun"},
        {"id": "rsmc-hdhr-physical-b", "channel_number": "7.1", "name": "HDHR 7.1", "source_kind": "hdhomerun"},
        {"id": "rsmc-weather", "channel_number": "2", "name": "Weather"},
    ]

    ordered = sort_exported_lineup(channels)
    assert [row["channel_number"] for row in ordered] == ["1", "2", "6", "7.1", "7.2", "10"]


def test_exported_lineup_duplicate_numbers_use_guide_source_priority():
    from app.channel_registry import sort_exported_lineup

    channels = [
        {"id": "rsmc-hdhr-physical-a", "channel_number": "7.1", "name": "HDHR", "source_kind": "hdhomerun"},
        {"id": "rsmc-source-a", "channel_number": "7.1", "name": "IPTV", "source_kind": "source"},
        {"id": "rsmc-guide-duplicate", "channel_number": "7.1", "name": "Virtual"},
    ]

    ordered = sort_exported_lineup(channels)
    assert [row["id"] for row in ordered] == [
        "rsmc-guide-duplicate",
        "rsmc-source-a",
        "rsmc-hdhr-physical-a",
    ]
