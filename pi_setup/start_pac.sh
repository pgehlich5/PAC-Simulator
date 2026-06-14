#!/bin/bash
# Start (or restart) the PAC Simulator — run by the "Start Simulator" icon.
#
# Acts as a single "Start" button for non-technical users: if the app isn't
# running it launches it; if it is running it relaunches it cleanly. How:
# stop any running simulator, then — if the watchdog (launch_pac.sh) is alive
# it relaunches the app on its own; if not (it gave up after repeated crashes,
# or was never started), start a fresh watchdog so the app comes up either way.

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"

# Stop the running simulator (gentle first, then force if it's wedged/frozen).
pkill -f "pac_simulator.py" 2>/dev/null
sleep 2
pkill -9 -f "pac_simulator.py" 2>/dev/null

# If a watchdog loop is alive, it will relaunch the app on its own.
# Otherwise, start a fresh watchdog (detached so it outlives this script).
if ! pgrep -f "pi_setup/launch_pac.sh" >/dev/null; then
    sleep 1
    setsid "$REPO_ROOT/pi_setup/launch_pac.sh" >/dev/null 2>&1 &
fi
