#!/bin/bash
# PAC Simulator launcher for Raspberry Pi
# Used by both autostart and desktop shortcut.
# Works from any install location — derives the repo root from the script path.

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO_ROOT"

# Small delay on autostart to let the desktop fully load
if [ "$1" = "--autostart" ]; then
    sleep 3
fi

# Pull latest from git (skip if offline)
git pull --ff-only 2>/dev/null || true

# Launch the simulator
/usr/bin/python3 pac_simulator.py
