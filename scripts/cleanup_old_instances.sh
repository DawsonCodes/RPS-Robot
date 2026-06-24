#!/usr/bin/env bash
set -euo pipefail

echo "Stopping old related processes..."
pkill -f "python.*app.py" 2>/dev/null || true
pkill -f "RPS-Robot" 2>/dev/null || true
pkill -f "openclaw" 2>/dev/null || true

echo "Removing old leftover folders if they exist..."
for path in \
  "$HOME/Documents/openclaw" \
  "$HOME/Documents/OpenClaw" \
  "$HOME/openclaw" \
  "$HOME/OpenClaw" \
  "$HOME/Documents/RPS-Robot-old" \
  "$HOME/RPS-Robot-old"
do
  if [ -e "$path" ]; then
    echo "Removing $path"
    rm -rf "$path"
  fi
done

echo "Cleanup done."
