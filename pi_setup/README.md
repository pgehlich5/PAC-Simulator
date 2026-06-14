# Pi Setup

Files for configuring the Raspberry Pi to run the PAC Simulator at boot
and via a desktop shortcut.

## Installation

Clone the repo anywhere you like — these scripts derive paths from their own
location, so you don't have to install at any particular path. The examples
below assume `~/PAC-Simulator/`:

```bash
git clone https://github.com/pgehlich5/PAC-Simulator.git ~/PAC-Simulator
cd ~/PAC-Simulator

# The .sh scripts are tracked as executable in git, so a fresh clone/pull
# keeps the +x bit. These chmods are just a harmless safety net.
chmod +x pi_setup/launch_pac.sh pi_setup/start_pac.sh

# --- Autostart on boot ---
# Append to labwc autostart (don't overwrite existing lines)
echo "$(pwd)/pi_setup/launch_pac.sh --autostart &" >> ~/.config/labwc/autostart

# --- Desktop shortcuts (launch + start/restart) ---
# The .desktop files ship with __INSTALL_DIR__ placeholders; fill them in
# with your actual install path, then copy to your Desktop.
sed "s|__INSTALL_DIR__|$(pwd)|g" pi_setup/PAC-Simulator.desktop > ~/Desktop/PAC-Simulator.desktop
sed "s|__INSTALL_DIR__|$(pwd)|g" pi_setup/Start-PAC-Simulator.desktop > ~/Desktop/Start-PAC-Simulator.desktop
chmod +x ~/Desktop/PAC-Simulator.desktop ~/Desktop/Start-PAC-Simulator.desktop

# --- Optional: skip the "execute?" prompt on tap ---
# In PCManFM: Edit > Preferences > General > check "Don't ask options on launch executable file"
```

## Crash recovery

The simulator is built for others to use, so it recovers from crashes two ways:

- **Automatic (watchdog).** `launch_pac.sh` runs the app in a loop and
  relaunches it automatically if it crashes (non-zero exit). A clean exit
  (the in-app **EXIT** button or Escape) stops the loop so you can reach the
  desktop. After ~5 rapid crashes it gives up to avoid spinning. Because both
  autostart and the desktop shortcut go through `launch_pac.sh`, the app is
  self-healing in both cases.
- **Manual (Start Simulator icon).** Tapping the green **Start Simulator**
  desktop icon launches the app — or, if it's already running, relaunches it
  cleanly. Use this to get going, or if the app is misbehaving but hasn't
  crashed. (One "Start" button is more intuitive for learners than a separate
  "restart"; it does both.)

> Note: a *frozen* (not crashed) app stays full-screen and covers the desktop,
> so the Start icon may be unreachable in that case. Crashes — where the
> window closes — are handled by both mechanisms. To also recover frozen
> sessions without a keyboard, wire the GPIO reset button to `start_pac.sh`.

## What each file does

- `launch_pac.sh` — Shared launcher + watchdog: cd's to repo root
  (self-discovered from the script's own path), pulls latest from git, then
  runs the simulator in a relaunch-on-crash loop.
- `start_pac.sh` — Starts the app, or relaunches it if already running (stops
  it so the watchdog brings it back, or starts a fresh watchdog). Backs the
  Start icon.
- `PAC-Simulator.desktop` — Launch shortcut template. The `__INSTALL_DIR__`
  placeholders get filled in by the `sed` command above.
- `Start-PAC-Simulator.desktop` — Start shortcut template (same placeholder
  substitution).
- `start_icon.png` — Green play-button icon for the Start shortcut (generated
  by `make_start_icon.py`).
- `make_start_icon.py` — Regenerates `start_icon.png` (needs Pillow).
- `pac_icon.png` — Optional icon for the launch shortcut (not included in
  repo — the shortcut works without it).
