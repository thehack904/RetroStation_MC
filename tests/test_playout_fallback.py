from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from app.playout_fallback import (
    FALLBACK_VIDEO_ITEM,
    FALLBACK_VIRTUAL_CHANNEL_ITEM,
    FallbackReason,
    PlayoutFallbackEvent,
    PlayoutFallbackHandler,
    _default_availability_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingLogger:
    """Minimal logger stub that records calls for assertion in tests."""

    def __init__(self) -> None:
        self.warnings: List[tuple[str, str]] = []
        self.infos: List[tuple[str, str]] = []

    def warning(self, category: str, message: str) -> None:
        self.warnings.append((category, message))

    def info(self, category: str, message: str) -> None:
        self.infos.append((category, message))


def _video_item(source: str = "/media/clip.mp4", duration: float = 30) -> Dict[str, Any]:
    return {"type": "video", "source": source, "duration": duration}


def _promo_item(source: str = "/media/promo.mp4", duration: float = 15) -> Dict[str, Any]:
    return {"type": "promo", "source": source, "duration": duration}


def _virtual_channel_item(source: str = "weather", duration: float = 60) -> Dict[str, Any]:
    return {"type": "virtual_channel", "source": source, "duration": duration}


def _preview_channel_item(source: str = "guide", duration: float = 120) -> Dict[str, Any]:
    return {"type": "preview_channel", "source": source, "duration": duration}


def _standby_item(source: str = "default", duration: float = 60) -> Dict[str, Any]:
    return {"type": "standby", "source": source, "duration": duration}


def _always_available(source: str) -> bool:  # noqa: ARG001
    return True


def _never_available(source: str) -> bool:  # noqa: ARG001
    return False


# ---------------------------------------------------------------------------
# PlayoutFallbackEvent
# ---------------------------------------------------------------------------


class PlayoutFallbackEventTests(unittest.TestCase):
    def test_to_dict_is_json_serialisable(self) -> None:
        import json

        event = PlayoutFallbackEvent(
            original_item=_video_item(),
            fallback_item=_standby_item(),
            reason=FallbackReason.FILE_NOT_FOUND,
            detail="source '/media/clip.mp4' does not exist",
        )
        result = event.to_dict()
        # Must serialise without raising
        json.dumps(result)

    def test_to_dict_contains_required_keys(self) -> None:
        event = PlayoutFallbackEvent(
            original_item=_video_item(),
            fallback_item=_standby_item(),
            reason=FallbackReason.FILE_NOT_FOUND,
        )
        d = event.to_dict()
        self.assertIn("original_item", d)
        self.assertIn("fallback_item", d)
        self.assertIn("reason", d)
        self.assertIn("detail", d)

    def test_original_item_is_deep_copied(self) -> None:
        original = _video_item()
        event = PlayoutFallbackEvent(
            original_item=original,
            fallback_item=_standby_item(),
            reason=FallbackReason.FILE_NOT_FOUND,
        )
        original["source"] = "mutated"
        self.assertEqual(event.original_item["source"], "/media/clip.mp4")


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — available items pass through unchanged
# ---------------------------------------------------------------------------


class FallbackHandlerPassThroughTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = PlayoutFallbackHandler(availability_check=_always_available)

    def test_available_video_item_returned_unchanged(self) -> None:
        item = _video_item()
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "video")
        self.assertEqual(result["source"], item["source"])

    def test_available_promo_item_returned_unchanged(self) -> None:
        item = _promo_item()
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "promo")

    def test_available_virtual_channel_item_returned_unchanged(self) -> None:
        item = _virtual_channel_item()
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "virtual_channel")
        self.assertEqual(result["source"], "weather")

    def test_available_preview_channel_item_returned_unchanged(self) -> None:
        item = _preview_channel_item()
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "preview_channel")

    def test_standby_item_always_returned_as_is(self) -> None:
        """Standby items bypass the availability check entirely."""
        handler = PlayoutFallbackHandler(availability_check=_never_available)
        item = _standby_item()
        result = handler.resolve(item)
        self.assertEqual(result["type"], "standby")

    def test_resolve_returns_deep_copy(self) -> None:
        item = _video_item()
        result = self.handler.resolve(item)
        result["source"] = "mutated"
        self.assertEqual(item["source"], "/media/clip.mp4")

    def test_no_fallback_event_recorded_when_item_is_available(self) -> None:
        self.handler.resolve(_video_item())
        self.assertIsNone(self.handler.last_fallback_event)


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — missing file-based items trigger fallback
# ---------------------------------------------------------------------------


class FallbackHandlerMissingFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log = _CapturingLogger()
        self.handler = PlayoutFallbackHandler(
            logger=self.log,
            availability_check=_never_available,
        )

    def test_missing_video_returns_standby(self) -> None:
        result = self.handler.resolve(_video_item("/media/missing.mp4"))
        self.assertEqual(result["type"], "standby")

    def test_missing_promo_returns_standby(self) -> None:
        result = self.handler.resolve(_promo_item("/media/missing_promo.mp4"))
        self.assertEqual(result["type"], "standby")

    def test_missing_video_logs_warning(self) -> None:
        self.handler.resolve(_video_item("/media/missing.mp4"))
        self.assertEqual(len(self.log.warnings), 1)
        category, message = self.log.warnings[0]
        self.assertEqual(category, "playout_fallback")
        self.assertIn("video", message)
        self.assertIn("/media/missing.mp4", message)
        self.assertIn(FallbackReason.FILE_NOT_FOUND, message)

    def test_fallback_event_is_recorded(self) -> None:
        self.handler.resolve(_video_item("/media/missing.mp4"))
        event = self.handler.last_fallback_event
        self.assertIsNotNone(event)
        self.assertEqual(event.reason, FallbackReason.FILE_NOT_FOUND)
        self.assertEqual(event.original_item["type"], "video")
        self.assertEqual(event.fallback_item["type"], "standby")

    def test_fallback_event_to_dict_for_admin(self) -> None:
        self.handler.resolve(_video_item("/media/missing.mp4"))
        d = self.handler.last_fallback_event.to_dict()
        self.assertEqual(d["reason"], FallbackReason.FILE_NOT_FOUND)
        self.assertIn("detail", d)
        self.assertIn("/media/missing.mp4", d["detail"])

    def test_empty_source_video_triggers_fallback(self) -> None:
        result = self.handler.resolve(_video_item(source=""))
        self.assertEqual(result["type"], "standby")

    def test_last_fallback_event_updated_on_each_trigger(self) -> None:
        self.handler.resolve(_video_item("/media/a.mp4"))
        self.handler.resolve(_video_item("/media/b.mp4"))
        event = self.handler.last_fallback_event
        self.assertEqual(event.original_item["source"], "/media/b.mp4")


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — unavailable source-name items trigger fallback
# ---------------------------------------------------------------------------


class FallbackHandlerEmptySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log = _CapturingLogger()
        self.handler = PlayoutFallbackHandler(logger=self.log)

    def test_empty_virtual_channel_source_returns_standby(self) -> None:
        item = _virtual_channel_item(source="")
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "standby")

    def test_blank_virtual_channel_source_returns_standby(self) -> None:
        item = _virtual_channel_item(source="   ")
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "standby")

    def test_empty_preview_channel_source_returns_standby(self) -> None:
        item = _preview_channel_item(source="")
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "standby")

    def test_empty_virtual_channel_logs_warning(self) -> None:
        self.handler.resolve(_virtual_channel_item(source=""))
        self.assertEqual(len(self.log.warnings), 1)
        category, message = self.log.warnings[0]
        self.assertEqual(category, "playout_fallback")
        self.assertIn("virtual_channel", message)
        self.assertIn(FallbackReason.SOURCE_UNAVAILABLE, message)

    def test_fallback_event_recorded_for_empty_virtual_channel(self) -> None:
        self.handler.resolve(_virtual_channel_item(source=""))
        event = self.handler.last_fallback_event
        self.assertIsNotNone(event)
        self.assertEqual(event.reason, FallbackReason.SOURCE_UNAVAILABLE)

    def test_non_empty_virtual_channel_source_passes_through(self) -> None:
        item = _virtual_channel_item(source="weather")
        result = self.handler.resolve(item)
        self.assertEqual(result["type"], "virtual_channel")
        self.assertIsNone(self.handler.last_fallback_event)


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — real filesystem availability check
# ---------------------------------------------------------------------------


class FallbackHandlerFilesystemTests(unittest.TestCase):
    def test_existing_file_passes_through(self) -> None:
        handler = PlayoutFallbackHandler()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            tmp_path = f.name
        try:
            result = handler.resolve(_video_item(source=tmp_path))
            self.assertEqual(result["type"], "video")
            self.assertIsNone(handler.last_fallback_event)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_nonexistent_file_triggers_fallback(self) -> None:
        handler = PlayoutFallbackHandler()
        result = handler.resolve(_video_item(source="/nonexistent/path/clip.mp4"))
        self.assertEqual(result["type"], "standby")
        self.assertIsNotNone(handler.last_fallback_event)


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — custom fallback items
# ---------------------------------------------------------------------------


class FallbackHandlerCustomFallbackTests(unittest.TestCase):
    def test_custom_fallback_video_item_is_used(self) -> None:
        custom = {"type": "standby", "source": "custom_standby", "duration": 30}
        handler = PlayoutFallbackHandler(
            availability_check=_never_available,
            fallback_video=custom,
        )
        result = handler.resolve(_video_item())
        self.assertEqual(result["source"], "custom_standby")
        self.assertEqual(result["duration"], 30)

    def test_custom_fallback_virtual_channel_item_is_used(self) -> None:
        custom = {"type": "standby", "source": "vc_standby", "duration": 45}
        handler = PlayoutFallbackHandler(
            fallback_virtual_channel=custom,
        )
        result = handler.resolve(_virtual_channel_item(source=""))
        self.assertEqual(result["source"], "vc_standby")

    def test_custom_fallback_items_are_deep_copied(self) -> None:
        custom = {"type": "standby", "source": "original", "duration": 60}
        handler = PlayoutFallbackHandler(
            availability_check=_never_available,
            fallback_video=custom,
        )
        custom["source"] = "mutated"
        result = handler.resolve(_video_item())
        self.assertEqual(result["source"], "original")


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — no logger (stderr fallback)
# ---------------------------------------------------------------------------


class FallbackHandlerNoLoggerTests(unittest.TestCase):
    def test_fallback_without_logger_does_not_raise(self) -> None:
        handler = PlayoutFallbackHandler(availability_check=_never_available)
        try:
            handler.resolve(_video_item())
        except Exception as exc:
            self.fail(f"resolve() raised unexpectedly: {exc}")

    def test_fallback_event_is_still_recorded_without_logger(self) -> None:
        handler = PlayoutFallbackHandler(availability_check=_never_available)
        handler.resolve(_video_item())
        self.assertIsNotNone(handler.last_fallback_event)


# ---------------------------------------------------------------------------
# PlayoutFallbackHandler — integration with PlayoutScheduler
# ---------------------------------------------------------------------------


class FallbackHandlerSchedulerIntegrationTests(unittest.TestCase):
    """Verify that the handler composes correctly with PlayoutScheduler."""

    def _make_doc(self, source: str = "/media/real.mp4") -> dict:
        return {
            "channel": "test",
            "items": [
                {"type": "video", "source": source, "duration": 10},
                {"type": "virtual_channel", "source": "weather", "duration": 20},
            ],
        }

    def test_unavailable_item_is_replaced_when_composed_with_scheduler(self) -> None:
        from app.playout_scheduler import PlayoutScheduler

        log = _CapturingLogger()
        handler = PlayoutFallbackHandler(
            logger=log,
            availability_check=_never_available,
        )
        scheduler = PlayoutScheduler(self._make_doc(), time_fn=lambda: 0.0)

        resolved = handler.resolve(scheduler.active_item(now=0.0))
        self.assertEqual(resolved["type"], "standby")
        self.assertEqual(len(log.warnings), 1)

    def test_available_item_passes_through_when_composed_with_scheduler(self) -> None:
        from app.playout_scheduler import PlayoutScheduler

        handler = PlayoutFallbackHandler(availability_check=_always_available)
        scheduler = PlayoutScheduler(self._make_doc(), time_fn=lambda: 0.0)

        resolved = handler.resolve(scheduler.active_item(now=0.0))
        self.assertEqual(resolved["type"], "video")
        self.assertIsNone(handler.last_fallback_event)


# ---------------------------------------------------------------------------
# _default_availability_check
# ---------------------------------------------------------------------------


class DefaultAvailabilityCheckTests(unittest.TestCase):
    def test_existing_file_returns_true(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            self.assertTrue(_default_availability_check(f.name))

    def test_nonexistent_path_returns_false(self) -> None:
        self.assertFalse(_default_availability_check("/no/such/file.mp4"))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_default_availability_check(""))


if __name__ == "__main__":
    unittest.main()
