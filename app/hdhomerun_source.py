from __future__ import annotations

import json
import socket
import struct
import xml.etree.ElementTree as ET
from hashlib import sha1
from typing import Any
from urllib.parse import quote_plus, urlparse

from app.hdhomerun_discovery import (
    HDHOMERUN_DEVICE_ID_WILDCARD,
    HDHOMERUN_DEVICE_TYPE_WILDCARD,
    HDHOMERUN_MAX_PACKET_SIZE,
    HDHOMERUN_TAG_BASE_URL,
    HDHOMERUN_TAG_DEVICE_ID,
    HDHOMERUN_TAG_DEVICE_TYPE,
    HDHOMERUN_TAG_LINEUP_URL,
    HDHOMERUN_TYPE_DISCOVER_REQ,
    _tlv,
    _u32,
    build_frame,
    parse_frame,
    parse_tlvs,
)
from app.source_fetch import read_text_or_file

HDHOMERUN_DISCOVER_UDP_PORT = 65001


def normalize_base_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text.rstrip("/")


def _read_json(source: str, *, timeout: int = 8) -> Any:
    return json.loads(read_text_or_file(source, timeout=timeout))


def _metadata_from_discover(discover: dict[str, Any], *, fallback_base_url: str) -> dict[str, Any]:
    base_url = normalize_base_url(str(discover.get("BaseURL") or fallback_base_url))
    lineup_url = str(discover.get("LineupURL") or "").strip()
    if not lineup_url and base_url:
        lineup_url = f"{base_url}/lineup.json"
    return {
        "FriendlyName": str(discover.get("FriendlyName") or "").strip(),
        "ModelNumber": str(discover.get("ModelNumber") or "").strip(),
        "FirmwareVersion": str(discover.get("FirmwareVersion") or "").strip(),
        "DeviceID": str(discover.get("DeviceID") or "").strip(),
        "DeviceAuth": str(discover.get("DeviceAuth") or "").strip(),
        "BaseURL": base_url,
        "LineupURL": lineup_url,
        "TunerCount": int(discover.get("TunerCount") or 0),
    }


def fetch_device_and_lineup(base_url: str, *, timeout: int = 8) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = normalize_base_url(base_url)
    if not normalized:
        raise ValueError("Invalid HDHomeRun hostname/IP")
    discover = _read_json(f"{normalized}/discover.json", timeout=timeout)
    if not isinstance(discover, dict):
        raise ValueError("Invalid discover.json payload")
    metadata = _metadata_from_discover(discover, fallback_base_url=normalized)
    lineup_raw = _read_json(metadata["LineupURL"], timeout=timeout)
    if not isinstance(lineup_raw, list):
        raise ValueError("Invalid lineup.json payload")
    lineup: list[dict[str, Any]] = [item for item in lineup_raw if isinstance(item, dict)]
    return metadata, lineup



def fetch_silicondust_xmltv_channels(device_auth: str, *, timeout: int = 12) -> dict[str, dict[str, str]]:
    """Return SiliconDust XMLTV channel metadata keyed by logical channel number.

    The local HDHomeRun lineup does not contain station artwork.  SiliconDust's
    authenticated XMLTV feed does, including channel-level <icon> elements.
    Failure to load guide metadata is intentionally non-fatal so local lineup
    discovery continues to work when Internet guide data is unavailable.
    """
    auth = str(device_auth or "").strip()
    if not auth:
        return {}
    url = f"https://api.hdhomerun.com/api/xmltv?DeviceAuth={quote_plus(auth)}"
    xml_text = read_text_or_file(url, timeout=timeout)
    root = ET.fromstring(xml_text)
    result: dict[str, dict[str, str]] = {}
    for channel in root.findall("channel"):
        lcn = str(channel.findtext("lcn") or "").strip()
        if not lcn:
            # Some feeds encode the logical channel only in display-name.
            for node in channel.findall("display-name"):
                text = str(node.text or "").strip()
                first = text.split(None, 1)[0] if text else ""
                if first and all(part.isdigit() for part in first.split(".")):
                    lcn = first
                    break
        if not lcn:
            continue
        icon = channel.find("icon")
        icon_url = str(icon.get("src") if icon is not None else "").strip()
        names = [str(node.text or "").strip() for node in channel.findall("display-name") if str(node.text or "").strip()]
        result[lcn] = {
            "xmltv_id": str(channel.get("id") or "").strip(),
            "icon": icon_url,
            "name": names[-1] if names else "",
        }
    return result


def apply_xmltv_channel_metadata(
    lineup: list[dict[str, Any]],
    xmltv_channels: dict[str, dict[str, str]] | None,
) -> list[dict[str, Any]]:
    """Attach SiliconDust XMLTV id/icon metadata to local lineup rows."""
    metadata = xmltv_channels or {}
    enriched: list[dict[str, Any]] = []
    for item in lineup:
        row = dict(item)
        guide_number = str(row.get("GuideNumber") or "").strip()
        match = metadata.get(guide_number) or {}
        if match.get("icon"):
            row["LogoURL"] = str(match["icon"]).strip()
        if match.get("xmltv_id"):
            row["XMLTVID"] = str(match["xmltv_id"]).strip()
        enriched.append(row)
    return enriched

def discover_base_urls(*, timeout: float = 1.5) -> list[str]:
    request_payload = b"".join(
        (
            _tlv(HDHOMERUN_TAG_DEVICE_TYPE, _u32(HDHOMERUN_DEVICE_TYPE_WILDCARD)),
            _tlv(HDHOMERUN_TAG_DEVICE_ID, _u32(HDHOMERUN_DEVICE_ID_WILDCARD)),
        )
    )
    packet = build_frame(HDHOMERUN_TYPE_DISCOVER_REQ, request_payload)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(max(0.2, float(timeout)))
    discovered: list[str] = []
    seen: set[str] = set()
    try:
        sock.sendto(packet, ("255.255.255.255", HDHOMERUN_DISCOVER_UDP_PORT))
        while True:
            try:
                response, _ = sock.recvfrom(HDHOMERUN_MAX_PACKET_SIZE)
            except socket.timeout:
                break
            try:
                _, payload = parse_frame(response)
                tlvs = dict(parse_tlvs(payload))
            except Exception:
                continue
            raw_base_url = str(tlvs.get(HDHOMERUN_TAG_BASE_URL, b"").decode("utf-8", errors="replace"))
            if not raw_base_url:
                continue
            normalized = normalize_base_url(raw_base_url)
            if normalized and normalized not in seen:
                seen.add(normalized)
                discovered.append(normalized)
    finally:
        sock.close()
    return discovered


def _lineup_value(line: dict[str, Any], key: str, default: Any = "") -> Any:
    value = line.get(key, default)
    return default if value is None else value


def _lineup_identity(line: dict[str, Any]) -> str:
    guide_number = str(_lineup_value(line, "GuideNumber")).strip()
    guide_name = str(_lineup_value(line, "GuideName")).strip()
    stream_url = str(_lineup_value(line, "URL")).strip()
    digest = sha1(f"{guide_number}\x1f{guide_name}\x1f{stream_url}".encode("utf-8")).hexdigest()
    return digest[:20]


def merge_lineup_channels(
    lineup: list[dict[str, Any]],
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    existing = existing or []
    existing_by_key = {
        str(item.get("key") or "").strip(): item
        for item in existing
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    existing_by_pair = {
        (
            str(item.get("GuideNumber") or "").strip(),
            str(item.get("GuideName") or "").strip(),
        ): item
        for item in existing
        if isinstance(item, dict)
    }
    merged: list[dict[str, Any]] = []
    for index, line in enumerate(lineup):
        key = _lineup_identity(line)
        guide_number = str(_lineup_value(line, "GuideNumber", index + 1)).strip() or str(index + 1)
        guide_name = str(_lineup_value(line, "GuideName", f"Channel {index + 1}")).strip() or f"Channel {index + 1}"
        previous = existing_by_key.get(key) or existing_by_pair.get((guide_number, guide_name)) or {}
        channel_number = str(previous.get("ChannelNumber") or guide_number).strip() or guide_number
        merged.append(
            {
                "key": key,
                "enabled": bool(previous.get("enabled", True)),
                "rebroadcast": bool(previous.get("rebroadcast", False)),
                "ChannelNumber": channel_number,
                "GuideNumber": guide_number,
                "GuideName": guide_name,
                "URL": str(_lineup_value(line, "URL")).strip(),
                "VideoCodec": str(_lineup_value(line, "VideoCodec")).strip(),
                "AudioCodec": str(_lineup_value(line, "AudioCodec")).strip(),
                "HD": bool(_lineup_value(line, "HD", False)),
                "SignalStrength": _lineup_value(line, "SignalStrength", ""),
                "SignalQuality": _lineup_value(line, "SignalQuality", ""),
                "CallSign": str(_lineup_value(line, "CallSign")).strip(),
                "StationName": str(_lineup_value(line, "StationName")).strip(),
                "XMLTVID": str(_lineup_value(line, "XMLTVID") or previous.get("XMLTVID") or "").strip(),
                "logo": str(_lineup_value(line, "LogoURL") or previous.get("logo") or "").strip(),
                "logo_source": str(previous.get("logo_source") or "").strip(),
                "logo_cache_file": str(previous.get("logo_cache_file") or "").strip(),
                "logo_override": str(previous.get("logo_override") or previous.get("LogoOverride") or "").strip(),
                "upstream_xmltv_id": str(previous.get("upstream_xmltv_id") or "").strip(),
            }
        )
    return merged
