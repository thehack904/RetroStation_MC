from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bump_version


class BumpVersionScriptTests(unittest.TestCase):
    def test_normalize_version_accepts_prefix_and_plain(self) -> None:
        base, v = bump_version.normalize_version("1.1.0")
        self.assertEqual(base, "1.1.0")
        self.assertEqual(v, "v1.1.0")

        base2, v2 = bump_version.normalize_version("v2.3.4")
        self.assertEqual(base2, "2.3.4")
        self.assertEqual(v2, "v2.3.4")

    def test_normalize_version_rejects_invalid_values(self) -> None:
        with self.assertRaises(SystemExit):
            bump_version.normalize_version("1.1")

    def test_main_updates_target_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "README.md"
            changelog = root / "CHANGELOG.md"
            index_template = root / "app" / "templates" / "index.html"
            admin_about_test = root / "tests" / "test_admin_about_section.py"

            index_template.parent.mkdir(parents=True)
            admin_about_test.parent.mkdir(parents=True)

            readme.write_text(
                "\n".join(
                    [
                        '<img src="https://img.shields.io/badge/version-v1.0.0-blue?style=for-the-badge">',
                        "This repository is versioned as **v1.0.0**.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            changelog.write_text("# Changelog\n\n---\n\n## [v1.0.0] - 2026-05-23\n", encoding="utf-8")
            index_template.write_text(
                '<div class="info-row"><span class="info-label">Version</span><code>v1.0.0</code></div>\n',
                encoding="utf-8",
            )
            admin_about_test.write_text(
                """self.assertRegex(
    html,
    r'<div class="tab-panel" id="tab-about">[\\s\\S]*?RetroStation MC[\\s\\S]*?v1\\.0\\.0[\\s\\S]*?RetroIPTVGuide',
)
""",
                encoding="utf-8",
            )

            with (
                patch.object(bump_version, "README_FILE", readme),
                patch.object(bump_version, "CHANGELOG_FILE", changelog),
                patch.object(bump_version, "INDEX_TEMPLATE_FILE", index_template),
                patch.object(bump_version, "ADMIN_ABOUT_TEST_FILE", admin_about_test),
            ):
                bump_version.main(["bump_version.py", "1.1.0", "--date", "2026-05-25"])

            self.assertIn("version-v1.1.0-blue", readme.read_text(encoding="utf-8"))
            self.assertIn("**v1.1.0**", readme.read_text(encoding="utf-8"))

            root_changelog = changelog.read_text(encoding="utf-8")
            self.assertIn("## [v1.1.0] - 2026-05-25", root_changelog)
            self.assertIn("### Fixed", root_changelog)
            self.assertIn("### Security", root_changelog)

            self.assertIn("<code>v1.1.0</code>", index_template.read_text(encoding="utf-8"))
            self.assertIn(r"v1\.1\.0", admin_about_test.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
