#!/usr/bin/env bash
#
# Starts the RPS-Robot server.
#
# Environment variables (all optional — see .env.example for the full list):
#   RPS_DEMO_MODE   set to "true" to stream a synthetic feed (no camera needed)
#   CAMERA_INDEX    /dev/video index of the USB camera (default 0)
#   PORT            HTTP port to serve on (default 5000)
#
# Examples:
#   ./run.sh                       # normal mode (requires a camera)
#   RPS_DEMO_MODE=true ./run.sh    # demo mode (no hardware required)
#
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found. Run ./setup.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

export RPS_DEMO_MODE="${RPS_DEMO_MODE:-false}"
export CAMERA_INDEX="${CAMERA_INDEX:-0}"
export CAMERA_WIDTH="${CAMERA_WIDTH:-640}"
export CAMERA_HEIGHT="${CAMERA_HEIGHT:-360}"
export CAMERA_FPS="${CAMERA_FPS:-20}"
export PORT="${PORT:-5000}"

python app.py
