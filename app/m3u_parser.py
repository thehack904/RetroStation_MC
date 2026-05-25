from __future__ import annotations

import re
from urllib.parse import urlparse

from app.source_fetch import read_text_or_file


def read_text(source: str) -> str:
    return read_text_or_file(source, timeout=15)


def parse_extinf(line: str) -> dict:
    attrs = {}
    for key, value in re.findall(r'([\w\-]+)="([^"]*)"', line):
        attrs[key] = value
    name = line.split(",", 1)[1].strip() if "," in line else "Unknown"
    attrs["display_name"] = name
    return attrs


def parse_m3u(source: str) -> list[dict]:
    text = read_text(source)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    channels = []
    current = None
    for line in lines:
        if line.startswith("#EXTINF"):
            current = parse_extinf(line)
        elif not line.startswith("#") and current is not None:
            channels.append(
                {
                    "id": current.get("tvg-id") or current.get("display_name") or line,
                    "name": current.get("tvg-name") or current.get("display_name") or "Unknown",
                    "number": current.get("tvg-chno") or str(len(channels) + 1),
                    "group": current.get("group-title", ""),
                    "logo": current.get("tvg-logo", ""),
                    "stream_url": line,
                }
            )
            current = None
    return channels
