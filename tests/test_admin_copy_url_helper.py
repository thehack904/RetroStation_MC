from __future__ import annotations

import re
import unittest
from pathlib import Path


class AdminCopyUrlHelperTests(unittest.TestCase):
    def test_copy_helper_includes_clipboard_and_fallback_paths(self) -> None:
        template = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
        html = template.read_text(encoding="utf-8")
        helper_match = re.search(
            r"<!-- Copy URL helper -->\s*<script>(?P<body>.*?)</script>\s*<!-- Music controls -->",
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(helper_match, "Copy URL helper script block is missing")
        helper_block = helper_match.group("body") if helper_match else ""
        self.assertRegex(helper_block, r"navigator\.clipboard\s*&&\s*window\.isSecureContext")
        self.assertRegex(helper_block, r"navigator\.clipboard\.writeText\(el\.value\)")
        self.assertRegex(helper_block, r"document\.execCommand\s*&&\s*document\.execCommand\('copy'\)")
        self.assertRegex(helper_block, r"fallbackCopy\(\)")

    def test_stream_output_copy_buttons_use_copy_helper(self) -> None:
        template = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
        html = template.read_text(encoding="utf-8")
        self.assertRegex(html, r'onclick="copyUrl\(\'url-hls\', this\)"')
        self.assertRegex(html, r'onclick="copyUrl\(\'url-m3u\', this\)"')
        self.assertRegex(html, r'onclick="copyUrl\(\'url-xmltv\', this\)"')


if __name__ == "__main__":
    unittest.main()
