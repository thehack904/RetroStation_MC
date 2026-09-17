from __future__ import annotations

import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

MAX_NEWS_FEEDS = 6
NEWS_ROTATION_BLOCK_SECONDS = 30 * 60
NEWS_EMPTY_REFRESH_SECONDS = 5 * 60
NEWS_CACHE_SECONDS = 5 * 60
NEWS_FETCH_TIMEOUT_SECONDS = 8
NEWS_MAX_ITEMS = 20

_CACHE_LOCK = threading.Lock()
_FEED_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')


def validate_feed_urls(urls) -> list[str]:
    """Validate and normalize up to six HTTP(S) RSS/Atom feed URLs."""
    result: list[str] = []
    for raw in list(urls)[:MAX_NEWS_FEEDS]:
        value = str(raw or '').strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError(f"Invalid feed URL: {value!r}. Must be an http or https URL with a valid hostname.")
        result.append(value)
    return result


def get_current_feed_state(feed_count: int, now: float | None = None) -> tuple[int, int, int]:
    """Return synchronized feed index, milliseconds to transition, and elapsed slot time.

    This preserves RetroIPTVGuide's 30-minute wall-clock rotation block. Every
    viewer therefore receives the same feed slot independent of tune time.
    """
    if feed_count <= 0:
        return 0, NEWS_EMPTY_REFRESH_SECONDS * 1000, 0
    feed_duration_s = NEWS_ROTATION_BLOCK_SECONDS / feed_count
    current = time.time() if now is None else float(now)
    time_slot = int(current / feed_duration_s)
    feed_index = time_slot % feed_count
    elapsed = current % feed_duration_s
    elapsed_ms = int(elapsed * 1000)
    remaining_ms = max(1000, int((feed_duration_s - elapsed) * 1000))
    return feed_index, remaining_ms, elapsed_ms


def _clean_text(value: str | None, limit: int = 1000) -> str:
    text = html.unescape(str(value or ''))
    text = _TAG_RE.sub(' ', text)
    text = _WS_RE.sub(' ', text).strip()
    # Remove control characters except ordinary whitespace (already normalized).
    text = ''.join(ch for ch in text if ch >= ' ' or ch in '\t\n\r')
    return text[:limit]


def _local_name(tag: str) -> str:
    return tag.split('}', 1)[-1].lower()


def _first_child(element: ET.Element, *names: str) -> ET.Element | None:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if _local_name(child.tag) in wanted:
            return child
    return None


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name.lower()]


def _image_from_element(element: ET.Element) -> str:
    for child in element.iter():
        lname = _local_name(child.tag)
        if lname in {'enclosure', 'content', 'thumbnail'}:
            url = str(child.attrib.get('url') or child.attrib.get('href') or '').strip()
            ctype = str(child.attrib.get('type') or '').lower()
            if url and (lname == 'thumbnail' or ctype.startswith('image/') or re.search(r'\.(png|jpe?g|webp|gif)(\?|$)', url, re.I)):
                if urlparse(url).scheme in {'http', 'https'}:
                    return url[:2048]
    return ''


def parse_feed(payload: bytes | str, max_items: int = NEWS_MAX_ITEMS) -> list[dict[str, str]]:
    """Parse representative RSS/RDF/Atom payloads into the RSMC news model."""
    root = ET.fromstring(payload)
    local = _local_name(root.tag)
    items: list[dict[str, str]] = []

    if local == 'feed':
        channel_title_el = _first_child(root, 'title')
        channel_title = _clean_text(channel_title_el.text if channel_title_el is not None else '', 200)
        entries = _children(root, 'entry')
        for entry in entries[:max_items]:
            title_el = _first_child(entry, 'title')
            title = _clean_text(title_el.text if title_el is not None else '', 500)
            if not title:
                continue
            link = ''
            for link_el in _children(entry, 'link'):
                candidate = str(link_el.attrib.get('href') or (link_el.text or '')).strip()
                if candidate and urlparse(candidate).scheme in {'http', 'https'}:
                    link = candidate[:2048]
                    break
            ts_el = _first_child(entry, 'updated', 'published')
            summary_el = _first_child(entry, 'summary', 'content')
            items.append({
                'title': title,
                'source': channel_title,
                'url': link,
                'ts': _clean_text(ts_el.text if ts_el is not None else '', 100),
                'image': _image_from_element(entry),
                'summary': _clean_text(summary_el.text if summary_el is not None else '', 1200),
            })
    else:
        channel_node = _first_child(root, 'channel')
        channel = channel_node if channel_node is not None else root
        title_el = _first_child(channel, 'title')
        channel_title = _clean_text(title_el.text if title_el is not None else '', 200)
        entries = _children(channel, 'item')
        # RSS 1.0/RDF often places item nodes as root siblings rather than under channel.
        if not entries:
            entries = [node for node in list(root) if _local_name(node.tag) == 'item']
        for entry in entries[:max_items]:
            title_node = _first_child(entry, 'title')
            title = _clean_text(title_node.text if title_node is not None else '', 500)
            if not title:
                continue
            link_node = _first_child(entry, 'link')
            candidate = str(link_node.text or '').strip() if link_node is not None else ''
            link = candidate[:2048] if urlparse(candidate).scheme in {'http', 'https'} else ''
            ts_node = _first_child(entry, 'pubdate', 'date', 'updated')
            desc_node = _first_child(entry, 'description', 'summary', 'content')
            items.append({
                'title': title,
                'source': channel_title,
                'url': link,
                'ts': _clean_text(ts_node.text if ts_node is not None else '', 100),
                'image': _image_from_element(entry),
                'summary': _clean_text(desc_node.text if desc_node is not None else '', 1200),
            })
    return items


def fetch_feed(url: str, *, max_items: int = NEWS_MAX_ITEMS, force: bool = False) -> list[dict[str, str]]:
    """Fetch one feed with a per-URL cache. Failures return the last cached data or []."""
    now = time.time()
    with _CACHE_LOCK:
        cached = _FEED_CACHE.get(url)
        if cached and not force and now - cached[0] < NEWS_CACHE_SECONDS:
            return [dict(item) for item in cached[1]]
    try:
        response = requests.get(
            url,
            timeout=NEWS_FETCH_TIMEOUT_SECONDS,
            headers={'User-Agent': 'RetroStation-MC/1.4 NewsChannel'},
        )
        response.raise_for_status()
        parsed = parse_feed(response.content, max_items=max_items)
        with _CACHE_LOCK:
            _FEED_CACHE[url] = (now, parsed)
        return [dict(item) for item in parsed]
    except Exception:
        with _CACHE_LOCK:
            cached = _FEED_CACHE.get(url)
            return [dict(item) for item in cached[1]] if cached else []


def clear_feed_cache() -> None:
    with _CACHE_LOCK:
        _FEED_CACHE.clear()


def build_news_payload(feed_urls, now: float | None = None) -> dict[str, Any]:
    """Build one synchronized News Now state payload for API and renderer use."""
    feeds = validate_feed_urls(feed_urls)
    feed_count = len(feeds)
    feed_index, ms_until_next, elapsed_ms = get_current_feed_state(feed_count, now=now)
    current_ts = time.time() if now is None else float(now)
    slot_start = datetime.fromtimestamp(current_ts, tz=timezone.utc) - timedelta(milliseconds=elapsed_ms)
    headlines = fetch_feed(feeds[feed_index]) if feeds else []
    return {
        'updated': slot_start.isoformat(),
        'headlines': headlines,
        'feed_count': feed_count,
        'feed_index': feed_index,
        'refresh_ms': int((NEWS_ROTATION_BLOCK_SECONDS * 1000) / feed_count) if feed_count else NEWS_EMPTY_REFRESH_SECONDS * 1000,
        'ms_until_next_feed': ms_until_next,
        'configured': bool(feeds),
        'active_feed_url': feeds[feed_index] if feeds else '',
    }
