#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

source .venv/bin/activate

export CAMERA_INDEX="${CAMERA_INDEX:-0}"
export CAMERA_WIDTH="${CAMERA_WIDTH:-640}"
export CAMERA_HEIGHT="${CAMERA_HEIGHT:-360}"
export CAMERA_FPS="${CAMERA_FPS:-20}"
export SHOW_OVERLAY="${SHOW_OVERLAY:-0}"

python app.py
