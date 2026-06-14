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
chmod +x pi_setup/launch_pac.sh pi_setup/restart_pac.sh

# --- Autostart on boot ---
# Append to labwc autostart (don't overwrite existing lines)
echo "$(pwd)/pi_setup/launch_pac.sh --autostart &" >> ~/.config/labwc/autostart

# --- Desktop shortcuts (launch + restart) ---
# The .desktop files ship with __INSTALL_DIR__ placeholders; fill them in
# with your actual install path, then copy to your Desktop.
sed "s|__INSTALL_DIR__|$(pwd)|g" pi_setup/PAC-Simulator.desktop > ~/Desktop/PAC-Simulator.desktop
sed "s|__INSTALL_DIR__|$(pwd)|g" pi_setup/Restart-PAC-Simulator.desktop > ~/Desktop/Restart-PAC-Simulator.desktop
chmod +x ~/Desktop/PAC-Simulator.desktop ~/Desktop/Restart-PAC-Simulator.desktop

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
- **Manual (Restart Simulator icon).** Tapping the **Restart Simulator**
  desktop icon kills the running app; the watchdog brings it back (or a fresh
  watchdog is started if none is running). Use this if the app is misbehaving
  but hasn't crashed.

> Note: a *frozen* (not crashed) app stays full-screen and covers the desktop,
> so the Restart icon may be unreachable in that case. Crashes — where the
> window closes — are handled by both mechanisms. To also recover frozen
> sessions without a keyboard, wire the GPIO reset button to `restart_pac.sh`.

## What each file does

- `launch_pac.sh` — Shared launcher + watchdog: cd's to repo root
  (self-discovered from the script's own path), pulls latest from git, then
  runs the simulator in a relaunch-on-crash loop.
- `restart_pac.sh` — Stops the running app so the watchdog relaunches it
  (starts a fresh watchdog if none is running). Backs the Restart icon.
- `PAC-Simulator.desktop` — Launch shortcut template. The `__INSTALL_DIR__`
  placeholders get filled in by the `sed` command above.
- `Restart-PAC-Simulator.desktop` — Restart shortcut template (same
  placeholder substitution).
- `pac_icon.png` / `restart_icon.png` — Icons for the shortcuts (optional;
  not included in repo — the shortcut works without them).
