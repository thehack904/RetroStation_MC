from __future__ import annotations

import math
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterable

DEFAULT_NAME = "Channel Mix"
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = 1440


def normalize_channel_mix_config(name, channels, valid_ids: Iterable[str]) -> dict:
    valid_ids = set(valid_ids)
    clean_name = str(name or "").strip() or DEFAULT_NAME
    if len(clean_name) > 120:
        raise ValueError("Channel Mix name must be 120 characters or fewer")
    out = []
    seen = set()
    for entry in channels or []:
        channel_id = str(entry.get("channel_id", entry.get("tvg_id", ""))).strip()
        if channel_id not in valid_ids:
            raise ValueError(f"Invalid channel ID for Channel Mix: {channel_id!r}")
        if channel_id in seen:
            continue
        try:
            minutes = int(entry.get("duration_minutes", 120))
        except (TypeError, ValueError):
            raise ValueError("duration_minutes must be an integer")
        if not MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES:
            raise ValueError(f"duration_minutes must be 1–1440, got {minutes}")
        seen.add(channel_id)
        out.append({"channel_id": channel_id, "duration_minutes": minutes})
    return {"name": clean_name, "channels": out}


def get_active_channel_mix_slot(channels, now: float | None = None):
    """Return (channel_id, seconds_remaining) using Unix-time wall-clock alignment."""
    if not channels:
        return None, 0
    total = sum(int(c["duration_minutes"]) * 60 for c in channels)
    if total <= 0:
        return None, 0
    timestamp = int(time.time() if now is None else now)
    offset = timestamp % total
    elapsed = 0
    for entry in channels:
        slot = int(entry["duration_minutes"]) * 60
        if offset < elapsed + slot:
            return entry["channel_id"], elapsed + slot - offset
        elapsed += slot
    first = channels[0]
    return first["channel_id"], int(first["duration_minutes"]) * 60


def get_active_available_channel(channels, available_ids: Iterable[str], now: float | None = None):
    """Return scheduled source, falling forward to the next available member.

    The wall-clock schedule is never compressed when a source is unavailable;
    fallback selection lasts only until the current nominal slot boundary.
    """
    scheduled, remaining = get_active_channel_mix_slot(channels, now=now)
    if scheduled is None:
        return None, 0, None
    available = set(available_ids)
    ids = [c["channel_id"] for c in channels]
    try:
        start = ids.index(scheduled)
    except ValueError:
        return None, remaining, scheduled
    for delta in range(len(ids)):
        candidate = ids[(start + delta) % len(ids)]
        if candidate in available:
            return candidate, remaining, scheduled
    return None, remaining, scheduled


def parse_media_playlist(text: str) -> tuple[int, list[tuple[float, str]]]:
    """Return ``(target_duration, [(duration, uri), ...])`` from a media playlist.

    Channel Mix only consumes RSMC-owned MPEG-TS media playlists, so a compact
    parser is preferable to bringing another HLS dependency into the project.
    """
    target = 6
    pending_duration: float | None = None
    segments: list[tuple[float, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target = max(1, int(float(line.split(":", 1)[1])))
            except (TypeError, ValueError):
                pass
        elif line.startswith("#EXTINF:"):
            try:
                pending_duration = max(0.001, float(line.split(":", 1)[1].split(",", 1)[0]))
            except (TypeError, ValueError):
                pending_duration = float(target)
        elif not line.startswith("#") and pending_duration is not None:
            segments.append((pending_duration, line))
            pending_duration = None
    return target, segments


class ChannelMixHLSState:
    """Build one continuous HLS timeline from already-running RSMC channels.

    The member channels intentionally use epoch-aligned media sequence numbers.
    Proxying one member playlist and then another therefore makes players such
    as VLC believe the new source's segments were already consumed.  In
    addition, each encoder has an independent MPEG-TS timestamp timeline.

    Channel Mix solves both issues by owning a small sliding HLS window:

    * source .ts files are hard-linked/copied to Channel-Mix-owned filenames;
    * the mix assigns one monotonically increasing media sequence;
    * a discontinuity is inserted whenever the selected source changes; and
    * old mix files are retired only after they leave the mix window.

    This keeps VLC on the same URL through every source boundary and through the
    last-member -> first-member wall-clock wrap without starting another
    renderer, encoder, or provider fetch.
    """

    def __init__(self, output_dir: str | Path, *, window_size: int = 10) -> None:
        self.output_dir = Path(output_dir)
        self.window_size = max(4, int(window_size))
        self._lock = threading.Lock()
        self._records: deque[dict] = deque()
        self._seen: set[tuple[str, str]] = set()
        self._seen_order: deque[tuple[str, str]] = deque()
        self._last_source: str | None = None
        self._next_sequence = int(time.time()) // 6
        self._discarded_discontinuities = 0

    def _remember_seen(self, key: tuple[str, str]) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self._seen_order.append(key)
        while len(self._seen_order) > 256:
            old = self._seen_order.popleft()
            self._seen.discard(old)

    def _materialize(self, source_uri: str, sequence: int) -> str | None:
        # RSMC member playlists use same-directory relative MPEG-TS names. Do
        # not allow a feed/playlist value to escape OUTPUT_DIR.
        source_name = Path(source_uri.split("?", 1)[0]).name
        if not source_name.lower().endswith(".ts"):
            return None
        source = self.output_dir / source_name
        if not source.is_file():
            return None
        dest_name = f"channel_mix_{sequence}.ts"
        dest = self.output_dir / dest_name
        try:
            if dest.exists():
                dest.unlink()
            try:
                os.link(source, dest)
            except OSError:
                shutil.copy2(source, dest)
            return dest_name
        except OSError:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _append(self, source_id: str, duration: float, uri: str, *, discontinuity: bool) -> bool:
        key = (source_id, uri)
        if key in self._seen:
            return False
        seq = self._next_sequence
        mix_uri = self._materialize(uri, seq)
        if not mix_uri:
            return False
        self._next_sequence += 1
        self._remember_seen(key)
        self._records.append({
            "sequence": seq,
            "duration": float(duration),
            "uri": mix_uri,
            "source": source_id,
            "discontinuity": bool(discontinuity),
        })
        while len(self._records) > self.window_size:
            old = self._records.popleft()
            if old["discontinuity"]:
                self._discarded_discontinuities += 1
            try:
                (self.output_dir / old["uri"]).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def build(self, source_id: str, playlist_text: str) -> str | None:
        target, source_segments = parse_media_playlist(playlist_text)
        if not source_segments:
            return None

        with self._lock:
            source_changed = self._last_source is not None and self._last_source != source_id
            initial = self._last_source is None

            # On first tune provide enough history for immediate startup. At a
            # source boundary use only the newest two source segments: the
            # existing mix-owned history supplies pre-boundary buffer, while
            # importing the source's whole 60-second window would delay the
            # requested wall-clock switch by up to a minute.
            candidates = source_segments[-6:] if initial else (source_segments[-2:] if source_changed else source_segments)
            inserted = 0
            for duration, uri in candidates:
                if (source_id, uri) in self._seen:
                    continue
                did_add = self._append(
                    source_id,
                    duration,
                    uri,
                    discontinuity=source_changed and inserted == 0,
                )
                if did_add:
                    inserted += 1

            # Once we accept a source/window, mark the rest of that source's
            # currently visible history as already observed.  Otherwise the
            # next reload after a switch would append the older pre-switch
            # segments *after* the two newest switch segments, making playback
            # jump backward before moving forward again.
            accepted_window = initial or not source_changed or inserted > 0
            if accepted_window:
                for _duration, uri in source_segments:
                    self._remember_seen((source_id, uri))

            # If a switch happened before the new encoder had produced a URI we
            # had not seen on a prior cycle, retain the old source momentarily
            # and try again on the next HLS reload rather than publishing an
            # invalid transition.
            if source_changed and inserted == 0:
                source_id = self._last_source or source_id
            else:
                self._last_source = source_id

            if not self._records:
                return None

            max_duration = max([float(target)] + [r["duration"] for r in self._records])
            lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                f"#EXT-X-TARGETDURATION:{max(1, int(math.ceil(max_duration)))}",
                f"#EXT-X-MEDIA-SEQUENCE:{self._records[0]['sequence']}",
                f"#EXT-X-DISCONTINUITY-SEQUENCE:{self._discarded_discontinuities}",
                "#EXT-X-INDEPENDENT-SEGMENTS",
            ]
            for record in self._records:
                if record["discontinuity"]:
                    lines.append("#EXT-X-DISCONTINUITY")
                lines.append(f"#EXTINF:{record['duration']:.6f},")
                lines.append(record["uri"])
            return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            for record in self._records:
                try:
                    (self.output_dir / record["uri"]).unlink(missing_ok=True)
                except OSError:
                    pass
            self._records.clear()
            self._seen.clear()
            self._seen_order.clear()
            self._last_source = None
            self._next_sequence = int(time.time()) // 6
            self._discarded_discontinuities = 0
