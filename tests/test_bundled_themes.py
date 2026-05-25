from __future__ import annotations

import json
import unittest
from pathlib import Path


class BundledThemesTests(unittest.TestCase):
    def test_themes_have_display_names(self) -> None:
        themes_root = Path(__file__).resolve().parents[1] / "app" / "themes"

        for theme_dir, expected_name in (("classic_cable", "Classic Cable"), ("ersatztv", "Icon Guide")):
            with self.subTest(theme=theme_dir):
                theme_path = themes_root / theme_dir / "theme.json"
                theme = json.loads(theme_path.read_text(encoding="utf-8"))
                self.assertEqual(theme["name"], expected_name)

    def test_retrostation_mc_theme_matches_brand_palette(self) -> None:
        theme_path = Path(__file__).resolve().parents[1] / "app" / "themes" / "retrostation_mc" / "theme.json"
        theme = json.loads(theme_path.read_text(encoding="utf-8"))

        self.assertEqual(theme["name"], "RetroStation MC")
        self.assertEqual(
            theme["colors"],
            {
                "background": "#05070F",
                "header_bg": "#0FCCFC",
                "header_text": "#05070F",
                "footer_bg": "#8E16D4",
                "footer_text": "#C6C3E2",
                "channel_bg": "#0C0E29",
                "channel_text": "#C6C3E2",
                "grid_line": "#223463",
                "time_text": "#5AC4F3",
                "program_bg": "#0C0E29",
                "program_outline": "#EC32E6",
                "program_text": "#C6C3E2",
                "now_line": "#0FCCFC",
                "now_line_glow": "#5AC4F3",
                "now_line_shadow": "#682175",
            },
        )
        self.assertEqual(
            theme["layout"],
            {
                "header_height": 88,
                "footer_height": 42,
                "channel_column_width": 250,
                "row_height": 68,
            },
        )

    def test_theme_docs_list_retrostation_mc_as_bundled_theme(self) -> None:
        docs_path = Path(__file__).resolve().parents[1] / "docs" / "THEMES.md"
        docs = docs_path.read_text(encoding="utf-8")

        self.assertIn("- `retrostation_mc`", docs)
        self.assertIn("- `classic_cable`", docs)
        self.assertIn("- `ersatztv`", docs)

    def test_theme_select_uses_theme_labels(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
        template = template_path.read_text(encoding="utf-8")
        self.assertIn("theme_labels.get(theme, theme)", template)


if __name__ == "__main__":
    unittest.main()
