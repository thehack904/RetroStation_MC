#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "install-linux.sh only supports Linux hosts." >&2
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
cd "$REPO_ROOT"

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt

echo "Linux setup complete."
echo "Start the app with: source .venv/bin/activate && python app.py"
