# PAC Simulator

A pulmonary artery catheter (PAC / Swan-Ganz) insertion simulator for medical
education. Displays real-time hemodynamic waveforms on a Philips IntelliVue-style
monitor as a learner advances a catheter through the heart chambers
(SVC → RA → RV → PA → PCWP).

Designed to run on a desktop PC for practice or on a Raspberry Pi 5 driving a
3D-printed heart with a rotary encoder for physical catheter advancement.

## Modes

- **Real Patient mode** (default) — plays back real waveforms extracted from the
  open-access [MIMIC-III Waveform Database](https://physionet.org/content/mimic3wdb/1.0/).
  Advancing the catheter switches PAP clips per chamber while ECG and arterial
  pressure stream continuously, mirroring a real bedside monitor.
- **Simulated mode** _(experimental — hidden by default)_ — mathematically
  generated waveforms (NeuroKit2 for ECG, Gaussian models for ABP/PAP) driven by
  scenario JSON files. Heart rate and PA pressure are adjustable in real time via
  touch-friendly buttons. Still a work in progress, so it's hidden from the UI;
  launch it with `--mode simulated` (see below) or re-enable the toggle by setting
  `SHOW_SIMULATED_MODE = True` in `pac_simulator.py`.

## Quick start

```bash
pip install -r requirements.txt
python pac_simulator.py
```

This is for a PC. On a Raspberry Pi, follow
[Raspberry Pi configuration](#raspberry-pi-configuration) instead.

Other launches:

```bash
python pac_simulator.py --patient grover_p003914    # specific patient
python pac_simulator.py --mode simulated            # generated waveforms
python pac_simulator.py --mode simulated --scenario septic_shock
```

Keyboard controls when no rotary encoder is connected:

- `+` / `=` — advance the catheter (10 encoder steps)
- `-` — retract the catheter
- `r` — reset to SVC
- `h` — toggle the Patient History panel
- `Esc` — quit

## Patients included

Listed in on-screen button order:

| Patient    | Waveforms                                      | Notes                                                          |
|------------|------------------------------------------------|---------------------------------------------------------------|
| PowerPoint | PAP only (no ECG/ABP)                          | Digitized from a teaching slide via WebPlotDigitizer          |
| Perfect Patty | ECG + ABP + per-chamber PAP (per-chamber bg) | **Synthetic** textbook reference case — start here (see below); the patient the app opens on |
| Esther     | ECG + ABP + per-chamber PAP                    | Complete PAC float (SVC→RA→RV→PA→wedge); first algorithm-found case |
| Herbert    | ECG + ABP + per-chamber PAP                    | Teaching case: septic shock                                   |
| Horace     | ECG + ABP + per-chamber PAP (per-chamber bg)   | Complete float with severe pulmonary hypertension; chamber-locked ectopy |
| Grover     | ECG + ABP + per-chamber PAP + RV ectopy        | Teaching case: cardiogenic shock + severe AS                  |

Clinical vignettes shipped publicly with this repository are **fictional
teaching cases** written for educational use. They are not derived from
individual patient records.

### Perfect Patty — the synthetic reference case

`waveform_data/ideal/` is **not patient data and not MIMIC-III**. Every sample
is generated from a landmark model: each teaching feature (a wave, c wave, x/y
descents, RV diastolic upslope, dicrotic notch) is defined as a
`(cardiac-cycle phase, pressure)` control point and the points are joined with
PCHIP interpolation, so features land exactly where a textbook draws them.

Everything runs off one beat clock — HR 75 at 125 Hz is exactly 100 samples per
beat, and each clip is a whole number of beats — so clips loop seamlessly and
the PAP landmarks stay phase-locked to the ECG across chamber switches.

Values: SVC mean 3 · RA mean 4 (a > v) · RV 25/5 · PA 25/10, mean 15 · PCWP
mean 10 (v > a, waves delayed). RV and PA share a systolic pressure on purpose:
the diastolic step-up and the appearance of a dicrotic notch are what tell you
the tip crossed the pulmonic valve.

She ships no `vignette.txt` on purpose — she is a reference waveform rather than a
case with a history, so the "Patient History" button is hidden for her.

Because every clip is a whole number of identical, phase-aligned beats, she sets
`"continuous_phase": true` in her `patient.json`. The simulator then carries the
playback position across a chamber switch instead of restarting the beat, so the
ECG never breaks stride and the PAP morphology changes mid-beat — the way it does
when the tip actually crosses a valve. Patients extracted from real recordings
must leave this off; their per-chamber clips are specific time windows that have
to restart together to stay time-locked.

**Regenerate it; don't hand-edit the CSVs.** The waveforms are the output of a
model, so edits made directly to the CSVs are lost the next time anyone runs:

```bash
python setup_ideal_patient.py
```

That script rebuilds `waveform_data/ideal/` from the landmarks in its
`LANDMARKS` dict and (idempotently) registers the patient in `pac_simulator.py`.
To change a waveform, change a landmark and re-run.

## Hardware (Raspberry Pi setup)

- Raspberry Pi 5 with Waveshare 10.1" DSI touchscreen (landscape orientation)
- LPD3806-600BM-G5-24C rotary encoder on GPIO17/18 for catheter advancement
  (see [Wiring](#4-wiring))
- Window manager: labwc (Wayland) — X11 and Wayfire have known issues
- UI is touch-friendly throughout — no keyboard required during teaching

Step thresholds for chamber transitions (configurable in `pac_simulator.py`):

| Chamber | Step range |
|---------|------------|
| SVC     | 0 – 850    |
| RA      | 850 – 1200 |
| RV      | 1200 – 2200|
| PA      | 2200 – 3000|
| PCWP    | 3000+      |

A 20-step stateful hysteresis dead zone absorbs encoder backlash at boundaries.

## Raspberry Pi configuration

Step-by-step setup for a fresh Pi (repeat this for each build).

### 1. OS and dependencies

- Flash **Raspberry Pi OS (64-bit) with desktop** to a **32 GB or larger** card.
  An 8 GB card ran out of space during setup. The newest build runs **Trixie**;
  the first build runs **Bookworm**, and both work.
- **Before first boot, enable the display** or the screen stays blank. With the
  SD card in your PC, open the `bootfs` drive and add these lines to the end of
  `config.txt`:
  ```
  dtoverlay=vc4-kms-v3d
  dtoverlay=vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a
  ```
  This is for DSI1; on DSI0, append `,dsi0` to the second line. The panel also
  needs its 5V and GND cable connected to the Pi's GPIO header.
- Boot to the desktop. Keep the default **labwc** (Wayland) session.
  X11 and Wayfire have known issues with this panel (lost display rotation;
  inverted cursor over remote sharing).
- Update the system, install the three packages the simulator needs, and clone
  the repo:
  ```bash
  sudo apt update && sudo apt full-upgrade -y
  sudo apt install -y python3-numpy python3-tk python3-gpiozero
  git clone https://github.com/pgehlich5/PAC-Simulator.git ~/PAC-Simulator
  ```
  Don't install `requirements.txt` on a Pi. It's for the PC waveform tools and
  the optional NeuroKit2 ECG used by the hidden Simulated mode, and it isn't
  needed for Real Patient mode. These steps are tested on Trixie; the Bookworm
  build was set up earlier with pip.

### 2. Display + touch rotation (the important part)

The Waveshare 10.1" is a native-portrait panel used in landscape, so it must be
rotated — and **the touch input has to rotate with it.** Do this through the
GUI, which syncs display *and* touch together. The steps are the same on
Bookworm and Trixie:

1. **Menu → Preferences → Screen Configuration**
2. **Screen** menu → the DSI output (e.g. **DSI-2**) → **Orientation** → choose
   your landscape orientation → **Apply**
3. **Screen Configuration → Touchscreen** → set **Mouse Emulation** (tap = left
   click, long-press = right click). "Multitouch" mode feels wrong for tapping
   buttons.

If touch ever feels mirrored or offset after this, re-open Screen Configuration,
switch the orientation to another value → Apply, then back to the correct one →
Apply. That forces the touch mapping to re-bind to the rotated display.

> ⚠️ **Do not use Appearance Settings → Defaults → "Set Defaults".** It resets
> desktop/scaling settings and can desync the touch panel from the display (taps
> land across the screen). If that happens, redo step 2.

**Command-line fallback** (only if the GUI sync won't hold): create
`/etc/udev/rules.d/99-waveshare-touch.rules` with a rotation matrix, then reboot:

```
SUBSYSTEM=="input", ENV{ID_INPUT_TOUCHSCREEN}=="1", ENV{LIBINPUT_CALIBRATION_MATRIX}="<matrix>"
```

Waveshare's matrices: `90° = "0 -1 1 1 0 0"`, `180° = "-1 0 1 0 -1 1"`,
`270° = "0 1 0 -1 0 1"`. Prefer the GUI method above — a manual
matrix can conflict with the compositor's own touch mapping. See the
[10.1-DSI-TOUCH-A wiki](https://www.waveshare.com/wiki/10.1-DSI-TOUCH-A).

### 3. Auto-start, on-screen Start/Update buttons, crash recovery

See **[`pi_setup/README.md`](pi_setup/README.md)** — it covers launching the
simulator automatically at boot, the touch-friendly **Start** and **Update**
desktop icons, and the self-healing watchdog that relaunches the app if it
crashes.

### 4. Wiring

This layout uses five Wago 221 lever connectors and two 10 kΩ resistors. The
encoder has open-collector outputs, so each signal line gets an external pull-up
to 3.3V. Wire with the Pi powered off.

**GPIO header.** Pins in brackets are used. The odd-numbered row is the one
farther from the board edge, and pin 1 is at the SD-card end:

```
                    USB-port end                 SD-card end
inner row (odd)    ...  13  [11] [ 9]   7    5    3  [ 1]
outer row (even)   ...  14  [12]  10    8  [ 6] [ 4] [ 2]
```

| Pin | Signal | Connects to             |
|-----|--------|-------------------------|
| 1   | 3.3V   | 3.3V Wago               |
| 2   | 5V     | 5V Wago                 |
| 4   | 5V     | Screen 5V (no Wago)     |
| 6   | GND    | Screen GND (no Wago)    |
| 9   | GND    | GND Wago                |
| 11  | GPIO17 | Signal A Wago           |
| 12  | GPIO18 | Signal B Wago           |

**Wagos:**

| Wago     | Size  | Connections                                |
|----------|-------|--------------------------------------------|
| 5V       | 2-way | Pin 2, encoder red                         |
| GND      | 2-way | Pin 9, encoder black                       |
| 3.3V     | 3-way | Pin 1, resistor 1, resistor 2              |
| Signal A | 3-way | Pin 11, encoder white, other end of resistor 1 |
| Signal B | 3-way | Pin 12, encoder green, other end of resistor 2 |

Resistors have no polarity. Encoder wires can be thin, so check them against the
Wago's rated wire range and give each one a gentle tug after closing the lever.

**Check before relying on it:**

1. With the encoder's red and black wires connected but the signal wires not yet
   landed on the GPIO pins, confirm the 5V and 3.3V Wagos read the right voltages.
2. Confirm pins 11 and 12 only ever read about 3.3V or near 0V. **5V on a GPIO
   pin can damage the Pi.**
3. Turn the encoder while running this command. Both pins should flip between
   high and low:
   ```bash
   pinctrl get 17,18
   ```
4. If advancing the catheter moves the chambers backward, swap the encoder's
   white and green wires.

## Adding your own patient

Create a folder under `waveform_data/` containing a `patient.json` and one or
more `pap_*/` subfolders with PAP CSV clips. The simulator auto-discovers any
folder containing a `patient.json` and shows it as a tab.

Minimum structure:

```
waveform_data/my_patient/
  patient.json                  # {"nickname": "My Patient"}
  pap_svc/PAP.csv + metadata.json
  pap_ra/PAP.csv + metadata.json
  pap_rv/PAP.csv + metadata.json
  pap_pa/PAP.csv + metadata.json
  pap_wedge/PAP.csv + metadata.json
  background/II.csv, ABP.csv, metadata.json   # optional
  vignette.txt                  # optional teaching vignette
```

See `tools/extract_p003914.py` for an end-to-end extraction script that pulls
clips from MIMIC-III via the `wfdb` library.

## For credentialed MIMIC-III researchers

If you have credentialed access to the
[MIMIC-III Clinical Database](https://physionet.org/content/mimiciii/1.4/),
you can attach richer clinical context to any patient by creating a
`clinical_data/` subfolder:

```
waveform_data/<patient>/clinical_data/
  clinical_vignette.txt         # takes precedence over public vignette.txt
  hospital_course.json
  echo_report.json
  past_medical_history.json
  ... etc.
```

The simulator prefers `clinical_data/clinical_vignette.txt` over the public
`vignette.txt` when both are present. The `clinical_data/` path is in
`.gitignore` and must remain so — content there is governed by the PhysioNet
Credentialed Health Data Use Agreement and cannot be redistributed.

## License

- **Code** — MIT License (see `LICENSE`)
- **Waveform data** under `waveform_data/herbert_p001840/` and `waveform_data/grover_p003914/`
  — Open Data Commons Open Database License (ODbL) v1.0 (see
  `waveform_data/LICENSE-ODbL.txt` for required attribution and obligations)
- **`waveform_data/ideal/`** — synthetic, generated by `setup_ideal_patient.py`;
  not patient data and not subject to the ODbL terms above

## Attribution

If you use or publish work based on the waveform data shipped here, please cite:

- Moody, B. et al. (2020). MIMIC-III Waveform Database. PhysioNet.
  https://doi.org/10.13026/c2607m
- Johnson, A. E. W. et al. (2016). MIMIC-III, a freely accessible critical
  care database. Scientific Data, 3, 160035.
- Goldberger, A. et al. (2000). PhysioBank, PhysioToolkit, and PhysioNet.
  Circulation, 101(23), e215–e220.
