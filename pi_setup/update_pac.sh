#!/bin/bash
# One-click update — run by the "Update Simulator" desktop icon.
# Pulls the latest code from GitHub, shows a quick result dialog, then restarts
# the simulator so the new version is running.

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO_ROOT"

OUT="$(git pull --ff-only 2>&1)"
STATUS=$?

# Touch-friendly result popup if zenity is available; otherwise just proceed.
if command -v zenity >/dev/null 2>&1; then
    if [ "$STATUS" -eq 0 ]; then
        zenity --info --title="PAC Simulator Update" --width=420 \
            --text="Update complete:

$OUT

The simulator will now restart with the latest version."
    else
        zenity --error --title="PAC Simulator Update" --width=420 \
            --text="Update failed (no changes were applied):

$OUT"
        exit 1
    fi
fi

# Stop on a failed pull even without zenity.
[ "$STATUS" -eq 0 ] || exit 1

# Restart so the freshly pulled code takes effect. start_pac.sh stops the
# running app; the watchdog relaunches it (running the now-updated file), or a
# fresh watchdog is started if none is running.
"$REPO_ROOT/pi_setup/start_pac.sh"
