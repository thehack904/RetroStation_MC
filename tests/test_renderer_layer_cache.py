from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from app.renderer import GuideRenderer, _resolve_program_cell_fill


def _build_state(title: str = "Guide Channel") -> dict:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = now + timedelta(minutes=90)
    return {
        "title": title,
        "display": {
            "resolution": "640x360",
            "fps": 30,
            "page_seconds": 12,
            "transition": "scroll",
        },
        "time_window": {
            "start": now.isoformat(),
            "end": end.isoformat(),
        },
        "theme_data": {
            "colors": {
                "background": "#08101d",
                "header_bg": "#173361",
                "header_text": "#ffffff",
                "footer_bg": "#102040",
                "footer_text": "#d9e6ff",
                "channel_bg": "#0d1930",
                "channel_text": "#ffffff",
                "grid_line": "#2d4a7a",
                "time_text": "#d9e6ff",
                "program_bg": "#21406b",
                "program_outline": "#7db2ff",
                "program_text": "#ffffff",
                "now_line": "#ffd166",
            },
            "layout": {
                "header_height": 64,
                "footer_height": 30,
                "channel_column_width": 170,
                "row_height": 52,
            },
        },
        "pages": [
            [
                {
                    "number": "1",
                    "name": "Retro News",
                    "programs": [
                        {
                            "title": "Morning Retro",
                            "start": now.isoformat(),
                            "stop": (now + timedelta(minutes=30)).isoformat(),
                        },
                        {
                            "title": "Noon Replay",
                            "start": (now + timedelta(minutes=30)).isoformat(),
                            "stop": end.isoformat(),
                        },
                    ],
                }
            ]
        ],
    }


class GuideRendererLayerCacheTests(unittest.TestCase):
    def test_resolve_program_cell_fill_prefers_group_specific_colors(self) -> None:
        colors = {
            "program_bg": "#00135a",
            "program_bg_movies": "#7a0000",
            "program_bg_sports": "#005500",
        }
        self.assertEqual(_resolve_program_cell_fill(colors, "Movies"), "#7a0000")
        self.assertEqual(_resolve_program_cell_fill(colors, "TV & Movies"), "#7a0000")
        self.assertEqual(_resolve_program_cell_fill(colors, "Sports"), "#005500")
        self.assertEqual(_resolve_program_cell_fill(colors, "Sport"), "#005500")
        self.assertEqual(_resolve_program_cell_fill(colors, "Movie"), "#7a0000")
        self.assertEqual(_resolve_program_cell_fill(colors, "Local"), "#00135a")
        self.assertEqual(_resolve_program_cell_fill(colors, "Transportation"), "#00135a")
        self.assertEqual(_resolve_program_cell_fill(colors, "Motorsports"), "#00135a")
        self.assertEqual(_resolve_program_cell_fill(colors, "Moviemania"), "#00135a")
        self.assertEqual(_resolve_program_cell_fill(colors, None), "#00135a")

    def test_resolve_program_cell_fill_falls_back_to_default_when_program_bg_missing(self) -> None:
        colors = {}
        self.assertEqual(_resolve_program_cell_fill(colors, "Local"), "#21406b")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "guide_state.json"
        self.state_path.write_text(json.dumps(_build_state()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_static_content_layer_is_cached_for_same_page(self) -> None:
        renderer = GuideRenderer(self.state_path)
        renderer.reload_if_needed()
        display = renderer.state["display"]
        width, height = [int(x) for x in display["resolution"].split("x", 1)]
        layout = renderer.state["theme_data"]["layout"]
        content_h = height - int(layout["header_height"]) - int(layout["footer_height"])
        page = renderer.state["pages"][0]
        time_window = renderer.state["time_window"]
        start_dt = datetime.fromisoformat(time_window["start"])
        end_dt = datetime.fromisoformat(time_window["end"])
        colors = renderer.state["theme_data"]["colors"]

        first = renderer._get_static_content_layer(width, content_h, 0, page, colors, layout, start_dt, end_dt)
        second = renderer._get_static_content_layer(width, content_h, 0, page, colors, layout, start_dt, end_dt)
        self.assertIs(first, second)

    def test_cache_invalidates_when_state_changes(self) -> None:
        renderer = GuideRenderer(self.state_path)
        renderer.reload_if_needed()
        generation_before = renderer._cache_generation
        frame_before = renderer.draw_frame(epoch_time=time.time())

        time.sleep(0.6)
        self.state_path.write_text(json.dumps(_build_state(title="Updated Guide")), encoding="utf-8")
        future = time.time() + 1.0
        os.utime(self.state_path, (future, future))
        renderer._last_mtime_check = 0.0
        frame_after = renderer.draw_frame(epoch_time=time.time())

        self.assertGreater(renderer._cache_generation, generation_before)
        self.assertNotEqual(frame_before.tobytes(), frame_after.tobytes())

    def test_display_programs_merge_casefolded_titles_with_small_gap(self) -> None:
        renderer = GuideRenderer(self.state_path)
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        programs = [
            {"title": "  Psych  ", "start": start.isoformat(), "stop": (start + timedelta(minutes=15)).isoformat()},
            {"title": "psych", "start": (start + timedelta(minutes=15, seconds=30)).isoformat(), "stop": (start + timedelta(minutes=30)).isoformat()},
        ]
        merged = renderer._build_display_programs(programs, merge_gap_seconds=90.0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "Psych")
        self.assertEqual(merged[0]["stop"], datetime.fromisoformat(programs[1]["stop"]))
        self.assertEqual(programs[0]["title"], "  Psych  ")

    def test_program_text_thresholds_hide_tiny_cells_and_ellipsis_long_titles(self) -> None:
        renderer = GuideRenderer(self.state_path)
        canvas = Image.new("RGB", (320, 120), "#000000")
        draw = ImageDraw.Draw(canvas)
        long_title = "Harry Potter and the Order of the Phoenix Extended Cut"

        tiny = renderer._program_text_for_cell(draw, long_title, max_text_width=60, cell_width=30)
        medium = renderer._program_text_for_cell(draw, long_title, max_text_width=60, cell_width=60)
        wide = renderer._program_text_for_cell(draw, long_title, max_text_width=160, cell_width=120)

        self.assertIsNone(tiny)
        self.assertIsNotNone(medium)
        self.assertIsNotNone(wide)
        self.assertLessEqual(renderer._text_width(draw, medium or "", renderer.font_medium), 45)
        self.assertLessEqual(renderer._text_width(draw, wide or "", renderer.font_medium), 160)
        self.assertTrue((wide or "").endswith("…"))

    def test_edge_program_cells_are_clipped_to_timeline_bounds(self) -> None:
        renderer = GuideRenderer(self.state_path)
        renderer.reload_if_needed()
        display = renderer.state["display"]
        width, height = [int(x) for x in display["resolution"].split("x", 1)]
        layout = renderer.state["theme_data"]["layout"]
        colors = renderer.state["theme_data"]["colors"]
        content_h = height - int(layout["header_height"]) - int(layout["footer_height"])
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        end = now + timedelta(minutes=90)
        page = [
            {
                "number": "2.1",
                "name": "Edge Cases",
                "programs": [
                    {"title": "Looney Tunes", "start": (now - timedelta(minutes=30)).isoformat(), "stop": (now + timedelta(minutes=10)).isoformat()},
                    {"title": "Gaming Historian", "start": (end - timedelta(minutes=10)).isoformat(), "stop": (end + timedelta(minutes=30)).isoformat()},
                ],
            }
        ]
        static_layer = renderer._render_content_static(width, content_h, page, colors, layout, now, end)
        timeline_x0, timeline_x1 = renderer._timeline_bounds(width, layout, (end - now).total_seconds())
        row_mid_y = 40 + int(layout["row_height"]) // 2

        channel_bg = tuple(int(colors["channel_bg"][i : i + 2], 16) for i in (1, 3, 5))
        program_bg = tuple(int(colors["program_bg"][i : i + 2], 16) for i in (1, 3, 5))
        self.assertEqual(static_layer.getpixel((timeline_x0 + 8, row_mid_y)), program_bg)
        self.assertEqual(static_layer.getpixel((timeline_x1 - 8, row_mid_y)), program_bg)
        self.assertEqual(static_layer.getpixel((timeline_x0 - 8, row_mid_y)), channel_bg)


if __name__ == "__main__":
    unittest.main()
