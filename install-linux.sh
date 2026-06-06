#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "install-linux.sh only supports Linux hosts." >&2
  exit 1
fi

if [[ $(id -u) -ne 0 ]]; then
  echo "Run install-linux.sh as root (sudo)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found in PATH." >&2
  exit 1
fi

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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="iptv"
APP_HOME="/home/$APP_USER"
APP_DIR="$APP_HOME/retrostation-mc"
SERVICE_NAME="retrostation-mc"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

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

setup_environment() {
  run_as_app_user python3 -m venv "$APP_DIR/.venv"
  run_as_app_user "$APP_DIR/.venv/bin/pip" install --upgrade pip
  run_as_app_user "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
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

ensure_systemd_available
ensure_user
stage_project
setup_environment
install_service

echo "Linux setup complete."
echo "Installed to: $APP_DIR"
echo "Service enabled and started: $SERVICE_NAME"
echo "Check status with: systemctl status $SERVICE_NAME"
