from __future__ import annotations

import re
from collections import Counter
from typing import Any

_SOURCE_LABELS = {
    "rsmc-virtual": "RSMC",
    "playlist": "IPTV",
    "hdhomerun-testing": "HDHR",
}
_SOURCE_PRIORITY = {
    "rsmc-virtual": 0,
    "playlist": 1,
    "hdhomerun-testing": 2,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _number_key(value: Any) -> tuple:
    """Return a natural channel-number sort key.

    Numeric channel numbers and subchannels sort numerically (2, 2.1, 2.2, 10).
    Mixed/non-numeric labels remain deterministic and sort after numeric values.
    """
    text = _clean(value)
    if not text:
        return (2, (), "")
    if re.fullmatch(r"\d+(?:\.\d+)*", text):
        return (0, tuple(int(part) for part in text.split(".")), "")
    match = re.match(r"^(\d+(?:\.\d+)*)(.*)$", text)
    if match:
        return (1, tuple(int(part) for part in match.group(1).split(".")), match.group(2).casefold())
    return (2, (), text.casefold())


def _source_kind(channel: dict) -> str:
    explicit = _clean(channel.get("source_kind"))
    if explicit:
        return explicit
    channel_id = _clean(channel.get("id"))
    if channel_id.startswith("rsmc-"):
        return "rsmc-virtual"
    return "playlist"


def finalize_guide_lineup(channels: list[dict]) -> list[dict]:
    """Normalize, disambiguate collisions, and sort the unified Guide lineup.

    Channel number is a display attribute, not an identity. Stable ``id`` values
    remain authoritative. When multiple logical channels share the same display
    number, the rendered Guide receives a compact source marker alongside the
    original number while source numbering remains unchanged for other consumers.
    """
    normalized: list[dict] = []
    for index, raw in enumerate(channels):
        channel = dict(raw)
        channel["source_kind"] = _source_kind(channel)
        channel["_registry_index"] = index
        normalized.append(channel)

    number_counts = Counter(_clean(channel.get("number")) for channel in normalized if _clean(channel.get("number")))
    for channel in normalized:
        number = _clean(channel.get("number"))
        name = _clean(channel.get("name")) or "Unknown"
        source_kind = _source_kind(channel)
        if number and number_counts[number] > 1:
            label = _SOURCE_LABELS.get(source_kind, source_kind.upper() or "SOURCE")
            badge = {"RSMC": "R", "IPTV": "I", "HDHR": "H"}.get(label, label[:1] or "?")
            channel["guide_number_label"] = f"{number} [{badge}]"
            channel["guide_source_label"] = label
        else:
            channel["guide_number_label"] = number
            channel["guide_source_label"] = _SOURCE_LABELS.get(source_kind, source_kind.upper() or "SOURCE")

    normalized.sort(
        key=lambda channel: (
            _number_key(channel.get("number")),
            _SOURCE_PRIORITY.get(_source_kind(channel), 50),
            _clean(channel.get("name")).casefold(),
            _clean(channel.get("id")).casefold(),
            int(channel.get("_registry_index", 0)),
        )
    )
    for channel in normalized:
        channel.pop("_registry_index", None)
    return normalized


def sort_exported_lineup(channels: list[dict]) -> list[dict]:
    """Return combined M3U/XMLTV entries in Guide-equivalent channel order.

    Exported channel numbers are display attributes, not identities.  Sort the
    complete aggregated lineup numerically across RSMC virtual, imported source,
    and HDHomeRun entries instead of preserving source-block append order.
    Duplicate channel numbers remain unchanged and use the same source-priority
    tie break as the rendered Guide.
    """
    normalized: list[dict] = []
    for index, raw in enumerate(channels):
        channel = dict(raw)
        source_kind = _clean(channel.get("source_kind"))
        if source_kind == "source":
            source_kind = "playlist"
        elif source_kind == "hdhomerun":
            source_kind = "hdhomerun-testing"
        elif not source_kind and _clean(channel.get("id")).startswith("rsmc-"):
            source_kind = "rsmc-virtual"
        channel["_export_source_kind"] = source_kind or _source_kind(channel)
        channel["_export_index"] = index
        normalized.append(channel)

    normalized.sort(
        key=lambda channel: (
            _number_key(channel.get("channel_number")),
            _SOURCE_PRIORITY.get(_clean(channel.get("_export_source_kind")), 50),
            _clean(channel.get("name")).casefold(),
            _clean(channel.get("id")).casefold(),
            int(channel.get("_export_index", 0)),
        )
    )
    for channel in normalized:
        channel.pop("_export_source_kind", None)
        channel.pop("_export_index", None)
    return normalized
