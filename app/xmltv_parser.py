from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def read_xml(source: str) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        with urlopen(source, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    return Path(source).read_text(encoding="utf-8", errors="replace")


def parse_xmltv_dt(value: str) -> datetime:
    value = value.strip()
    fmts = ["%Y%m%d%H%M%S %z", "%Y%m%d%H%M%S", "%Y%m%d%H%M %z", "%Y%m%d%H%M"]
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def parse_xmltv(source: str) -> dict[str, list[dict]]:
    xml_text = read_xml(source)
    root = ET.fromstring(xml_text)
    programmes: dict[str, list[dict]] = {}
    for prog in root.findall("programme"):
        channel = prog.attrib.get("channel", "")
        title = (prog.findtext("title") or "Untitled").strip()
        desc = (prog.findtext("desc") or "").strip()
        start = parse_xmltv_dt(prog.attrib.get("start", ""))
        stop = parse_xmltv_dt(prog.attrib.get("stop", ""))
        programmes.setdefault(channel, []).append(
            {
                "title": title,
                "desc": desc,
                "start": start.isoformat(),
                "stop": stop.isoformat(),
            }
        )
    for items in programmes.values():
        items.sort(key=lambda item: item["start"])
    return programmes
