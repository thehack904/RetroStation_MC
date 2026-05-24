from __future__ import annotations

import unittest

from app.hls_playlist import trim_playlist_for_delayed_live_edge


def _playlist_with_segments(count: int) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:6",
        "#EXT-X-MEDIA-SEQUENCE:100",
    ]
    for idx in range(count):
        lines.extend(
            [
                f"#EXT-X-PROGRAM-DATE-TIME:2024-01-01T00:00:{idx:02d}Z",
                "#EXTINF:6.000,",
                f"guide_{idx}.ts",
            ]
        )
    return "\n".join(lines) + "\n"


def _count_visible_segments(playlist_text: str) -> int:
    return sum(1 for line in playlist_text.splitlines() if line.strip().endswith(".ts"))


class DelayedLiveEdgeTrimTests(unittest.TestCase):
    def test_delayed_edge_trimming_keeps_at_least_three_segments(self) -> None:
        playlist = _playlist_with_segments(5)
        trimmed = trim_playlist_for_delayed_live_edge(playlist, delay_segments=4)
        self.assertGreaterEqual(_count_visible_segments(trimmed), 3)

    def test_delay_two_on_hls_list_size_eight_keeps_playable_window(self) -> None:
        playlist = _playlist_with_segments(8)
        trimmed = trim_playlist_for_delayed_live_edge(playlist, delay_segments=2)
        self.assertEqual(_count_visible_segments(trimmed), 6)

    def test_excessive_delay_segments_is_safely_clamped(self) -> None:
        playlist = _playlist_with_segments(8)
        trimmed = trim_playlist_for_delayed_live_edge(playlist, delay_segments=100)
        self.assertEqual(_count_visible_segments(trimmed), 3)


if __name__ == "__main__":
    unittest.main()
