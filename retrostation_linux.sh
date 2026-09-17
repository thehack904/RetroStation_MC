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
APP_USER="iptv"
APP_HOME="/home/$APP_USER"
APP_DIR="$APP_HOME/retrostation-mc"
SERVICE_NAME="retrostation-mc"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON_BIN=""
MIN_PYTHON_MINOR=11
MAX_PYTHON_MINOR=14

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
  getent group "$APP_USER" >/dev/null 2>&1 || groupadd --system "$APP_USER"
  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd -r -m -d "$APP_HOME" -s "$no_login_shell" -g "$APP_USER" "$APP_USER"
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

  chmod 755 "$APP_HOME" || true
}

stage_project() {
  if [[ -z "$APP_DIR" || "$APP_DIR" != /* || "$APP_DIR" != "$APP_HOME/"* ]]; then
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
  chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
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
  run_as_app_user "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
  run_as_app_user "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
  run_as_app_user "$APP_DIR/.venv/bin/python" -m pip install --only-binary=:all: -r "$APP_DIR/requirements.txt"
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
  cat >"$SYSTEMD_FILE"<<EOF
[Unit]
Description=RetroStation MC
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

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
  stage_project
  setup_environment
  run_hwaccel_diagnostics
  install_service

  echo "Linux setup complete."
  echo "Installed to: $APP_DIR"
  echo "Service enabled and started: $SERVICE_NAME"
  echo "Check status with: systemctl status $SERVICE_NAME"
}

# ---------------------------------------------------------------------------
# Uninstall helpers
# ---------------------------------------------------------------------------

do_uninstall() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    if [[ -f "$SYSTEMD_FILE" ]]; then
      rm -f "$SYSTEMD_FILE"
      systemctl daemon-reload 2>/dev/null || true
      echo "Removed systemd unit: $SYSTEMD_FILE"
    else
      echo "No systemd unit found at: $SYSTEMD_FILE"
    fi
  fi

  if [[ -d "$APP_DIR" ]]; then
    rm -rf "$APP_DIR"
    echo "Removed install directory: $APP_DIR"
  else
    echo "No install directory found at: $APP_DIR"
  fi

  if [[ -d "$APP_HOME" ]] && find "$APP_HOME" -mindepth 1 -print -quit | grep -q .; then
    echo "Warning: $APP_HOME still contains additional files."
    PRESERVE_APP_USER=true
  else
    PRESERVE_APP_USER=false
  fi

  if [[ "$PRESERVE_APP_USER" == "true" ]]; then
    echo "Preserved user/group: $APP_USER"
  else
    pkill -u "$APP_USER" 2>/dev/null || true

    if id "$APP_USER" >/dev/null 2>&1; then
      userdel "$APP_USER" 2>/dev/null || true
      echo "Removed user: $APP_USER"
    else
      echo "No user found: $APP_USER"
    fi

    if getent group "$APP_USER" >/dev/null 2>&1; then
      groupdel "$APP_USER" 2>/dev/null || true
      echo "Removed group: $APP_USER"
    else
      echo "No group found: $APP_USER"
    fi
  fi

  echo "Linux uninstall complete."
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

case "$COMMAND" in
  install)
    do_install
    ;;
  uninstall)
    do_uninstall
    ;;
  *)
    echo "Usage: $0 {install|uninstall}" >&2
    exit 1
    ;;
esac
