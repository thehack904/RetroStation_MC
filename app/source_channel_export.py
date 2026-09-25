from __future__ import annotations

import hashlib
from typing import Any


def source_channel_key(channel: dict[str, Any], index: int) -> str:
    """Return a stable opaque key for an imported Playlist Source channel.

    The URL is deliberately excluded so expiring tokens or regenerated HLS URLs
    do not change the public channel identity.  Index is used only when the
    source supplies no usable identity metadata.
    """
    identity = "\x1f".join(
        str(channel.get(field) or "").strip()
        for field in ("id", "number", "name", "group")
    )
    if not identity.replace("\x1f", ""):
        identity = f"source-index:{index}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def build_source_export_entries(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert parsed Playlist Source rows into combined-export channel rows."""
    entries: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, channel in enumerate(channels):
        stream_url = str(channel.get("stream_url") or "").strip()
        if not stream_url:
            continue
        name = str(channel.get("name") or f"Channel {index + 1}").strip() or f"Channel {index + 1}"
        number = str(channel.get("number") or index + 1).strip()
        upstream_id = str(channel.get("id") or "").strip()
        base_id = f"rsmc-source-{source_channel_key(channel, index)}"
        public_id = base_id
        # Duplicate source metadata is legal.  Preserve both rows rather than
        # silently collapsing them, while retaining the stable base identity.
        if public_id in used_ids:
            public_id = f"{base_id}-{index + 1}"
        used_ids.add(public_id)
        entries.append(
            {
                "id": public_id,
                "name": name,
                "description": f"Imported channel from the configured Playlist Source: {name}.",
                "channel_number": number,
                "stream_url": stream_url,
                "logo_url": str(channel.get("logo") or "").strip(),
                "group_title": str(channel.get("group") or "Source Playlist").strip() or "Source Playlist",
                "source_kind": "source",
                "upstream_xmltv_id": upstream_id,
                # Imported streams may be MPEG-2, H.264, HEVC, etc.  Do not
                # advertise a codec pair RSMC has not actually normalized.
                "advertise_h264_aac": False,
            }
        )
    return entries


def remap_source_programmes(
    entries: list[dict[str, Any]],
    upstream_programmes: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Map upstream XMLTV ids onto stable RSMC source-export ids."""
    result: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        upstream_id = str(entry.get("upstream_xmltv_id") or "").strip()
        public_id = str(entry.get("id") or "").strip()
        if not public_id or not upstream_id or upstream_id not in upstream_programmes:
            continue
        result[public_id] = [dict(row) for row in upstream_programmes[upstream_id]]
    return result
