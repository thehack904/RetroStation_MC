from __future__ import annotations

import copy
import time
from bisect import bisect_right
from typing import Any, Callable, Dict, List

from .playout_schema import validate_playout_document


class PlayoutScheduler:
    """Step through a validated playout document using fixed item durations."""

    def __init__(
        self,
        document: Dict[str, Any],
        *,
        loop: bool = True,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        validated = validate_playout_document(document)
        self.channel = str(validated["channel"])
        self.items: List[Dict[str, Any]] = copy.deepcopy(validated["items"])
        self.loop = bool(loop)
        self._time_fn = time_fn
        self._start_time = float(self._time_fn())
        self._durations = [float(item["duration"]) for item in self.items]
        self._boundaries: List[float] = []
        total = 0.0
        for duration in self._durations:
            total += duration
            self._boundaries.append(total)
        self._total_duration = total

    def _timeline(self, now: float | None = None) -> tuple[int, float, float]:
        ts = float(self._time_fn() if now is None else now)
        elapsed = max(0.0, ts - self._start_time)
        if self.loop:
            cycle = int(elapsed // self._total_duration)
            timeline_offset = elapsed % self._total_duration
        else:
            cycle = 0
            timeline_offset = min(elapsed, self._total_duration)
            if timeline_offset == self._total_duration:
                # Keep the final item active at/after the schedule end. Without
                # this tiny offset, bisect_right() would return len(items).
                timeline_offset = max(0.0, self._total_duration - 1e-12)
        active_index = bisect_right(self._boundaries, timeline_offset)
        active_index = min(active_index, len(self.items) - 1)
        return active_index, cycle, elapsed

    def state(self, *, now: float | None = None) -> Dict[str, Any]:
        active_index, cycle, elapsed = self._timeline(now=now)
        item_start_offset = 0.0 if active_index == 0 else self._boundaries[active_index - 1]
        item_end_offset = self._boundaries[active_index]
        item_elapsed = elapsed - (cycle * self._total_duration) - item_start_offset
        item_remaining = max(0.0, item_end_offset - item_start_offset - item_elapsed)
        next_index = (active_index + 1) % len(self.items) if self.loop else active_index + 1
        next_item = self.items[next_index] if next_index < len(self.items) else None
        return {
            "channel": self.channel,
            "loop": self.loop,
            "cycle": cycle,
            "elapsed_seconds": elapsed,
            "active_index": active_index,
            "next_index": next_index if next_item is not None else None,
            "active_item": self.items[active_index],
            "next_item": next_item,
            "item_elapsed_seconds": max(0.0, item_elapsed),
            "item_remaining_seconds": item_remaining,
            "document_duration_seconds": self._total_duration,
        }

    def active_item(self, *, now: float | None = None) -> Dict[str, Any]:
        return self.state(now=now)["active_item"]

    def next_item(self, *, now: float | None = None) -> Dict[str, Any] | None:
        return self.state(now=now)["next_item"]
