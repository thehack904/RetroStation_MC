from __future__ import annotations

import unittest

from app.playout_scheduler import PlayoutScheduler


def _doc() -> dict:
    return {
        "channel": "preview-channel",
        "items": [
            {"type": "video", "source": "a.mp4", "duration": 10},
            {"type": "promo", "source": "b.mp4", "duration": 5},
            {"type": "virtual_channel", "source": "weather", "duration": 20},
        ],
    }


class PlayoutSchedulerTests(unittest.TestCase):
    def test_initial_state_exposes_active_and_next_items(self) -> None:
        scheduler = PlayoutScheduler(_doc(), time_fn=lambda: 100.0)
        state = scheduler.state(now=100.0)
        self.assertEqual(state["active_index"], 0)
        self.assertEqual(state["next_index"], 1)
        self.assertEqual(state["active_item"]["source"], "a.mp4")
        self.assertEqual(state["next_item"]["source"], "b.mp4")

    def test_scheduler_advances_through_fixed_durations(self) -> None:
        scheduler = PlayoutScheduler(_doc(), time_fn=lambda: 100.0)
        self.assertEqual(scheduler.active_item(now=109.9)["source"], "a.mp4")
        self.assertEqual(scheduler.active_item(now=110.0)["source"], "b.mp4")
        self.assertEqual(scheduler.active_item(now=115.0)["source"], "weather")

    def test_scheduler_loops_continuously(self) -> None:
        scheduler = PlayoutScheduler(_doc(), time_fn=lambda: 100.0, loop=True)
        state = scheduler.state(now=136.0)
        self.assertEqual(state["cycle"], 1)
        self.assertEqual(state["active_item"]["source"], "a.mp4")
        self.assertEqual(state["next_item"]["source"], "b.mp4")

    def test_non_looping_scheduler_stops_on_last_item(self) -> None:
        scheduler = PlayoutScheduler(_doc(), time_fn=lambda: 100.0, loop=False)
        state = scheduler.state(now=200.0)
        self.assertEqual(state["active_item"]["source"], "weather")
        self.assertIsNone(state["next_item"])
        self.assertIsNone(state["next_index"])

    def test_state_includes_diagnostics_friendly_timing(self) -> None:
        scheduler = PlayoutScheduler(_doc(), time_fn=lambda: 100.0)
        state = scheduler.state(now=112.5)
        self.assertAlmostEqual(state["item_elapsed_seconds"], 2.5)
        self.assertAlmostEqual(state["item_remaining_seconds"], 2.5)
        self.assertEqual(state["document_duration_seconds"], 35.0)


if __name__ == "__main__":
    unittest.main()
