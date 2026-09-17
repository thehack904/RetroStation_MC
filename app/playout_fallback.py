from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ---------------------------------------------------------------------------
# Default fallback item definitions
# ---------------------------------------------------------------------------

#: Fallback used when a video or promo item's source file is unavailable.
FALLBACK_VIDEO_ITEM: Dict[str, Any] = {
    "type": "standby",
    "source": "default",
    "duration": 60,
}

#: Fallback used when a virtual_channel or preview_channel item is unavailable.
FALLBACK_VIRTUAL_CHANNEL_ITEM: Dict[str, Any] = {
    "type": "standby",
    "source": "default",
    "duration": 60,
}

# ---------------------------------------------------------------------------
# Item type classification
# ---------------------------------------------------------------------------

#: Types whose availability is determined by whether the source file exists.
_FILE_BASED_TYPES: frozenset[str] = frozenset({"video", "promo"})

#: Types whose availability is determined by whether the source name is non-empty.
_SOURCE_NAME_TYPES: frozenset[str] = frozenset({"virtual_channel", "preview_channel"})

#: Types that represent standby/fallback content — always considered available.
_ALWAYS_AVAILABLE_TYPES: frozenset[str] = frozenset({"standby"})


# ---------------------------------------------------------------------------
# Fallback reason constants
# ---------------------------------------------------------------------------


class FallbackReason:
    """Human-readable reason constants for why fallback playout was triggered."""

    FILE_NOT_FOUND = "file_not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"


# ---------------------------------------------------------------------------
# Fallback event record
# ---------------------------------------------------------------------------


class PlayoutFallbackEvent:
    """Record of a single fallback trigger for a scheduled playout item.

    Stored on :class:`PlayoutFallbackHandler` so that admin endpoints can
    surface the reason for the most recent fallback.
    """

    def __init__(
        self,
        original_item: Dict[str, Any],
        fallback_item: Dict[str, Any],
        reason: str,
        detail: str = "",
    ) -> None:
        self.original_item: Dict[str, Any] = copy.deepcopy(original_item)
        self.fallback_item: Dict[str, Any] = copy.deepcopy(fallback_item)
        self.reason: str = reason
        self.detail: str = detail

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation suitable for admin diagnostics."""
        return {
            "original_item": self.original_item,
            "fallback_item": self.fallback_item,
            "reason": self.reason,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Default availability check
# ---------------------------------------------------------------------------


def _default_availability_check(source: str) -> bool:
    """Return ``True`` when *source* is a non-empty string that points to an existing file."""
    return bool(source) and Path(source).is_file()


# ---------------------------------------------------------------------------
# Fallback handler
# ---------------------------------------------------------------------------


class PlayoutFallbackHandler:
    """Check playout item availability and route to standby when unavailable.

    This class sits between the :class:`~app.playout_scheduler.PlayoutScheduler`
    and the consumer.  It inspects each item returned by the scheduler and
    replaces unavailable items with appropriate standby fallbacks before they
    reach the renderer or transcoder.

    Usage::

        from app.playout_fallback import PlayoutFallbackHandler
        from app.playout_scheduler import PlayoutScheduler

        handler = PlayoutFallbackHandler(logger=my_logger)
        scheduler = PlayoutScheduler(document)

        resolved = handler.resolve(scheduler.active_item())

    **Fallback selection:**

    - ``video`` / ``promo`` items: source file is checked with the configured
      *availability_check* callable.  If the file does not exist the item is
      replaced by *fallback_video* (defaults to :data:`FALLBACK_VIDEO_ITEM`).
    - ``virtual_channel`` / ``preview_channel`` items: source name must be
      non-empty.  Empty sources are replaced by *fallback_virtual_channel*
      (defaults to :data:`FALLBACK_VIRTUAL_CHANNEL_ITEM`).
    - ``standby`` items: always returned as-is (they *are* the fallback).

    **Diagnostics:**

    Every time a fallback is triggered the event is recorded on
    :attr:`last_fallback_event`.  If a *logger* is provided the event is also
    emitted at WARNING level under the ``playout_fallback`` category.
    """

    def __init__(
        self,
        *,
        logger: Any = None,
        availability_check: Callable[[str], bool] | None = None,
        fallback_video: Dict[str, Any] | None = None,
        fallback_virtual_channel: Dict[str, Any] | None = None,
    ) -> None:
        self._logger = logger
        self._check_available: Callable[[str], bool] = (
            availability_check if availability_check is not None
            else _default_availability_check
        )
        self._fallback_video: Dict[str, Any] = copy.deepcopy(
            fallback_video if fallback_video is not None else FALLBACK_VIDEO_ITEM
        )
        self._fallback_virtual_channel: Dict[str, Any] = copy.deepcopy(
            fallback_virtual_channel
            if fallback_virtual_channel is not None
            else FALLBACK_VIRTUAL_CHANNEL_ITEM
        )
        self._last_fallback_event: Optional[PlayoutFallbackEvent] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def last_fallback_event(self) -> Optional[PlayoutFallbackEvent]:
        """The most recent :class:`PlayoutFallbackEvent`, or ``None``."""
        return self._last_fallback_event

    def resolve(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Return *item* if available, otherwise log and return the appropriate fallback.

        Parameters
        ----------
        item:
            A playout item dict as produced by
            :class:`~app.playout_scheduler.PlayoutScheduler`.

        Returns
        -------
        dict
            A deep copy of either the original item (when available) or the
            configured fallback item (when unavailable).
        """
        item_type = item.get("type", "")

        # Standby items are always available — they represent the fallback itself
        if item_type in _ALWAYS_AVAILABLE_TYPES:
            return copy.deepcopy(item)

        unavailable, reason, detail = self._check_item_availability(item)
        if not unavailable:
            return copy.deepcopy(item)

        fallback = self._select_fallback(item_type)
        event = PlayoutFallbackEvent(
            original_item=item,
            fallback_item=fallback,
            reason=reason,
            detail=detail,
        )
        self._last_fallback_event = event
        self._emit_log(event)
        return copy.deepcopy(fallback)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_item_availability(
        self, item: Dict[str, Any]
    ) -> tuple[bool, str, str]:
        """Return ``(is_unavailable, reason, detail)`` for *item*."""
        item_type = item.get("type", "")
        source = item.get("source", "")

        if item_type in _FILE_BASED_TYPES:
            if not source or not self._check_available(source):
                return (
                    True,
                    FallbackReason.FILE_NOT_FOUND,
                    f"source {source!r} does not exist or is not accessible",
                )

        elif item_type in _SOURCE_NAME_TYPES:
            if not source or not source.strip():
                return (
                    True,
                    FallbackReason.SOURCE_UNAVAILABLE,
                    f"{item_type} source name is empty or blank",
                )

        return False, "", ""

    def _select_fallback(self, item_type: str) -> Dict[str, Any]:
        """Return the appropriate fallback item dict for *item_type*."""
        if item_type in _SOURCE_NAME_TYPES:
            return copy.deepcopy(self._fallback_virtual_channel)
        return copy.deepcopy(self._fallback_video)

    def _emit_log(self, event: PlayoutFallbackEvent) -> None:
        """Write a WARNING-level log entry for *event*."""
        original = event.original_item
        msg = (
            f"Playout fallback triggered: "
            f"type={original.get('type')!r} source={original.get('source')!r} "
            f"reason={event.reason!r}"
        )
        if event.detail:
            msg += f" — {event.detail}"
        if self._logger is not None:
            self._logger.warning("playout_fallback", msg)
        else:
            print(f"[playout_fallback] WARNING: {msg}", file=sys.stderr, flush=True)
