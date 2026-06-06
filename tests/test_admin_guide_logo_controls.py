from __future__ import annotations

import re
import unittest
from pathlib import Path


class AdminGuideLogoControlsTests(unittest.TestCase):
    def test_guide_logo_controls_exist_in_admin_template(self) -> None:
        template = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
        html = template.read_text(encoding="utf-8")

        self.assertRegex(html, r'<label class="field-label">Guide Icon \(M3U\)</label>')
        self.assertRegex(html, r'<option value="default"')
        self.assertRegex(html, r'<option value="custom"')
        self.assertRegex(html, r'<option value="disabled"')
        self.assertRegex(html, r'action="\{\{ url_for\(\'guide_logo_upload\'\) \}\}"')
        self.assertRegex(html, r'name="guide_logo_file"')
        self.assertRegex(html, r'data-tab="standby-pattern"')
        self.assertRegex(html, r'action="\{\{ url_for\(\'standby_pattern_upload\'\) \}\}"')
        self.assertRegex(html, r'action="\{\{ url_for\(\'standby_pattern_select\'\) \}\}"')
        self.assertRegex(html, r'action="\{\{ url_for\(\'standby_pattern_settings\'\) \}\}"')
        self.assertRegex(html, r'action="\{\{ url_for\(\'standby_pattern_remove\'\) \}\}"')
        self.assertRegex(html, r'name="standby_pattern_file"')
        self.assertRegex(html, r'name="standby_overlay_enabled"')
        self.assertRegex(html, r'name="standby_overlay_opacity"')
        self.assertRegex(html, r'Overlay opacity')
        self.assertRegex(html, r'Lower % = more transparent, higher % = less transparent')
        self.assertRegex(html, r'name="off_air_static_enabled"')
        self.assertRegex(html, r'Play static noise while off-air')


if __name__ == "__main__":
    unittest.main()
