from __future__ import annotations

import unittest
from urllib.error import URLError
from unittest.mock import Mock, patch

from app.manager import GuideManager


class RefreshLoggingContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Mock()
        self.store.get_config.return_value = {
            "playlist_source": "http://invalid-playlist-host.local/channels.m3u",
            "xmltv_source": "http://invalid-xmltv-host.local/guide.xml",
        }
        self.manager = GuideManager(self.store)
        self.manager.logger = Mock()

    def test_refresh_logs_playlist_source_on_playlist_fetch_failure(self) -> None:
        with patch("app.manager.parse_m3u", side_effect=URLError("[Errno -2] Name or service not known")):
            with self.assertRaises(URLError):
                self.manager.refresh_state()

        error_messages = [call.args[1] for call in self.manager.logger.error.call_args_list]
        self.assertTrue(any("Playlist load failed" in message for message in error_messages))
        self.assertTrue(
            any("playlist_source='http://invalid-playlist-host.local/channels.m3u'" in message for message in error_messages)
        )

    def test_refresh_logs_xmltv_source_on_xmltv_fetch_failure(self) -> None:
        channels = [
            {
                "id": "channel.1",
                "name": "Channel 1",
                "number": "1",
                "group": "",
                "logo": "",
                "stream_url": "http://example.invalid/stream",
            }
        ]
        with patch("app.manager.parse_m3u", return_value=channels), patch(
            "app.manager.parse_xmltv",
            side_effect=URLError("[Errno -2] Name or service not known"),
        ):
            with self.assertRaises(URLError):
                self.manager.refresh_state()

        error_messages = [call.args[1] for call in self.manager.logger.error.call_args_list]
        self.assertTrue(any("XMLTV load failed" in message for message in error_messages))
        self.assertTrue(
            any("xmltv_source='http://invalid-xmltv-host.local/guide.xml'" in message for message in error_messages)
        )


if __name__ == "__main__":
    unittest.main()
