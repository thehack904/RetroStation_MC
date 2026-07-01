from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.m3u_parser import parse_extinf, parse_m3u


class M3UParserTests(unittest.TestCase):
    def test_parse_extinf_ignores_commas_inside_quoted_attributes(self) -> None:
        parsed = parse_extinf(
            '#EXTINF:-1 tvg-id="channel-2" group-title="Movies, Kids" tvg-chno="2",Channel 2'
        )

        self.assertEqual(parsed["display_name"], "Channel 2")
        self.assertEqual(parsed["group-title"], "Movies, Kids")

    def test_parse_m3u_uses_display_name_when_tvg_name_missing(self) -> None:
        playlist = """#EXTM3U
#EXTINF:-1 tvg-id="channel-2" group-title="Movies, Kids" tvg-chno="2",Channel 2
http://example.invalid/stream2
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            playlist_path = Path(tmp_dir) / "channels.m3u"
            playlist_path.write_text(playlist, encoding="utf-8")

            channels = parse_m3u(str(playlist_path))

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["name"], "Channel 2")
        self.assertEqual(channels[0]["id"], "channel-2")
        self.assertEqual(channels[0]["group"], "Movies, Kids")


if __name__ == "__main__":
    unittest.main()
