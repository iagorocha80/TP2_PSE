#!/usr/bin/env bash
# Install the dependencies in a local virtual environment and start the
# application on the Raspberry Pi.
#
#   ./run.sh            web server (reads the HC-SR04) + OLED display
#   ./run.sh web        web server only
#   ./run.sh display    OLED display only (needs the web server running)
set -euo pipefail

cd "$(dirname "$0")"

# --system-site-packages reuses the RPi.GPIO already installed in the system
if [ ! -d .venv ]; then
    python3 -m venv --system-site-packages .venv
fi
if ! .venv/bin/python -c "import RPi.GPIO" 2>/dev/null; then
    echo "RPi.GPIO not found: sudo apt install python3-rpi.gpio" >&2
    exit 1
fi
.venv/bin/pip install --quiet -r requirements.txt

mode="${1:-all}"
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT INT TERM

if [ "$mode" = "all" ] || [ "$mode" = "web" ]; then
    .venv/bin/python -u webpage.py &
    pids+=($!)
fi
if [ "$mode" = "all" ] || [ "$mode" = "display" ]; then
    .venv/bin/python -u writeDisplay.py &
    pids+=($!)
fi

wait "${pids[@]}"
