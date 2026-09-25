from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


class LinuxInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        unified = (self.repo_root / "retrostation_linux.sh").read_text(encoding="utf-8")
        self.installer = unified
        self.uninstaller = unified

    def _installer_prelude(self) -> str:
        return self.installer[self.installer.index('APP_USER='):self.installer.index('select_python() {')]

    def _script_body(self) -> str:
        return self.installer[self.installer.index('APP_USER='):self.installer.index('case "$COMMAND" in')]

    def test_linux_installer_contains_required_setup_commands(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Linux" ]]; then', self.installer)
        self.assertIn('APP_USER="retrostation-mc"', self.installer)
        self.assertIn('APP_GROUP="$APP_USER"', self.installer)
        self.assertIn('APP_DIR="/opt/retrostation-mc"', self.installer)
        self.assertIn('CONFIG_DIR="/etc/retrostation-mc"', self.installer)
        self.assertIn('STATE_DIR="/var/lib/retrostation-mc"', self.installer)
        self.assertIn('SERVICE_NAME="retrostation-mc"', self.installer)
        self.assertIn('SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"', self.installer)
        self.assertIn('CONFIG_ENV_FILE="$CONFIG_DIR/${SERVICE_NAME}.conf"', self.installer)
        self.assertIn('LEGACY_APP_DIR="/home/iptv/retrostation-mc"', self.installer)
        self.assertIn('MIGRATION_VERSION="linux-layout-v1.5.0"', self.installer)
        self.assertIn('MIGRATION_DONE_FILE="$MIGRATION_STATE_DIR/${MIGRATION_VERSION}.done"', self.installer)
        self.assertIn('useradd -r -M -d "$STATE_DIR" -s "$no_login_shell" -g "$APP_GROUP" "$APP_USER"', self.installer)
        self.assertIn('Existing user $APP_USER is not managed by this installer; refusing to modify it.', self.installer)
        self.assertIn('"$PYTHON_BIN" -m venv "$APP_DIR/.venv"', self.installer)
        self.assertIn('"$APP_DIR/.venv/bin/python" -m pip install --only-binary=:all: -r "$APP_DIR/requirements.txt"', self.installer)
        self.assertIn("run_as_app_user \"$APP_DIR/.venv/bin/python\" \"$APP_DIR/gpu_hwaccel_detect_v3.py\"", self.installer)
        self.assertIn("Warning: GPU hardware acceleration diagnostics failed; continuing with software fallback.", self.installer)
        self.assertIn("systemctl enable --now \"$SERVICE_NAME\"", self.installer)
        self.assertIn('EnvironmentFile=-$CONFIG_ENV_FILE', self.installer)
        self.assertIn('ReadWritePaths=$STATE_DIR', self.installer)
        self.assertIn('NoNewPrivileges=yes', self.installer)
        self.assertIn('PrivateTmp=yes', self.installer)
        self.assertIn('ProtectHome=yes', self.installer)
        self.assertIn('ProtectKernelTunables=yes', self.installer)
        self.assertIn('ProtectKernelModules=yes', self.installer)
        self.assertIn('ProtectControlGroups=yes', self.installer)

    def test_linux_installer_links_mutable_roots_into_var_lib(self) -> None:
        self.assertIn('MUTABLE_DIRS=(', self.installer)
        self.assertIn('"data"', self.installer)
        self.assertIn('"output"', self.installer)
        self.assertIn('"runtime"', self.installer)
        self.assertIn('MUTABLE_FILES=()', self.installer)
        self.assertIn('link_state_directory "$relative_path"', self.installer)
        self.assertIn('cp -a --update=none "$source_path"/. "$state_path"/', self.installer)
        # File-level links are unsafe for state files that RSMC unlinks/replaces:
        # unlinking the symlink itself would require write permission under /opt.
        self.assertNotIn('"data/guide_state_secondary.json"', self.installer)

    def test_linux_installer_selects_only_validated_python_versions(self) -> None:
        self.assertIn('MIN_PYTHON_MINOR=11', self.installer)
        self.assertIn('MAX_PYTHON_MINOR=14', self.installer)
        self.assertIn('select_python()', self.installer)
        self.assertIn('for minor in $(seq "$MAX_PYTHON_MINOR" -1 "$MIN_PYTHON_MINOR")', self.installer)
        self.assertIn('requires a validated Python 3.${MIN_PYTHON_MINOR} through 3.${MAX_PYTHON_MINOR}', self.installer)
        self.assertIn('Using Python interpreter:', self.installer)

    def test_linux_installer_avoids_dependency_source_builds(self) -> None:
        self.assertIn('--only-binary=:all:', self.installer)

    def test_linux_installer_checks_ffmpeg_dependency(self) -> None:
        self.assertIn("if ! command -v ffmpeg >/dev/null 2>&1; then", self.installer)
        self.assertIn("Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg", self.installer)
        self.assertIn("Fedora:        sudo dnf install -y ffmpeg", self.installer)
        self.assertIn("Arch:          sudo pacman -S ffmpeg", self.installer)

    def test_linux_installer_refuses_unverified_systemd_unit_replacement(self) -> None:
        self.assertIn('UNIT_MARKER="# Managed by RetroStation MC installer"', self.installer)
        self.assertIn('unit_belongs_to_project()', self.installer)
        self.assertIn('grep -Fq "Description=RetroStation MC" "$SYSTEMD_FILE"', self.installer)
        self.assertRegex(self.installer, re.compile(r"ExecStart=/home/iptv/retrostation-mc/\\\.venv/bin/python /home/iptv/retrostation-mc/app\\\.py"))
        self.assertRegex(self.installer, re.compile(r"ExecStart=/opt/retrostation-mc/\\\.venv/bin/python /opt/retrostation-mc/app\\\.py"))
        self.assertIn("Refusing to overwrite unverified systemd unit", self.installer)
        self.assertIn('assert_safe_to_manage_systemd_unit', self.installer)

    def test_linux_uninstaller_preserves_config_and_state_without_purge(self) -> None:
        self.assertIn('if [[ "$(uname -s)" != "Linux" ]]; then', self.uninstaller)
        self.assertIn('APP_USER="retrostation-mc"', self.uninstaller)
        self.assertIn('APP_DIR="/opt/retrostation-mc"', self.uninstaller)
        self.assertIn('CONFIG_DIR="/etc/retrostation-mc"', self.uninstaller)
        self.assertIn('STATE_DIR="/var/lib/retrostation-mc"', self.uninstaller)
        self.assertIn('SERVICE_NAME="retrostation-mc"', self.uninstaller)
        self.assertIn('SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"', self.uninstaller)
        self.assertIn("systemctl stop \"$SERVICE_NAME\"", self.uninstaller)
        self.assertIn("systemctl disable \"$SERVICE_NAME\"", self.uninstaller)
        self.assertIn("rm -f \"$SYSTEMD_FILE\"", self.uninstaller)
        self.assertIn("rm -rf \"$APP_DIR\"", self.uninstaller)
        self.assertIn('if [[ "${1:-}" == "--purge" && $# -eq 1 ]]; then', self.uninstaller)
        self.assertIn('record_retained_path "$CONFIG_DIR"', self.uninstaller)
        self.assertIn('record_retained_path "$STATE_DIR"', self.uninstaller)
        self.assertIn("rm -rf \"$CONFIG_DIR\"", self.uninstaller)
        self.assertIn("rm -rf \"$STATE_DIR\"", self.uninstaller)
        self.assertIn('project_user_artifacts_remain', self.uninstaller)
        self.assertIn("userdel \"$APP_USER\"", self.uninstaller)
        self.assertIn("groupdel \"$APP_GROUP\"", self.uninstaller)
        self.assertIn('print_path_summary "Removed paths:"', self.uninstaller)
        self.assertIn('print_path_summary "Retained paths:"', self.uninstaller)

    def test_linux_installer_uses_exact_service_operations_only(self) -> None:
        self.assertNotIn("pkill", self.installer)
        self.assertNotIn("killall", self.installer)
        self.assertNotIn("pkill", self.uninstaller)
        self.assertNotIn("killall", self.uninstaller)
        self.assertNotIn("systemctl kill", self.uninstaller)
        self.assertNotIn("userdel -r iptv", self.uninstaller)
        self.assertNotIn("groupdel iptv", self.uninstaller)
        self.assertNotIn("pkill -u iptv", self.uninstaller)
        self.assertIn('systemctl stop "$SERVICE_NAME"', self.installer)
        self.assertIn('systemctl stop "$SERVICE_NAME"', self.uninstaller)

    def test_linux_installer_preserves_updated_seed_files_when_state_exists(self) -> None:
        self.assertIn('local preserved_path="${state_path}.install-default"', self.installer)
        self.assertIn('if [[ -s "$source_path" ]] && ! cmp -s "$source_path" "$state_path"; then', self.installer)
        self.assertIn('cp -f "$source_path" "$preserved_path"', self.installer)

    def test_linux_installer_migrates_legacy_layout_with_backup_and_marker(self) -> None:
        self.assertIn('run_legacy_migration()', self.installer)
        self.assertIn('backup_legacy_installation', self.installer)
        self.assertIn('Legacy backup saved to:', self.installer)
        self.assertIn('for relative_path in data output runtime; do', self.installer)
        self.assertIn('copy_tree_preserve_no_clobber "$LEGACY_APP_DIR/$relative_path" "$STATE_DIR/$relative_path"', self.installer)
        self.assertIn('touch "$MIGRATION_DONE_FILE"', self.installer)
        self.assertIn('Legacy migration marker written: $MIGRATION_DONE_FILE', self.installer)
        self.assertIn('validate_migrated_installation', self.installer)
        self.assertIn('run_as_app_user "$APP_DIR/.venv/bin/python" -c "import app.config_store"', self.installer)

    def test_install_config_defaults_writes_expected_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config_dir = tmp_path / "etc" / "retrostation-mc"
            state_dir = tmp_path / "var" / "lib" / "retrostation-mc"
            config_env_file = config_dir / "retrostation-mc.conf"
            config_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)

            harness = f"""set -euo pipefail
{self._installer_prelude()}
CONFIG_DIR="{config_dir}"
STATE_DIR="{state_dir}"
CONFIG_ENV_FILE="{config_env_file}"
chown() {{
  :
}}
chmod() {{
  :
}}
install_config_defaults
grep -Fx "# RetroStation MC service environment" "$CONFIG_ENV_FILE"
grep -Fx "HOME=$STATE_DIR" "$CONFIG_ENV_FILE"
grep -Fx "RETROSTATION_MC_CONFIG_DIR=$CONFIG_DIR" "$CONFIG_ENV_FILE"
grep -Fx "RETROSTATION_MC_STATE_DIR=$STATE_DIR" "$CONFIG_ENV_FILE"
grep -Fx "PYTHONUNBUFFERED=1" "$CONFIG_ENV_FILE"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_install_service_writes_unit_for_dedicated_account_and_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            app_dir = tmp_path / "opt" / "retrostation-mc"
            config_dir = tmp_path / "etc" / "retrostation-mc"
            state_dir = tmp_path / "var" / "lib" / "retrostation-mc"
            systemd_file = tmp_path / "etc" / "systemd" / "system" / "retrostation-mc.service"
            config_env_file = config_dir / "retrostation-mc.conf"
            log_file = tmp_path / "commands.log"
            systemd_file.parent.mkdir(parents=True)

            harness = f"""set -euo pipefail
{self._script_body()}
APP_DIR="{app_dir}"
CONFIG_DIR="{config_dir}"
STATE_DIR="{state_dir}"
SYSTEMD_FILE="{systemd_file}"
CONFIG_ENV_FILE="{config_env_file}"
LOG_FILE="{log_file}"
systemctl() {{
  printf '%s\\n' "systemctl:$*" >>"$LOG_FILE"
}}
install_service
grep -Fx "# Managed by RetroStation MC installer" "$SYSTEMD_FILE"
grep -Fx "User=$APP_USER" "$SYSTEMD_FILE"
grep -Fx "Group=$APP_GROUP" "$SYSTEMD_FILE"
grep -Fx "WorkingDirectory=$APP_DIR" "$SYSTEMD_FILE"
grep -Fx "EnvironmentFile=-$CONFIG_ENV_FILE" "$SYSTEMD_FILE"
grep -Fx "ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app.py" "$SYSTEMD_FILE"
grep -Fx "ProtectSystem=full" "$SYSTEMD_FILE"
grep -Fx "NoNewPrivileges=yes" "$SYSTEMD_FILE"
grep -Fx "PrivateTmp=yes" "$SYSTEMD_FILE"
grep -Fx "ProtectHome=yes" "$SYSTEMD_FILE"
grep -Fx "ProtectKernelTunables=yes" "$SYSTEMD_FILE"
grep -Fx "ProtectKernelModules=yes" "$SYSTEMD_FILE"
grep -Fx "ProtectControlGroups=yes" "$SYSTEMD_FILE"
grep -Fx "ReadWritePaths=$STATE_DIR" "$SYSTEMD_FILE"
grep -Fx "systemctl:daemon-reload" "$LOG_FILE"
grep -Fx "systemctl:enable --now $SERVICE_NAME" "$LOG_FILE"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_restartable_legacy_migration_reuses_backup_and_writes_rollback_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            legacy_app_dir = tmp_path / "home" / "iptv" / "retrostation-mc"
            config_dir = tmp_path / "etc" / "retrostation-mc"
            state_dir = tmp_path / "var" / "lib" / "retrostation-mc"
            systemd_file = tmp_path / "etc" / "systemd" / "system" / "retrostation-mc.service"
            legacy_service_env = tmp_path / "etc" / "default" / "retrostation-mc"
            backup_root = tmp_path / "var" / "backups" / "retrostation-mc"
            legacy_app_dir.mkdir(parents=True)
            (legacy_app_dir / "data").mkdir()
            (legacy_app_dir / "output").mkdir()
            (legacy_app_dir / "runtime").mkdir()
            (legacy_app_dir / "app.py").write_text("print('legacy')\n", encoding="utf-8")
            (legacy_app_dir / ".env").write_text("LEGACY=1\n", encoding="utf-8")
            (legacy_app_dir / "data" / "config.db").write_text("legacy-db", encoding="utf-8")
            (legacy_app_dir / "output" / "guide.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (legacy_app_dir / "runtime" / "renderer.pid").write_text("42\n", encoding="utf-8")
            legacy_service_env.parent.mkdir(parents=True)
            legacy_service_env.write_text("LEGACY_ENV=1\n", encoding="utf-8")
            systemd_file.parent.mkdir(parents=True)
            systemd_file.write_text(
                "\n".join(
                    [
                        "# Managed by RetroStation MC installer",
                        "[Unit]",
                        "Description=RetroStation MC",
                        "[Service]",
                        f"ExecStart={legacy_app_dir}/.venv/bin/python {legacy_app_dir}/app.py",
                    ]
                ),
                encoding="utf-8",
            )

            harness = f"""set -euo pipefail
{self._installer_prelude()}
LEGACY_APP_DIR="{legacy_app_dir}"
LEGACY_SERVICE_ENV_FILES=("{legacy_service_env}")
LEGACY_CONFIG_FILES=(".env")
CONFIG_DIR="{config_dir}"
STATE_DIR="{state_dir}"
SYSTEMD_FILE="{systemd_file}"
BACKUP_ROOT="{backup_root}"
MIGRATION_STATE_DIR="$STATE_DIR/.migration"
MIGRATION_DONE_FILE="$MIGRATION_STATE_DIR/${{MIGRATION_VERSION}}.done"
MIGRATION_STEPS_FILE="$MIGRATION_STATE_DIR/${{MIGRATION_VERSION}}.steps"
MIGRATION_BACKUP_PATH_FILE="$MIGRATION_STATE_DIR/${{MIGRATION_VERSION}}.backup-path"
install() {{
  local args=()
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      -o|-g)
        shift 2
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done
  command install "${{args[@]}}"
}}
backup_legacy_installation
first_backup_path="$(cat "$MIGRATION_BACKUP_PATH_FILE")"
run_legacy_migration
test "$(cat "$MIGRATION_BACKUP_PATH_FILE")" = "$first_backup_path"
test "$(cat "$STATE_DIR/data/config.db")" = "legacy-db"
test -f "$CONFIG_DIR/legacy/.env"
test -f "$first_backup_path/rollback.txt"
grep -Fx "backup_complete" "$MIGRATION_STEPS_FILE"
grep -Fx "config_migrated" "$MIGRATION_STEPS_FILE"
grep -Fx "state_migrated" "$MIGRATION_STEPS_FILE"
grep -Fx "Legacy install path retained: $LEGACY_APP_DIR" "$first_backup_path/rollback.txt"
grep -Fx "2. Stop the current service: systemctl stop $SERVICE_NAME" "$first_backup_path/rollback.txt"
grep -Fx "4. Reload and restart legacy service: systemctl daemon-reload && systemctl start $SERVICE_NAME" "$first_backup_path/rollback.txt"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_linux_installer_never_targets_sibling_project_names_or_paths(self) -> None:
        for forbidden in (
            "retrostation-player",
            "RetroStation Player",
            "retroiptvguide.service",
            "/opt/retrostation-player",
            "/etc/retrostation-player",
            "/var/lib/retrostation-player",
            "/home/iptv/retrostation-player",
        ):
            self.assertNotIn(forbidden, self.installer)
            self.assertNotIn(forbidden, self.uninstaller)

    def test_link_state_file_preserves_distinct_seed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            app_dir = tmp_path / "app"
            state_dir = tmp_path / "state"
            (app_dir / "data").mkdir(parents=True)
            (state_dir / "data").mkdir(parents=True)
            (app_dir / "data" / "weather_state.json").write_text("seed-new", encoding="utf-8")
            (state_dir / "data" / "weather_state.json").write_text("existing-state", encoding="utf-8")

            harness = f"""set -euo pipefail
{self._installer_prelude()}
APP_DIR="{app_dir}"
STATE_DIR="{state_dir}"
link_state_file "data/weather_state.json"
test -L "$APP_DIR/data/weather_state.json"
test "$(readlink "$APP_DIR/data/weather_state.json")" = "$STATE_DIR/data/weather_state.json"
test "$(cat "$STATE_DIR/data/weather_state.json")" = "existing-state"
test "$(cat "$STATE_DIR/data/weather_state.json.install-default")" = "seed-new"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_copy_tree_preserve_no_clobber_is_restart_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_dir = tmp_path / "legacy-data"
            target_dir = tmp_path / "state-data"
            source_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)
            (source_dir / "config.db").write_text("legacy-db", encoding="utf-8")
            (source_dir / "guide_state.json").write_text("legacy-guide", encoding="utf-8")
            (target_dir / "config.db").write_text("existing-db", encoding="utf-8")

            harness = f"""set -euo pipefail
{self._installer_prelude()}
copy_tree_preserve_no_clobber "{source_dir}" "{target_dir}"
test "$(cat "{target_dir}/config.db")" = "existing-db"
test "$(cat "{target_dir}/guide_state.json")" = "legacy-guide"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_default_uninstall_preserves_state_and_skips_account_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            app_dir = tmp_path / "opt" / "retrostation-mc"
            config_dir = tmp_path / "etc" / "retrostation-mc"
            state_dir = tmp_path / "var" / "lib" / "retrostation-mc"
            systemd_file = tmp_path / "etc" / "systemd" / "system" / "retrostation-mc.service"
            log_file = tmp_path / "commands.log"
            app_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            systemd_file.parent.mkdir(parents=True)
            systemd_file.write_text(
                "\n".join(
                    [
                        "# Managed by RetroStation MC installer",
                        "[Unit]",
                        "Description=RetroStation MC",
                        "[Service]",
                        f"ExecStart={app_dir}/.venv/bin/python {app_dir}/app.py",
                    ]
                ),
                encoding="utf-8",
            )

            harness = f"""set -euo pipefail
{self._script_body()}
APP_DIR="{app_dir}"
CONFIG_DIR="{config_dir}"
STATE_DIR="{state_dir}"
SYSTEMD_FILE="{systemd_file}"
LOG_FILE="{log_file}"

command() {{
  if [[ "$1" == "-v" && "$2" == "systemctl" ]]; then
    return 0
  fi
  builtin command "$@"
}}

systemctl() {{
  printf '%s\\n' "systemctl:$*" >>"$LOG_FILE"
}}

id() {{
  if [[ "$#" -eq 1 && "$1" == "$APP_USER" ]]; then
    return 0
  fi
  if [[ "$1" == "-gn" && "$2" == "$APP_USER" ]]; then
    printf '%s\\n' "$APP_GROUP"
    return 0
  fi
  return 1
}}

getent() {{
  if [[ "$1" == "passwd" && "$2" == "$APP_USER" ]]; then
    printf '%s\\n' "$APP_USER:x:999:999::${{STATE_DIR}}:/usr/sbin/nologin"
    return 0
  fi
  if [[ "$1" == "group" && "$2" == "$APP_GROUP" ]]; then
    printf '%s\\n' "$APP_GROUP:x:999:"
    return 0
  fi
  return 2
}}

pgrep() {{
  return 1
}}

find() {{
  if [[ "$1" == "$STATE_DIR" ]]; then
    printf '%s\\n' "$STATE_DIR/data/config.db"
  fi
}}

userdel() {{
  printf '%s\\n' "userdel:$*" >>"$LOG_FILE"
}}

groupdel() {{
  printf '%s\\n' "groupdel:$*" >>"$LOG_FILE"
}}

output="$(do_uninstall)"
test ! -e "$APP_DIR"
test -d "$CONFIG_DIR"
test -d "$STATE_DIR"
test ! -e "$SYSTEMD_FILE"
grep -Fx "systemctl:stop $SERVICE_NAME" "$LOG_FILE"
grep -Fx "systemctl:disable $SERVICE_NAME" "$LOG_FILE"
grep -Fx "systemctl:daemon-reload" "$LOG_FILE"
! grep -F "userdel:" "$LOG_FILE"
! grep -F "groupdel:" "$LOG_FILE"
printf '%s\\n' "$output" | grep -Fx "Removed paths:"
printf '%s\\n' "$output" | grep -Fx "  {systemd_file}"
printf '%s\\n' "$output" | grep -Fx "  {app_dir}"
printf '%s\\n' "$output" | grep -Fx "Retained paths:"
printf '%s\\n' "$output" | grep -Fx "  {config_dir}"
printf '%s\\n' "$output" | grep -Fx "  {state_dir}"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_purge_uninstall_removes_state_and_account_when_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            app_dir = tmp_path / "opt" / "retrostation-mc"
            config_dir = tmp_path / "etc" / "retrostation-mc"
            state_dir = tmp_path / "var" / "lib" / "retrostation-mc"
            systemd_file = tmp_path / "etc" / "systemd" / "system" / "retrostation-mc.service"
            log_file = tmp_path / "commands.log"
            app_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            systemd_file.parent.mkdir(parents=True)
            systemd_file.write_text(
                "\n".join(
                    [
                        "# Managed by RetroStation MC installer",
                        "[Unit]",
                        "Description=RetroStation MC",
                        "[Service]",
                        f"ExecStart={app_dir}/.venv/bin/python {app_dir}/app.py",
                    ]
                ),
                encoding="utf-8",
            )

            harness = f"""set -euo pipefail
{self._script_body()}
APP_DIR="{app_dir}"
CONFIG_DIR="{config_dir}"
STATE_DIR="{state_dir}"
SYSTEMD_FILE="{systemd_file}"
LOG_FILE="{log_file}"

command() {{
  if [[ "$1" == "-v" && "$2" == "systemctl" ]]; then
    return 0
  fi
  builtin command "$@"
}}

systemctl() {{
  printf '%s\\n' "systemctl:$*" >>"$LOG_FILE"
}}

id() {{
  if [[ "$#" -eq 1 && "$1" == "$APP_USER" ]]; then
    return 0
  fi
  if [[ "$1" == "-gn" && "$2" == "$APP_USER" ]]; then
    printf '%s\\n' "$APP_GROUP"
    return 0
  fi
  return 1
}}

getent() {{
  if [[ "$1" == "passwd" && "$2" == "$APP_USER" ]]; then
    printf '%s\\n' "$APP_USER:x:999:999::${{STATE_DIR}}:/usr/sbin/nologin"
    return 0
  fi
  if [[ "$1" == "group" && "$2" == "$APP_GROUP" ]]; then
    printf '%s\\n' "$APP_GROUP:x:999:"
    return 0
  fi
  return 2
}}

pgrep() {{
  return 1
}}

find() {{
  return 0
}}

userdel() {{
  printf '%s\\n' "userdel:$*" >>"$LOG_FILE"
}}

groupdel() {{
  printf '%s\\n' "groupdel:$*" >>"$LOG_FILE"
}}

output="$(do_uninstall --purge)"
test ! -e "$APP_DIR"
test ! -e "$CONFIG_DIR"
test ! -e "$STATE_DIR"
test ! -e "$SYSTEMD_FILE"
grep -Fx "userdel:$APP_USER" "$LOG_FILE"
grep -Fx "groupdel:$APP_GROUP" "$LOG_FILE"
printf '%s\\n' "$output" | grep -Fx "Removed paths:"
printf '%s\\n' "$output" | grep -Fx "  {systemd_file}"
printf '%s\\n' "$output" | grep -Fx "  {app_dir}"
printf '%s\\n' "$output" | grep -Fx "  {config_dir}"
printf '%s\\n' "$output" | grep -Fx "  {state_dir}"
printf '%s\\n' "$output" | grep -Fx "Retained paths:"
printf '%s\\n' "$output" | grep -Fx "  (none)"
"""
            subprocess.run(["bash", "-lc", harness], check=True)

    def test_legacy_uninstall_keeps_shared_home_resources_for_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            legacy_base_dir = tmp_path / "home" / "iptv"
            legacy_app_dir = legacy_base_dir / "retrostation-mc"
            systemd_file = tmp_path / "etc" / "systemd" / "system" / "retrostation-mc.service"
            log_file = tmp_path / "commands.log"
            legacy_app_dir.mkdir(parents=True)
            systemd_file.parent.mkdir(parents=True)
            systemd_file.write_text(
                "\n".join(
                    [
                        "# Managed by RetroStation MC installer",
                        "[Unit]",
                        "Description=RetroStation MC",
                        "[Service]",
                        f"ExecStart={legacy_app_dir}/.venv/bin/python {legacy_app_dir}/app.py",
                    ]
                ),
                encoding="utf-8",
            )

            harness = f"""set -euo pipefail
{self._script_body()}
APP_DIR="{tmp_path}/opt/retrostation-mc"
CONFIG_DIR="{tmp_path}/etc/retrostation-mc"
STATE_DIR="{tmp_path}/var/lib/retrostation-mc"
LEGACY_APP_DIR="{legacy_app_dir}"
SYSTEMD_FILE="{systemd_file}"
LOG_FILE="{log_file}"

command() {{
  if [[ "$1" == "-v" && "$2" == "systemctl" ]]; then
    return 0
  fi
  builtin command "$@"
}}

systemctl() {{
  printf '%s\\n' "systemctl:$*" >>"$LOG_FILE"
}}

id() {{
  return 1
}}

output="$(do_uninstall)"
test ! -e "$LEGACY_APP_DIR"
test -d "{legacy_base_dir}"
test ! -e "$SYSTEMD_FILE"
! grep -F "userdel:" "$LOG_FILE"
! grep -F "groupdel:" "$LOG_FILE"
printf '%s\\n' "$output" | grep -Fx "Removed paths:"
printf '%s\\n' "$output" | grep -Fx "  {systemd_file}"
printf '%s\\n' "$output" | grep -Fx "  $LEGACY_APP_DIR"
printf '%s\\n' "$output" | grep -Fx "Retained paths:"
printf '%s\\n' "$output" | grep -Fx "  {legacy_base_dir}"
printf '%s\\n' "$output" | grep -Fx "Warnings:"
printf '%s\\n' "$output" | grep -Fx "  Legacy shared resources were left in place for manual review: {legacy_base_dir} and the iptv user/group."
"""
            subprocess.run(["bash", "-lc", harness], check=True)


if __name__ == "__main__":
    unittest.main()
