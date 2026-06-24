#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

sudo apt update
sudo apt install -y python3-venv python3-pip python3-opencv v4l-utils ffmpeg

if [ ! -d .venv ]; then
  python3 -m venv --system-site-packages .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo
echo "Setup finished."
echo "Run with: ./run.sh"
