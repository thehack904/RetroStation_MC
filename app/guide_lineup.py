from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# RSMC-owned channel ids are deliberately kept stable.  These ids are also used
# by the public M3U/XMLTV and HDHomeRun lineup surfaces, so the rendered Guide
# can merge the same logical channels without depending on Flask/app.py.
VIRTUAL_GUIDE_CHANNEL_ID = "rsmc-guide"
VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID = "rsmc-guide-sd"
VIRTUAL_WEATHER_CHANNEL_ID = "rsmc-weather"
VIRTUAL_TRAFFIC_CHANNEL_ID = "rsmc-traffic"
VIRTUAL_NEWS_CHANNEL_ID = "rsmc-news"
VIRTUAL_CHANNEL_MIX_ID = "rsmc-channel-mix"


def _enabled(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clean(value: Any, default: str) -> str:
    text = str(value or default).replace("\r", " ").replace("\n", " ").strip()
    return text or default


def build_rsmc_guide_channels(config: dict) -> list[dict]:
    """Return RSMC-owned channels that should appear in the rendered Guide.

    This is intentionally independent of playlist/XMLTV export settings.  The
    Guide's internal lineup reflects channels that actually exist in RSMC,
    while ``virtual_channels_export_enabled`` only controls downstream exports.
    """
    guide_name = _clean(config.get("title"), "Channel Guide")
    secondary_enabled = _enabled(config.get("guide_secondary_enabled"))

    channels: list[dict] = [
        {
            "id": VIRTUAL_GUIDE_CHANNEL_ID,
            "name": f"{guide_name} HD" if secondary_enabled else guide_name,
            "number": "1",
            "group": "Virtual Channels",
            "logo": "",
            "stream_url": "",
            "source_kind": "rsmc-virtual",
        }
    ]

    if secondary_enabled:
        channels.append(
            {
                "id": VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID,
                "name": f"{guide_name} SD",
                "number": "1.1",
                "group": "Virtual Channels",
                "logo": "",
                "stream_url": "",
                "source_kind": "rsmc-virtual",
            }
        )

    if _enabled(config.get("weather_channel_enabled")):
        location = _clean(config.get("weather_location_name"), "")
        weather_name = f"Weather Channel - {location}" if location else "Weather Channel"
        channels.append(
            {
                "id": VIRTUAL_WEATHER_CHANNEL_ID,
                "name": weather_name,
                "number": "2",
                "group": "Virtual Channels",
                "logo": "",
                "stream_url": "",
                "source_kind": "rsmc-virtual",
            }
        )

    if _enabled(config.get("traffic_channel_enabled")):
        channels.append(
            {
                "id": VIRTUAL_TRAFFIC_CHANNEL_ID,
                "name": "Simulated Traffic",
                "number": "3",
                "group": "Virtual Channels",
                "logo": "",
                "stream_url": "",
                "source_kind": "rsmc-virtual",
            }
        )

    if _enabled(config.get("news_channel_enabled")):
        channels.append(
            {
                "id": VIRTUAL_NEWS_CHANNEL_ID,
                "name": "News Now",
                "number": "4",
                "group": "Virtual Channels",
                "logo": "",
                "stream_url": "",
                "source_kind": "rsmc-virtual",
            }
        )

    if _enabled(config.get("channel_mix_enabled")):
        channels.append(
            {
                "id": VIRTUAL_CHANNEL_MIX_ID,
                "name": _clean(config.get("channel_mix_name"), "Channel Mix"),
                "number": "5",
                "group": "Virtual Channels",
                "logo": "",
                "stream_url": "",
                "source_kind": "rsmc-virtual",
            }
        )

    return channels


def build_rsmc_guide_programmes(config: dict, channels: list[dict]) -> dict[str, list[dict]]:
    """Create rolling programme blocks for RSMC-owned Guide rows.

    The blocks cover more than the largest normal Guide horizon and are rebuilt
    on every state refresh, so they never expire.  Channel-specific descriptions
    are used rather than the generic ``No guide data`` placeholder.
    """
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    end = start + timedelta(days=8)

    programme_titles = {
        VIRTUAL_GUIDE_CHANNEL_ID: "RetroStation MC Guide",
        VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID: "RetroStation MC Guide",
        VIRTUAL_WEATHER_CHANNEL_ID: "Local Weather",
        VIRTUAL_TRAFFIC_CHANNEL_ID: "Traffic Conditions",
        VIRTUAL_NEWS_CHANNEL_ID: "News Now",
        VIRTUAL_CHANNEL_MIX_ID: "Channel Mix",
    }
    descriptions = {
        VIRTUAL_GUIDE_CHANNEL_ID: "RetroStation MC channel guide.",
        VIRTUAL_GUIDE_SECONDARY_CHANNEL_ID: "RetroStation MC standard-definition channel guide.",
        VIRTUAL_WEATHER_CHANNEL_ID: "Local weather conditions and forecast.",
        VIRTUAL_TRAFFIC_CHANNEL_ID: "Simulated traffic conditions and travel information.",
        VIRTUAL_NEWS_CHANNEL_ID: "Current headlines and news information.",
        VIRTUAL_CHANNEL_MIX_ID: "Rotating selection of RetroStation MC virtual channels.",
    }

    programmes: dict[str, list[dict]] = {}
    for channel in channels:
        channel_id = str(channel.get("id") or "")
        if not channel_id:
            continue
        title = programme_titles.get(channel_id, _clean(channel.get("name"), "Virtual Channel"))
        desc = descriptions.get(channel_id, "RetroStation MC virtual channel.")
        slots: list[dict] = []
        slot_start = start
        while slot_start < end:
            slot_stop = slot_start + timedelta(hours=4)
            slots.append(
                {
                    "title": title,
                    "desc": desc,
                    "start": slot_start.isoformat(),
                    "stop": slot_stop.isoformat(),
                }
            )
            slot_start = slot_stop
        programmes[channel_id] = slots
    return programmes


def merge_rsmc_into_guide(
    config: dict,
    channels: list[dict],
    programmes: dict[str, list[dict]],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Merge RSMC-owned channels into an imported Guide lineup.

    Existing source channels keep their order.  Stable-id de-duplication avoids
    duplicate rows when an administrator already points the source playlist at
    an RSMC export.  RSMC programme data is only supplied when the imported
    XMLTV has no programme entries for that stable id.

    This function is the source-agnostic insertion point for additional channel
    providers (for example HDHomeRun) to contribute rows in a later phase.
    """
    merged_channels = [dict(channel) for channel in channels]
    merged_programmes = {key: list(value) for key, value in programmes.items()}
    existing_ids = {str(channel.get("id") or "") for channel in merged_channels}

    rsmc_channels = build_rsmc_guide_channels(config)
    generated = build_rsmc_guide_programmes(config, rsmc_channels)

    for channel in rsmc_channels:
        channel_id = str(channel["id"])
        if channel_id not in existing_ids:
            merged_channels.append(channel)
            existing_ids.add(channel_id)
        if not merged_programmes.get(channel_id):
            merged_programmes[channel_id] = generated[channel_id]

    return merged_channels, merged_programmes
