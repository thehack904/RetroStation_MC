from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

from app.source_fetch import read_text_or_file
from app.xmltv_parser import parse_xmltv_dt

_CACHE_TTL_SECONDS = 15 * 60
_XMLTV_CACHE: dict[str, tuple[float, str]] = {}


def _enabled(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def selected_testing_channels(config: dict) -> list[dict]:
    """Return only physical HDHomeRun Testing channels selected for Guide use."""
    if not _enabled(config.get("hdhomerun_testing_guide_enabled")):
        return []
    raw = config.get("hdhomerun_testing_channels")
    if not isinstance(raw, list):
        return []
    selected: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not _enabled(item.get("enabled"), True):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        row = dict(item)
        row["key"] = key
        row["GuideNumber"] = str(item.get("GuideNumber") or item.get("ChannelNumber") or index + 1).strip()
        row["GuideName"] = str(item.get("GuideName") or item.get("CallSign") or f"Channel {index + 1}").strip()
        selected.append(row)
    return selected



def preview_channel_details(config: dict, channel_key: str) -> tuple[dict[str, str], list[dict]]:
    """Resolve one selected physical HDHomeRun channel for Guide Preview.

    Unlike :func:`selected_testing_channels`, this deliberately does *not*
    depend on ``hdhomerun_testing_guide_enabled``.  Preview/Now Playing is a
    separate consumer: a physical channel can be used for Preview and expose
    its current programme even when the user chooses not to add that station
    to the rendered Guide lineup.
    """
    key = str(channel_key or "").strip()
    raw = config.get("hdhomerun_testing_channels")
    if not key or not isinstance(raw, list):
        return {}, []

    selected = None
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "").strip() != key:
            continue
        if not _enabled(item.get("enabled"), True):
            return {}, []
        selected = dict(item)
        selected["GuideNumber"] = str(item.get("GuideNumber") or item.get("ChannelNumber") or index + 1).strip()
        selected["GuideName"] = str(item.get("GuideName") or item.get("CallSign") or f"Channel {index + 1}").strip()
        break

    if selected is None:
        return {}, []

    number = str(selected.get("GuideNumber") or "").strip()
    name = str(selected.get("GuideName") or number or "HDHomeRun").strip()
    channel_id = f"rsmc-hdhr-physical-{key}"
    upstream_id = str(selected.get("XMLTVID") or selected.get("upstream_xmltv_id") or "").strip()

    programmes: list[dict] = []
    auth = _device_auth(config)
    if auth:
        try:
            lcn_to_xmltv_id, upstream_programmes = _parse_silicondust_xmltv(_fetch_xmltv(auth))
            if not upstream_id:
                upstream_id = lcn_to_xmltv_id.get(number, "")
            if upstream_id:
                programmes = list(upstream_programmes.get(upstream_id, []))
        except Exception:
            programmes = []

    if not programmes:
        programmes = _fallback_programmes(name)

    return {
        "id": channel_id,
        "name": name,
        "number": number,
    }, programmes

def _device_auth(config: dict) -> str:
    device = config.get("hdhomerun_testing_device")
    if not isinstance(device, dict):
        return ""
    return str(device.get("DeviceAuth") or "").strip()


def _fetch_xmltv(device_auth: str, *, timeout: int = 12) -> str:
    now = time.monotonic()
    cached = _XMLTV_CACHE.get(device_auth)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]
    url = f"https://api.hdhomerun.com/api/xmltv?DeviceAuth={quote_plus(device_auth)}"
    text = read_text_or_file(url, timeout=timeout)
    _XMLTV_CACHE[device_auth] = (now, text)
    return text


def _parse_silicondust_xmltv(xml_text: str) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """Return LCN->XMLTV id mapping plus programmes keyed by XMLTV id."""
    root = ET.fromstring(xml_text)
    lcn_to_id: dict[str, str] = {}
    programmes: dict[str, list[dict]] = {}

    for elem in root.findall("channel"):
        xmltv_id = str(elem.get("id") or "").strip()
        if not xmltv_id:
            continue
        lcn = str(elem.findtext("lcn") or "").strip()
        if not lcn:
            for node in elem.findall("display-name"):
                text = str(node.text or "").strip()
                first = text.split(None, 1)[0] if text else ""
                if first and all(part.isdigit() for part in first.split(".")):
                    lcn = first
                    break
        if lcn:
            lcn_to_id[lcn] = xmltv_id

    for prog in root.findall("programme"):
        channel_id = str(prog.get("channel") or "").strip()
        if not channel_id:
            continue
        title = str(prog.findtext("title") or "Untitled").strip() or "Untitled"
        desc = str(prog.findtext("desc") or "").strip()
        start = parse_xmltv_dt(str(prog.get("start") or ""))
        stop = parse_xmltv_dt(str(prog.get("stop") or ""))
        programmes.setdefault(channel_id, []).append(
            {
                "title": title,
                "desc": desc,
                "start": start.isoformat(),
                "stop": stop.isoformat(),
            }
        )
    for entries in programmes.values():
        entries.sort(key=lambda item: item["start"])
    return lcn_to_id, programmes


def _fallback_programmes(name: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    end = start + timedelta(days=8)
    entries: list[dict] = []
    while start < end:
        stop = start + timedelta(hours=4)
        entries.append(
            {
                "title": name,
                "desc": "Programming information unavailable.",
                "start": start.isoformat(),
                "stop": stop.isoformat(),
            }
        )
        start = stop
    return entries


def merge_hdhomerun_testing_into_guide(
    config: dict,
    channels: list[dict],
    programmes: dict[str, list[dict]],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Merge selected physical HDHomeRun Testing channels into the rendered Guide.

    The physical tuner streaming implementation is deliberately not involved here.
    This function consumes only persisted discovery/selection metadata and the
    SiliconDust XMLTV guide feed.  Failure to retrieve guide data is non-fatal:
    selected channels remain visible with a fallback programme block.
    """
    selected = selected_testing_channels(config)
    if not selected:
        return channels, programmes

    merged_channels = [dict(channel) for channel in channels]
    merged_programmes = {key: list(value) for key, value in programmes.items()}
    existing_ids = {str(channel.get("id") or "").strip() for channel in merged_channels}
    existing_pairs = {
        (str(channel.get("number") or "").strip(), str(channel.get("name") or "").strip().casefold())
        for channel in merged_channels
    }

    lcn_to_xmltv_id: dict[str, str] = {}
    upstream_programmes: dict[str, list[dict]] = {}
    auth = _device_auth(config)
    if auth:
        try:
            lcn_to_xmltv_id, upstream_programmes = _parse_silicondust_xmltv(_fetch_xmltv(auth))
        except Exception:
            # Guide rendering must remain available even when SiliconDust guide
            # service/network access is temporarily unavailable.
            lcn_to_xmltv_id = {}
            upstream_programmes = {}

    for item in selected:
        key = str(item.get("key") or "").strip()
        number = str(item.get("GuideNumber") or item.get("ChannelNumber") or "").strip()
        name = str(item.get("GuideName") or item.get("CallSign") or number or "HDHomeRun").strip()
        upstream_id = str(item.get("XMLTVID") or item.get("upstream_xmltv_id") or "").strip()
        if not upstream_id:
            upstream_id = lcn_to_xmltv_id.get(number, "")

        # If the imported M3U/XMLTV already carries this exact station, keep the
        # imported row authoritative rather than displaying a duplicate.
        if upstream_id and upstream_id in existing_ids:
            continue
        pair = (number, name.casefold())
        if pair in existing_pairs:
            continue

        channel_id = f"rsmc-hdhr-physical-{key}"
        if channel_id in existing_ids:
            continue
        merged_channels.append(
            {
                "id": channel_id,
                "name": name,
                "number": number,
                "group": "HDHomeRun",
                "logo": str(item.get("logo_override") or item.get("logo") or "").strip(),
                "stream_url": "",
                "source_kind": "hdhomerun-testing",
                "upstream_xmltv_id": upstream_id,
            }
        )
        existing_ids.add(channel_id)
        existing_pairs.add(pair)
        guide_entries = upstream_programmes.get(upstream_id, []) if upstream_id else []
        merged_programmes[channel_id] = list(guide_entries) if guide_entries else _fallback_programmes(name)

    return merged_channels, merged_programmes


def rebroadcast_export_programmes(config: dict) -> dict[str, list[dict]]:
    """Return SiliconDust EPG rows keyed by RSMC's stable rebroadcast tvg-id.

    Only physical channels that are both selected under Use and explicitly
    marked for rebroadcast are included. The SiliconDust channel id is mapped
    onto RSMC's stable id so channel.m3u and channel.xmltv always agree.
    """
    raw = config.get("hdhomerun_testing_channels")
    if not isinstance(raw, list):
        return {}
    selected = [
        item for item in raw
        if isinstance(item, dict)
        and _enabled(item.get("enabled"), True)
        and _enabled(item.get("rebroadcast"), False)
        and str(item.get("key") or "").strip()
    ]
    if not selected:
        return {}

    lcn_to_xmltv_id: dict[str, str] = {}
    upstream_programmes: dict[str, list[dict]] = {}
    auth = _device_auth(config)
    if auth:
        try:
            lcn_to_xmltv_id, upstream_programmes = _parse_silicondust_xmltv(_fetch_xmltv(auth))
        except Exception:
            lcn_to_xmltv_id = {}
            upstream_programmes = {}

    result: dict[str, list[dict]] = {}
    for item in selected:
        key = str(item.get("key") or "").strip()
        number = str(item.get("GuideNumber") or item.get("ChannelNumber") or "").strip()
        name = str(item.get("GuideName") or item.get("CallSign") or number or "HDHomeRun").strip()
        upstream_id = str(item.get("XMLTVID") or item.get("upstream_xmltv_id") or "").strip()
        if not upstream_id:
            upstream_id = lcn_to_xmltv_id.get(number, "")
        stable_id = f"rsmc-hdhr-physical-{key}"
        rows = upstream_programmes.get(upstream_id, []) if upstream_id else []
        result[stable_id] = list(rows) if rows else _fallback_programmes(name)
    return result
