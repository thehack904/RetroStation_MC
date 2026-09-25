from __future__ import annotations

import unittest
from pathlib import Path


class LegacyLayoutWarningTests(unittest.TestCase):
    def test_app_emits_warning_for_legacy_install_path(self) -> None:
        app_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('LEGACY_BASE_DIR = Path("/home/iptv/retrostation-mc")', app_source)
        self.assertIn("RetroStation MC is running from legacy path", app_source)
        self.assertIn("sudo ./retrostation_linux.sh install", app_source)

    def test_legacy_home_path_is_limited_to_migration_aware_code_files(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        legacy_path = "/home/iptv/retrostation-mc"
        allowed_hits = {"app.py", "retrostation_linux.sh"}
        actual_hits = set()

        for path in repo_root.rglob("*"):
            if not path.is_file():
                continue
            if path.parts[-2:-1] == ("tests",):
                continue
            if path.suffix not in {".py", ".sh"}:
                continue
            if legacy_path in path.read_text(encoding="utf-8"):
                actual_hits.add(path.relative_to(repo_root).as_posix())

        self.assertEqual(actual_hits, allowed_hits)


if __name__ == "__main__":
    unittest.main()
