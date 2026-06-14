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

# launch_pac.sh is tracked as executable in git, so a fresh clone/pull keeps
# the +x bit. This chmod is just a harmless safety net (no-op on a clean clone).
chmod +x pi_setup/launch_pac.sh

# --- Autostart on boot ---
# Append to labwc autostart (don't overwrite existing lines)
echo "$(pwd)/pi_setup/launch_pac.sh --autostart &" >> ~/.config/labwc/autostart

# --- Desktop shortcut ---
# The .desktop file ships with __INSTALL_DIR__ placeholders; fill them in
# with your actual install path, then copy to your Desktop.
sed "s|__INSTALL_DIR__|$(pwd)|g" pi_setup/PAC-Simulator.desktop > ~/Desktop/PAC-Simulator.desktop
chmod +x ~/Desktop/PAC-Simulator.desktop

# --- Optional: skip the "execute?" prompt on tap ---
# In PCManFM: Edit > Preferences > General > check "Don't ask options on launch executable file"
```

## What each file does

- `launch_pac.sh` — Shared launcher: cd's to repo root (self-discovered from
  the script's own path), pulls latest from git, runs the simulator.
- `PAC-Simulator.desktop` — Desktop shortcut template. The
  `__INSTALL_DIR__` placeholders get filled in by the `sed` command above.
- `pac_icon.png` — Icon for the shortcut (optional; not included in repo).
