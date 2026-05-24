from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def read_text(source: str) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        with urlopen(source, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    return Path(source).read_text(encoding="utf-8", errors="replace")


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
