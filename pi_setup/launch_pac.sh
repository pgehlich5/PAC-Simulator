#!/bin/bash
# PAC Simulator launcher + watchdog for Raspberry Pi.
# Used by both autostart and the desktop shortcut.
# Works from any install location — derives the repo root from the script path.
#
# Self-healing: runs the simulator in a loop and relaunches it automatically
# if it CRASHES (non-zero exit). A clean exit (the in-app EXIT button or the
# Escape key call root.destroy() -> exit code 0) stops the loop so you can get
# to the desktop. A repeated fast crash-loop gives up instead of spinning.

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO_ROOT"

# Small delay on autostart to let the desktop fully load
if [ "$1" = "--autostart" ]; then
    sleep 3
fi

# Pull latest from git (skip if offline). Done once here, not on every
# crash-relaunch, so a mid-session restart is instant.
git pull --ff-only 2>/dev/null || true

# --- Watchdog loop ---------------------------------------------------------
fail_count=0
while true; do
    start=$(date +%s)
    /usr/bin/python3 pac_simulator.py
    code=$?

    # Clean exit (EXIT button / Escape / window close) -> stop the watchdog.
    [ "$code" -eq 0 ] && break

    # Crashed or was killed (e.g. by the Restart icon). Count rapid crashes
    # so a hard failure doesn't spin forever.
    if [ $(( $(date +%s) - start )) -lt 5 ]; then
        fail_count=$((fail_count + 1))
    else
        fail_count=0   # it ran for a while; treat as a fresh failure
    fi
    if [ "$fail_count" -ge 5 ]; then
        echo "PAC Simulator crashed $fail_count times in quick succession;" \
             "stopping watchdog. Use the Restart icon to try again." >&2
        break
    fi

    sleep 2
done
