#!/usr/bin/env bash
#
# Sets up a Python virtual environment and installs dependencies.
# Tested on Raspberry Pi OS (Pi 5) and standard Linux laptops.
#
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing system packages (sudo may prompt)..."
if command -v apt >/dev/null 2>&1; then
  sudo apt update
  # ffmpeg/libav: required by PyAV.  v4l-utils: handy for debugging cameras.
  sudo apt install -y python3-venv python3-pip ffmpeg v4l-utils
else
  echo "    apt not found — skipping system packages (install ffmpeg manually)."
fi

if [ ! -d .venv ]; then
  echo "==> Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

echo "==> Installing Python dependencies..."
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo
echo "Setup finished."
echo "  Run on a Raspberry Pi with a camera:  ./run.sh"
echo "  Run a hardware-free demo anywhere:    RPS_DEMO_MODE=true ./run.sh"
