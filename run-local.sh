#!/usr/bin/env bash
set -euo pipefail

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required but not found in PATH." >&2
  exit 1
fi

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
