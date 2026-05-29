from __future__ import annotations

import unittest
from pathlib import Path


class LinuxInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.installer = (self.repo_root / "install-linux.sh").read_text(encoding="utf-8")
        self.uninstaller = (self.repo_root / "uninstall-linux.sh").read_text(encoding="utf-8")

    def test_linux_installer_contains_required_setup_commands(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Linux" ]]; then', self.installer)
        self.assertIn('APP_USER="iptv"', self.installer)
        self.assertIn('APP_DIR="$APP_HOME/retrostation-mc"', self.installer)
        self.assertIn('SERVICE_NAME="retrostation-mc"', self.installer)
        self.assertIn('SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"', self.installer)
        self.assertIn("run_as_app_user python3 -m venv \"$APP_DIR/.venv\"", self.installer)
        self.assertIn("run_as_app_user \"$APP_DIR/.venv/bin/pip\" install -r \"$APP_DIR/requirements.txt\"", self.installer)
        self.assertIn("systemctl enable --now \"$SERVICE_NAME\"", self.installer)

    def test_linux_installer_checks_ffmpeg_dependency(self) -> None:
        self.assertIn("if ! command -v ffmpeg >/dev/null 2>&1; then", self.installer)
        self.assertIn("Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg", self.installer)
        self.assertIn("Fedora:        sudo dnf install -y ffmpeg", self.installer)
        self.assertIn("Arch:          sudo pacman -S ffmpeg", self.installer)

    def test_linux_uninstaller_removes_iptv_install_and_user(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Linux" ]]; then', self.uninstaller)
        self.assertIn('APP_USER="iptv"', self.uninstaller)
        self.assertIn('APP_DIR="$APP_HOME/retrostation-mc"', self.uninstaller)
        self.assertIn('SERVICE_NAME="retrostation-mc"', self.uninstaller)
        self.assertIn('SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"', self.uninstaller)
        self.assertIn("systemctl stop \"$SERVICE_NAME\"", self.uninstaller)
        self.assertIn("systemctl disable \"$SERVICE_NAME\"", self.uninstaller)
        self.assertIn("rm -f \"$SYSTEMD_FILE\"", self.uninstaller)
        self.assertIn("rm -rf \"$APP_DIR\"", self.uninstaller)
        self.assertIn("userdel \"$APP_USER\"", self.uninstaller)

    def test_linux_uninstaller_preserves_shared_iptv_user_when_home_not_empty(self) -> None:
        import re

        self.assertIn('PRESERVE_APP_USER=true', self.uninstaller)
        self.assertIn('PRESERVE_APP_USER=false', self.uninstaller)
        self.assertIn('if [[ "$PRESERVE_APP_USER" == "true" ]]; then', self.uninstaller)
        self.assertIn('echo "Preserved user/group: $APP_USER"', self.uninstaller)
        self.assertRegex(
            self.uninstaller,
            re.compile(
                r'if \[\[ "\$PRESERVE_APP_USER" == "true" \]\]; then\s+'
                r'echo "Preserved user/group: \$APP_USER"\s+'
                r'else\s+'
                r'pkill -u "\$APP_USER".*?userdel "\$APP_USER".*?groupdel "\$APP_USER"',
                re.DOTALL,
            ),
        )


if __name__ == "__main__":
    unittest.main()
