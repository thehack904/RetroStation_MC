from app.channel_mix import normalize_channel_mix_config, get_active_channel_mix_slot, get_active_available_channel
import pytest

VALID={"rsmc-guide","rsmc-weather","rsmc-traffic","rsmc-news"}

def test_normalize_and_self_rejection():
    cfg=normalize_channel_mix_config("Info Mix", [{"channel_id":"rsmc-news","duration_minutes":2}], VALID)
    assert cfg["name"]=="Info Mix" and cfg["channels"][0]["duration_minutes"]==2
    with pytest.raises(ValueError): normalize_channel_mix_config("x", [{"channel_id":"rsmc-channel-mix","duration_minutes":2}], VALID)

def test_boundaries_full_cycle():
    ch=[{"channel_id":"rsmc-news","duration_minutes":2},{"channel_id":"rsmc-weather","duration_minutes":2}]
    assert get_active_channel_mix_slot(ch,60)==("rsmc-news",60)
    assert get_active_channel_mix_slot(ch,120)==("rsmc-weather",120)
    assert get_active_channel_mix_slot(ch,239)==("rsmc-weather",1)
    assert get_active_channel_mix_slot(ch,240)==("rsmc-news",120)

def test_disabled_source_falls_forward_without_compressing_schedule():
    ch=[{"channel_id":"rsmc-news","duration_minutes":2},{"channel_id":"rsmc-weather","duration_minutes":2}]
    active,remaining,scheduled=get_active_available_channel(ch,{"rsmc-weather"},60)
    assert scheduled=="rsmc-news" and active=="rsmc-weather" and remaining==60

def test_all_unavailable_returns_none():
    ch=[{"channel_id":"rsmc-news","duration_minutes":1}]
    assert get_active_available_channel(ch,set(),10)[0] is None

def test_two_migrated_virtual_channel_providers_rotate_and_fallback():
    channels=[
        {"channel_id":"rsmc-traffic","duration_minutes":1},
        {"channel_id":"rsmc-news","duration_minutes":1},
    ]
    assert get_active_available_channel(channels,{"rsmc-traffic","rsmc-news"},30)[0]=="rsmc-traffic"
    assert get_active_available_channel(channels,{"rsmc-traffic","rsmc-news"},90)[0]=="rsmc-news"
    # If Traffic is unavailable during its slot, News carries the mix until the boundary.
    active,remaining,scheduled=get_active_available_channel(channels,{"rsmc-news"},30)
    assert (active,scheduled,remaining)==("rsmc-news","rsmc-traffic",30)


def test_four_channel_schedule_wraps_last_back_to_first():
    channels = [
        {"channel_id": "rsmc-guide", "duration_minutes": 1},
        {"channel_id": "rsmc-weather", "duration_minutes": 1},
        {"channel_id": "rsmc-news", "duration_minutes": 1},
        {"channel_id": "rsmc-traffic", "duration_minutes": 1},
    ]
    # Final second of Traffic, then exact full-cycle boundary returns to Guide.
    assert get_active_channel_mix_slot(channels, 239) == ("rsmc-traffic", 1)
    assert get_active_channel_mix_slot(channels, 240) == ("rsmc-guide", 60)
    assert get_active_available_channel(channels, {c["channel_id"] for c in channels}, 240)[0] == "rsmc-guide"
