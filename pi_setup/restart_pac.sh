#!/bin/bash
# Restart the PAC Simulator — run by the "Restart Simulator" desktop icon.
#
# How it works: stop the running simulator. If the watchdog (launch_pac.sh) is
# running, it sees the non-zero exit and relaunches the app automatically. If
# no watchdog is running (e.g. it gave up after repeated crashes, or was never
# started), start a fresh one so the app comes back either way.

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
