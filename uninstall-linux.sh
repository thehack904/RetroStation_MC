#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "uninstall-linux.sh only supports Linux hosts." >&2
  exit 1
fi

if [[ $(id -u) -ne 0 ]]; then
  echo "Run uninstall-linux.sh as root (sudo)." >&2
  exit 1
fi

APP_USER="iptv"
APP_HOME="/home/$APP_USER"
APP_DIR="$APP_HOME/retrostation-mc"
SERVICE_NAME="retrostation-mc"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

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
