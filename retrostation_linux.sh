#!/usr/bin/env bash
set -euo pipefail

COMMAND="${1:-}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "retrostation_linux.sh only supports Linux hosts." >&2
  exit 1
fi

if [[ $(id -u) -ne 0 ]]; then
  echo "Run retrostation_linux.sh as root (sudo)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="retrostation-mc"
APP_GROUP="$APP_USER"
APP_DIR="/opt/retrostation-mc"
CONFIG_DIR="/etc/retrostation-mc"
STATE_DIR="/var/lib/retrostation-mc"
SERVICE_NAME="retrostation-mc"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CONFIG_ENV_FILE="$CONFIG_DIR/${SERVICE_NAME}.conf"
LEGACY_APP_DIR="/home/iptv/retrostation-mc"
LEGACY_SERVICE_ENV_FILES=(
  "/etc/default/${SERVICE_NAME}"
  "/etc/sysconfig/${SERVICE_NAME}"
)
LEGACY_CONFIG_FILES=(
  ".env"
  ".env.local"
  "config.env"
)
MIGRATION_VERSION="linux-layout-v1.5.0"
MIGRATION_STATE_DIR="$STATE_DIR/.migration"
MIGRATION_DONE_FILE="$MIGRATION_STATE_DIR/${MIGRATION_VERSION}.done"
MIGRATION_STEPS_FILE="$MIGRATION_STATE_DIR/${MIGRATION_VERSION}.steps"
MIGRATION_BACKUP_PATH_FILE="$MIGRATION_STATE_DIR/${MIGRATION_VERSION}.backup-path"
BACKUP_ROOT="/var/backups/retrostation-mc"
UNIT_MARKER="# Managed by RetroStation MC installer"
PYTHON_BIN=""
MIN_PYTHON_MINOR=11
MAX_PYTHON_MINOR=14
REMOVED_PATHS=()
RETAINED_PATHS=()
UNINSTALL_WARNINGS=()

MUTABLE_DIRS=(
  "data"
  "output"
  "runtime"
)

# Mutable state is linked at the directory boundary rather than as individual
# files. Several RSMC state files are atomically replaced or unlinked at
# runtime; a file-level symlink under root-owned /opt would require write
# permission on the /opt parent directory and breaks the isolated layout.
MUTABLE_FILES=()

# ---------------------------------------------------------------------------
# Install helpers
# ---------------------------------------------------------------------------

run_as_app_user() {
  if command -v sudo >/dev/null 2>&1; then
    sudo -u "$APP_USER" "$@"
  else
    runuser -u "$APP_USER" -- "$@"
  fi
}

ensure_user() {
  local no_login_shell
  no_login_shell="$(command -v nologin 2>/dev/null || echo /usr/sbin/nologin)"
  getent group "$APP_GROUP" >/dev/null 2>&1 || groupadd --system "$APP_GROUP"
  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd -r -M -d "$STATE_DIR" -s "$no_login_shell" -g "$APP_GROUP" "$APP_USER"
  else
    local current_group current_home current_shell
    current_group="$(id -gn "$APP_USER")"
    current_home="$(getent passwd "$APP_USER" | cut -d: -f6)"
    current_shell="$(getent passwd "$APP_USER" | cut -d: -f7)"
    if [[ "$current_group" != "$APP_GROUP" || "$current_home" != "$STATE_DIR" || "$current_shell" != "$no_login_shell" ]]; then
      echo "Existing user $APP_USER is not managed by this installer; refusing to modify it." >&2
      exit 1
    fi
  fi

  # Hardware video acceleration uses DRM render nodes, which are normally
  # owned by the render/video groups. Add the service account when those
  # groups exist so VA-API/QSV can open /dev/dri without manual intervention.
  local gpu_groups=()
  getent group video >/dev/null 2>&1 && gpu_groups+=(video)
  getent group render >/dev/null 2>&1 && gpu_groups+=(render)
  if (( ${#gpu_groups[@]} > 0 )); then
    local gpu_group_csv
    gpu_group_csv="$(IFS=,; echo "${gpu_groups[*]}")"
    usermod -aG "$gpu_group_csv" "$APP_USER"
  fi
}

ensure_layout_directories() {
  install -d -m 0755 -o root -g root "$CONFIG_DIR"
  install -d -m 0755 -o "$APP_USER" -g "$APP_GROUP" "$STATE_DIR"
}

install_config_defaults() {
  if [[ ! -f "$CONFIG_ENV_FILE" ]]; then
    cat >"$CONFIG_ENV_FILE"<<EOF
# RetroStation MC service environment
HOME=$STATE_DIR
RETROSTATION_MC_CONFIG_DIR=$CONFIG_DIR
RETROSTATION_MC_STATE_DIR=$STATE_DIR
PYTHONUNBUFFERED=1
EOF
  fi

  chown root:root "$CONFIG_ENV_FILE"
  chmod 0644 "$CONFIG_ENV_FILE"
}

ensure_migration_metadata_dir() {
  install -d -m 0750 -o root -g root "$MIGRATION_STATE_DIR"
  install -d -m 0750 -o root -g root "$BACKUP_ROOT"
}

migration_step_done() {
  local step="$1"
  [[ -f "$MIGRATION_STEPS_FILE" ]] && grep -Fxq "$step" "$MIGRATION_STEPS_FILE"
}

mark_migration_step() {
  local step="$1"
  ensure_migration_metadata_dir
  if ! migration_step_done "$step"; then
    echo "$step" >>"$MIGRATION_STEPS_FILE"
  fi
}

legacy_install_detected() {
  [[ -d "$LEGACY_APP_DIR" ]] && [[ -f "$LEGACY_APP_DIR/app.py" ]]
}

copy_tree_preserve_no_clobber() {
  local source_path="$1"
  local target_path="$2"

  [[ -e "$source_path" ]] || return 0

  if [[ -d "$source_path" ]]; then
    mkdir -p "$target_path"
    cp -a --update=none "$source_path"/. "$target_path"/
  else
    mkdir -p "$(dirname "$target_path")"
    cp -a --update=none "$source_path" "$target_path"
  fi
}

determine_migration_backup_path() {
  local backup_path

  ensure_migration_metadata_dir
  if [[ -s "$MIGRATION_BACKUP_PATH_FILE" ]]; then
    backup_path="$(cat "$MIGRATION_BACKUP_PATH_FILE")"
  else
    backup_path="$BACKUP_ROOT/${SERVICE_NAME}-${MIGRATION_VERSION}-$(date +%Y%m%d%H%M%S)"
    echo "$backup_path" >"$MIGRATION_BACKUP_PATH_FILE"
  fi

  install -d -m 0750 -o root -g root "$backup_path"
  printf '%s\n' "$backup_path"
}

backup_legacy_installation() {
  local backup_path service_env_file config_file

  if migration_step_done "backup_complete"; then
    return
  fi

  backup_path="$(determine_migration_backup_path)"

  install -d -m 0750 -o root -g root "$backup_path/systemd" "$backup_path/config" "$backup_path/state"

  [[ -f "$SYSTEMD_FILE" ]] && cp -a "$SYSTEMD_FILE" "$backup_path/systemd/"
  [[ -f "$CONFIG_ENV_FILE" ]] && cp -a "$CONFIG_ENV_FILE" "$backup_path/config/"

  for service_env_file in "${LEGACY_SERVICE_ENV_FILES[@]}"; do
    [[ -f "$service_env_file" ]] && cp -a "$service_env_file" "$backup_path/config/"
  done

  for config_file in "${LEGACY_CONFIG_FILES[@]}"; do
    [[ -f "$LEGACY_APP_DIR/$config_file" ]] && cp -a "$LEGACY_APP_DIR/$config_file" "$backup_path/config/"
  done

  copy_tree_preserve_no_clobber "$LEGACY_APP_DIR/data" "$backup_path/state/data"
  copy_tree_preserve_no_clobber "$LEGACY_APP_DIR/output" "$backup_path/state/output"
  copy_tree_preserve_no_clobber "$LEGACY_APP_DIR/runtime" "$backup_path/state/runtime"

  cat >"$backup_path/rollback.txt"<<EOF
Legacy backup for RetroStation MC migration ($MIGRATION_VERSION)

Backup path: $backup_path
Legacy install path retained: $LEGACY_APP_DIR

Rollback steps:
1. Restore the legacy unit and service environment from this backup.
2. Stop the current service: systemctl stop $SERVICE_NAME
3. Reinstall legacy files under $LEGACY_APP_DIR from this backup as needed.
4. Reload and restart legacy service: systemctl daemon-reload && systemctl start $SERVICE_NAME
EOF

  mark_migration_step "backup_complete"
  echo "Legacy backup saved to: $backup_path"
}

migrate_legacy_configuration() {
  local service_env_file config_file

  if migration_step_done "config_migrated"; then
    return
  fi

  install -d -m 0750 -o root -g root "$CONFIG_DIR/legacy"

  for service_env_file in "${LEGACY_SERVICE_ENV_FILES[@]}"; do
    [[ -f "$service_env_file" ]] && cp -a --update=none "$service_env_file" "$CONFIG_DIR/legacy/"
  done

  for config_file in "${LEGACY_CONFIG_FILES[@]}"; do
    [[ -f "$LEGACY_APP_DIR/$config_file" ]] && cp -a --update=none "$LEGACY_APP_DIR/$config_file" "$CONFIG_DIR/legacy/"
  done

  mark_migration_step "config_migrated"
}

migrate_legacy_state() {
  local relative_path

  if migration_step_done "state_migrated"; then
    return
  fi

  for relative_path in data output runtime; do
    copy_tree_preserve_no_clobber "$LEGACY_APP_DIR/$relative_path" "$STATE_DIR/$relative_path"
  done

  mark_migration_step "state_migrated"
}

run_legacy_migration() {
  if ! legacy_install_detected; then
    return
  fi

  if [[ -f "$MIGRATION_DONE_FILE" ]]; then
    return
  fi

  echo "Detected legacy install at $LEGACY_APP_DIR; starting restartable migration to $APP_DIR."
  backup_legacy_installation
  migrate_legacy_configuration
  migrate_legacy_state
}

validate_migrated_installation() {
  if ! legacy_install_detected || [[ -f "$MIGRATION_DONE_FILE" ]]; then
    return
  fi

  [[ -x "$APP_DIR/.venv/bin/python" ]]
  [[ -f "$APP_DIR/app.py" ]]
  [[ -d "$STATE_DIR/data" ]]
  run_as_app_user "$APP_DIR/.venv/bin/python" -c "import app.config_store"

  mark_migration_step "validated"
}

mark_legacy_migration_complete() {
  if ! legacy_install_detected; then
    return
  fi

  ensure_migration_metadata_dir
  touch "$MIGRATION_DONE_FILE"
  mark_migration_step "completed"
  echo "Legacy migration marker written: $MIGRATION_DONE_FILE"
}

unit_belongs_to_project() {
  [[ -f "$SYSTEMD_FILE" ]] || return 0

  if grep -Fq "$UNIT_MARKER" "$SYSTEMD_FILE"; then
    return 0
  fi

  grep -Fq "Description=RetroStation MC" "$SYSTEMD_FILE" &&
    grep -Eq '^ExecStart=/home/iptv/retrostation-mc/\.venv/bin/python /home/iptv/retrostation-mc/app\.py$|^ExecStart=/opt/retrostation-mc/\.venv/bin/python /opt/retrostation-mc/app\.py$' "$SYSTEMD_FILE"
}

assert_safe_to_manage_systemd_unit() {
  if [[ -f "$SYSTEMD_FILE" ]] && ! unit_belongs_to_project; then
    echo "Refusing to overwrite unverified systemd unit: $SYSTEMD_FILE" >&2
    exit 1
  fi
}

record_removed_path() {
  REMOVED_PATHS+=("$1")
}

record_retained_path() {
  RETAINED_PATHS+=("$1")
}

record_uninstall_warning() {
  UNINSTALL_WARNINGS+=("$1")
}

print_path_summary() {
  local heading="$1"
  shift || true

  echo "$heading"
  if (( $# == 0 )); then
    echo "  (none)"
  else
    printf '  %s\n' "$@"
  fi
}

print_uninstall_summary() {
  print_path_summary "Removed paths:" "${REMOVED_PATHS[@]}"
  print_path_summary "Retained paths:" "${RETAINED_PATHS[@]}"
  if (( ${#UNINSTALL_WARNINGS[@]} > 0 )); then
    echo "Warnings:"
    printf '  %s\n' "${UNINSTALL_WARNINGS[@]}"
  fi
  echo "Linux uninstall complete."
}

project_user_artifacts_remain() {
  local project_path

  for project_path in "$APP_DIR" "$STATE_DIR"; do
    if [[ -e "$project_path" ]] && find "$project_path" -xdev \( -user "$APP_USER" -o -group "$APP_GROUP" \) -print -quit 2>/dev/null | grep -q .; then
      return 0
    fi
  done

  return 1
}

remove_managed_account_if_safe() {
  local current_home current_group

  if ! id "$APP_USER" >/dev/null 2>&1; then
    return
  fi

  current_home="$(getent passwd "$APP_USER" | cut -d: -f6)"
  current_group="$(id -gn "$APP_USER")"
  if [[ "$current_home" != "$STATE_DIR" || "$current_group" != "$APP_GROUP" ]]; then
    record_uninstall_warning "Retained user $APP_USER because it is not using the expected managed home/group."
    return
  fi

  if pgrep -u "$APP_USER" >/dev/null 2>&1; then
    record_uninstall_warning "Retained user $APP_USER because running processes still use it."
    return
  fi

  if project_user_artifacts_remain; then
    record_uninstall_warning "Retained user $APP_USER because project-owned files still require it."
    return
  fi

  if userdel "$APP_USER" 2>/dev/null; then
    echo "Removed user: $APP_USER"
  else
    record_uninstall_warning "Could not remove user $APP_USER automatically."
    return
  fi

  if getent group "$APP_GROUP" >/dev/null 2>&1; then
    if groupdel "$APP_GROUP" 2>/dev/null; then
      echo "Removed group: $APP_GROUP"
    else
      record_uninstall_warning "Could not remove group $APP_GROUP automatically."
    fi
  fi
}

stop_managed_service_if_running() {
  assert_safe_to_manage_systemd_unit
  if [[ -f "$SYSTEMD_FILE" ]] && systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl stop "$SERVICE_NAME"
  fi
}

stage_project() {
  if [[ -z "$APP_DIR" || "$APP_DIR" != /* || "$APP_DIR" != /opt/* ]]; then
    echo "Refusing to stage files to unexpected location: $APP_DIR" >&2
    exit 1
  fi

  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  (
    cd "$REPO_ROOT"
    tar \
      --exclude='.git' \
      --exclude='.github' \
      --exclude='.venv' \
      --exclude='tests' \
      --exclude='docs' \
      --exclude='__pycache__' \
      --exclude='.pytest_cache' \
      --exclude='*.pyc' \
      --exclude='*.pyo' \
      --exclude='.vscode' \
      --exclude='.idea' \
      -cf - .
  ) | tar -xf - -C "$APP_DIR"
}

link_state_directory() {
  local relative_path="$1"
  local source_path="$APP_DIR/$relative_path"
  local state_path="$STATE_DIR/$relative_path"

  mkdir -p "$(dirname "$source_path")" "$(dirname "$state_path")"

  if [[ -L "$source_path" ]]; then
    if [[ "$(readlink "$source_path")" == "$state_path" ]]; then
      return
    fi
    echo "Refusing to replace unexpected symlink: $source_path" >&2
    exit 1
  fi

  if [[ -d "$source_path" && ! -L "$source_path" ]]; then
    if [[ -e "$state_path" ]]; then
      cp -a --update=none "$source_path"/. "$state_path"/
      rm -rf "$source_path"
    else
      mv "$source_path" "$state_path"
    fi
  elif [[ ! -e "$state_path" ]]; then
    mkdir -p "$state_path"
  fi

  rm -rf "$source_path"
  ln -s "$state_path" "$source_path"
}

link_state_file() {
  local relative_path="$1"
  local source_path="$APP_DIR/$relative_path"
  local state_path="$STATE_DIR/$relative_path"
  local preserved_path="${state_path}.install-default"

  mkdir -p "$(dirname "$source_path")" "$(dirname "$state_path")"

  if [[ -L "$source_path" ]]; then
    if [[ "$(readlink "$source_path")" == "$state_path" ]]; then
      return
    fi
    echo "Refusing to replace unexpected symlink: $source_path" >&2
    exit 1
  fi

  if [[ -f "$source_path" && ! -L "$source_path" ]]; then
    if [[ ! -e "$state_path" ]]; then
      mv "$source_path" "$state_path"
    else
      if [[ -s "$source_path" ]] && ! cmp -s "$source_path" "$state_path"; then
        cp -f "$source_path" "$preserved_path"
      fi
      rm -f "$source_path"
    fi
  elif [[ ! -e "$state_path" ]]; then
    : >"$state_path"
  fi

  rm -f "$source_path"
  ln -s "$state_path" "$source_path"
}

prepare_mutable_state() {
  local relative_path

  for relative_path in "${MUTABLE_DIRS[@]}"; do
    link_state_directory "$relative_path"
  done

  for relative_path in "${MUTABLE_FILES[@]}"; do
    link_state_file "$relative_path"
  done

  chown -R "$APP_USER":"$APP_GROUP" "$STATE_DIR"
}

select_python() {
  local candidate minor

  # Prefer the newest explicitly supported interpreter. This keeps installs
  # working if the system `python3` later advances beyond RSMC's tested range.
  for minor in $(seq "$MAX_PYTHON_MINOR" -1 "$MIN_PYTHON_MINOR"); do
    candidate="python3.${minor}"
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done

  # Fall back to python3 only when it is itself inside the supported range.
  if [[ -z "$PYTHON_BIN" ]] && command -v python3 >/dev/null 2>&1; then
    if python3 - "$MIN_PYTHON_MINOR" "$MAX_PYTHON_MINOR" <<'PY'
import sys
minimum = int(sys.argv[1])
maximum = int(sys.argv[2])
raise SystemExit(0 if sys.version_info.major == 3 and minimum <= sys.version_info.minor <= maximum else 1)
PY
    then
      PYTHON_BIN="$(command -v python3)"
    fi
  fi

  if [[ -z "$PYTHON_BIN" ]]; then
    local detected="not found"
    if command -v python3 >/dev/null 2>&1; then
      detected="$(python3 --version 2>&1) at $(command -v python3)"
    fi
    cat >&2 <<MSG
RetroStation MC requires a validated Python 3.${MIN_PYTHON_MINOR} through 3.${MAX_PYTHON_MINOR} interpreter.
Detected system python3: ${detected}

This safety check prevents installation against a newer, unvalidated Python
release whose binary dependencies may not yet provide compatible wheels.
Install a supported Python version and run the installer again. If multiple
Python versions are installed, RSMC will automatically select the newest
supported one.
MSG
    exit 1
  fi

  echo "Using Python interpreter: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"

  if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    echo "The venv module is unavailable for $PYTHON_BIN." >&2
    echo "Install the matching Python venv package and run the installer again." >&2
    exit 1
  fi
}

setup_environment() {
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$APP_DIR/.venv/bin/python" -m pip install --only-binary=:all: -r "$APP_DIR/requirements.txt"
}

run_hwaccel_diagnostics() {
  echo "Running GPU hardware acceleration diagnostics..."
  if ! run_as_app_user "$APP_DIR/.venv/bin/python" "$APP_DIR/gpu_hwaccel_detect_v3.py"; then
    echo "Warning: GPU hardware acceleration diagnostics failed; continuing with software fallback." >&2
  fi
}

ensure_systemd_available() {
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    echo "This installer requires systemd. For non-systemd systems, use manual installation." >&2
    exit 1
  fi
}

install_service() {
  local tmp_unit
  assert_safe_to_manage_systemd_unit
  tmp_unit="$(mktemp)"

  cat >"$tmp_unit"<<EOF
$UNIT_MARKER
[Unit]
Description=RetroStation MC
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=-$CONFIG_ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app.py
Restart=always
RestartSec=5
ProtectSystem=full
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths=$STATE_DIR

[Install]
WantedBy=multi-user.target
EOF

  install -m 0644 "$tmp_unit" "$SYSTEMD_FILE"
  rm -f "$tmp_unit"

  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME"
}

do_install() {
  select_python

  if ! command -v ffmpeg >/dev/null 2>&1; then
    cat >&2 <<'MSG'
ffmpeg is required but was not found in PATH.
Install it first, then run this installer again.

Common install commands:
  Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg
  Fedora:        sudo dnf install -y ffmpeg
  Arch:          sudo pacman -S ffmpeg
MSG
    exit 1
  fi

  ensure_systemd_available
  ensure_user
  ensure_layout_directories
  install_config_defaults
  stop_managed_service_if_running
  run_legacy_migration
  stage_project
  setup_environment
  prepare_mutable_state
  validate_migrated_installation
  run_hwaccel_diagnostics
  install_service
  mark_legacy_migration_complete

  echo "Linux setup complete."
  echo "Installed to: $APP_DIR"
  echo "Configuration directory: $CONFIG_DIR"
  echo "State directory: $STATE_DIR"
  echo "Service enabled and started: $SERVICE_NAME"
  echo "Check status with: systemctl status $SERVICE_NAME"
}

# ---------------------------------------------------------------------------
# Uninstall helpers
# ---------------------------------------------------------------------------

do_uninstall() {
  local purge=false legacy_base_dir
  if [[ "${1:-}" == "--purge" && $# -eq 1 ]]; then
    purge=true
  elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 {install|uninstall [--purge]}" >&2
    exit 1
  fi

  REMOVED_PATHS=()
  RETAINED_PATHS=()
  UNINSTALL_WARNINGS=()
  legacy_base_dir="$(dirname "$LEGACY_APP_DIR")"

  if command -v systemctl >/dev/null 2>&1; then
    assert_safe_to_manage_systemd_unit
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    if [[ -f "$SYSTEMD_FILE" ]]; then
      rm -f "$SYSTEMD_FILE"
      systemctl daemon-reload 2>/dev/null || true
      record_removed_path "$SYSTEMD_FILE"
    fi
  fi

  if [[ -d "$APP_DIR" ]]; then
    rm -rf "$APP_DIR"
    record_removed_path "$APP_DIR"
  elif [[ -d "$LEGACY_APP_DIR" ]]; then
    rm -rf "$LEGACY_APP_DIR"
    record_removed_path "$LEGACY_APP_DIR"
    record_retained_path "$legacy_base_dir"
    record_uninstall_warning "Legacy shared resources were left in place for manual review: $legacy_base_dir and the iptv user/group."
  fi

  if [[ "$purge" == true ]]; then
    if [[ -d "$CONFIG_DIR" ]]; then
      rm -rf "$CONFIG_DIR"
      record_removed_path "$CONFIG_DIR"
    fi

    if [[ -d "$STATE_DIR" ]]; then
      rm -rf "$STATE_DIR"
      record_removed_path "$STATE_DIR"
    fi
  else
    if [[ -e "$CONFIG_DIR" ]]; then
      record_retained_path "$CONFIG_DIR"
    fi

    if [[ -e "$STATE_DIR" ]]; then
      record_retained_path "$STATE_DIR"
    fi
  fi

  if [[ -d "$LEGACY_APP_DIR" ]]; then
    record_retained_path "$LEGACY_APP_DIR"
    record_uninstall_warning "Legacy shared resources were left in place for manual review: $LEGACY_APP_DIR plus the iptv user/group."
  fi

  if [[ "$purge" == true ]]; then
    remove_managed_account_if_safe
  fi

  print_uninstall_summary
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

case "$COMMAND" in
  install)
    do_install
    ;;
  uninstall)
    do_uninstall "${@:2}"
    ;;
  *)
    echo "Usage: $0 {install|uninstall [--purge]}" >&2
    exit 1
    ;;
esac
