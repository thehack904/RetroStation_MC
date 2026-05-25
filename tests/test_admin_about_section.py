from __future__ import annotations

import re
import unittest
from pathlib import Path


class AdminAboutSectionTests(unittest.TestCase):
    def test_about_tab_includes_version_information(self) -> None:
        template = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
        html = template.read_text(encoding="utf-8")

        self.assertRegex(html, r'<button class="tab-btn" data-tab="about">About</button>')
        self.assertRegex(
            html,
            r'<div class="tab-panel" id="tab-about">[\s\S]*?RetroStation MC[\s\S]*?v1\.1\.0[\s\S]*?RetroIPTVGuide',
        )


if __name__ == "__main__":
    unittest.main()
