# Pi Setup

Files for configuring the Raspberry Pi to run the PAC Simulator.

## Installation

After cloning the repo to `/home/pgehlich/PAC-Simulator/`:

```bash
# Make launcher executable
chmod +x /home/pgehlich/PAC-Simulator/pi_setup/launch_pac.sh

# --- Autostart on boot ---
# Add to labwc autostart (append, don't overwrite existing lines)
echo '/home/pgehlich/PAC-Simulator/pi_setup/launch_pac.sh --autostart &' >> ~/.config/labwc/autostart

# --- Desktop shortcut ---
# Copy to desktop and make executable
cp /home/pgehlich/PAC-Simulator/pi_setup/PAC-Simulator.desktop ~/Desktop/
chmod +x ~/Desktop/PAC-Simulator.desktop

# --- Optional: skip the "execute?" prompt on tap ---
# In PCManFM: Edit > Preferences > General > check "Don't ask options on launch executable file"
```

## What each file does

- `launch_pac.sh` — Shared launcher: cd's to repo, pulls latest, runs simulator
- `PAC-Simulator.desktop` — Desktop shortcut (single tap to launch)
- `pac_icon.png` — Icon for the shortcut (optional, can be added later)
