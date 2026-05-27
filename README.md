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
- **Simulated mode** — mathematically generated waveforms (NeuroKit2 for ECG,
  Gaussian models for ABP/PAP) driven by scenario JSON files. Heart rate and
  PA pressure are adjustable in real time via touch-friendly buttons.

## Quick start

```bash
pip install -r requirements.txt
python pac_simulator.py
```

Other launches:

```bash
python pac_simulator.py --patient p003914           # specific patient
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

| Patient    | Waveforms                              | Notes                                              |
|------------|----------------------------------------|----------------------------------------------------|
| Herbert    | ECG + ABP + per-chamber PAP            | Teaching case: septic shock                        |
| Grover     | ECG + ABP + per-chamber PAP + RV ectopy| Teaching case: cardiogenic shock + severe AS       |
| PowerPoint | PAP only (no ECG/ABP)                  | Digitized from a teaching slide via WebPlotDigitizer |

Clinical vignettes shipped publicly with this repository are **fictional
teaching cases** written for educational use. They are not derived from
individual patient records.

## Hardware (Raspberry Pi setup)

- Raspberry Pi 5 with Waveshare 10.1" DSI touchscreen (landscape orientation)
- Rotary encoder on GPIO17/18 for catheter advancement
- Reset button on GPIO2
- Window manager: labwc (Wayland) — X11 and Wayfire have known issues
- UI is touch-friendly throughout — no keyboard required during teaching

Step thresholds for chamber transitions (configurable in `pac_simulator.py`):

| Chamber | Step range |
|---------|------------|
| SVC     | 0 – 850    |
| RA      | 850 – 1200 |
| RV      | 1200 – 2600|
| PA      | 2600 – 3000|
| PCWP    | 3000+      |

A 20-step stateful hysteresis dead zone absorbs encoder backlash at boundaries.

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
- **Waveform data** under `waveform_data/herbert/` and `waveform_data/p003914/`
  — Open Data Commons Open Database License (ODbL) v1.0 (see
  `waveform_data/LICENSE-ODbL.txt` for required attribution and obligations)

## Attribution

If you use or publish work based on the waveform data shipped here, please cite:

- Moody, B. et al. (2020). MIMIC-III Waveform Database. PhysioNet.
  https://doi.org/10.13026/c2607m
- Johnson, A. E. W. et al. (2016). MIMIC-III, a freely accessible critical
  care database. Scientific Data, 3, 160035.
- Goldberger, A. et al. (2000). PhysioBank, PhysioToolkit, and PhysioNet.
  Circulation, 101(23), e215–e220.
