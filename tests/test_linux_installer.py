from __future__ import annotations

import unittest
from pathlib import Path


class LinuxInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.installer = (self.repo_root / "install-linux.sh").read_text(encoding="utf-8")

    def test_linux_installer_contains_required_setup_commands(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Linux" ]]; then', self.installer)
        self.assertIn("python3 -m venv .venv", self.installer)
        self.assertIn("source .venv/bin/activate", self.installer)
        self.assertIn("pip install -r requirements.txt", self.installer)

    def test_linux_installer_checks_ffmpeg_dependency(self) -> None:
        self.assertIn("if ! command -v ffmpeg >/dev/null 2>&1; then", self.installer)
        self.assertIn("Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg", self.installer)
        self.assertIn("Fedora:        sudo dnf install -y ffmpeg", self.installer)
        self.assertIn("Arch:          sudo pacman -S ffmpeg", self.installer)


if __name__ == "__main__":
    unittest.main()
