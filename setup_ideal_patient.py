#!/usr/bin/env python3
"""
One-step installer for the "ideal" (textbook) teaching patient.

Run this from the root of your PAC-Simulator checkout:

    python setup_ideal_patient.py

It does two things:
  1. generates waveform_data/ideal/  (all CSVs + metadata + vignette)
  2. registers the patient in pac_simulator.py (two small edits)

Both steps are safe to run twice -- it detects work already done and skips it.
Requires numpy and scipy, which are already in requirements.txt.

How the waveforms are made:

Instead of pulling waveforms from MIMIC-III, this generates them from a set of
named CLINICAL LANDMARKS -- (time in cardiac cycle, pressure) control points
that correspond to real teaching features (a wave, x descent, c wave, v wave,
y descent, dicrotic notch, RV diastolic upslope).  The points are joined with
PCHIP interpolation, which is shape-preserving and will not overshoot between
control points the way a cubic spline does.

Everything is locked to one master beat clock, so:
  - HR 75 -> 800 ms/beat -> exactly 100 samples/beat at 125 Hz (no drift)
  - every clip is an exact whole number of beats -> loops are seamless
  - PAP landmarks stay phase-locked to the ECG across chamber switches

Output matches the existing patient folder format, so the simulator needs
no code changes -- discover_patients() picks it up as a normal patient.
"""

import json
import os

import numpy as np
from scipy.interpolate import PchipInterpolator

# --- Master clock ------------------------------------------------------------
FS = 125                       # Hz, matches every other patient
HEART_RATE = 75                # bpm -> 0.8 s cycle -> 100 samples/beat exactly
BEATS = 20                     # 20 beats = 16 s clip = 2000 samples
SAMPLES_PER_BEAT = int(round(FS * 60.0 / HEART_RATE))
N = BEATS * SAMPLES_PER_BEAT

RESP_RATE = 15                 # breaths/min
RESP_AMP = {                   # mmHg of respiratory swing, per signal
    "svc": 0.8, "ra": 0.8, "rv": 0.5, "pa": 1.2, "wedge": 1.0, "abp": 1.5,
}

# --- Clinical landmarks ------------------------------------------------------
# (phase 0-1 through the cardiac cycle, pressure mmHg).  ECG reference within
# the same cycle: P ~0.10, QRS ~0.20, T ~0.42.

LANDMARKS = {
    # RA: a > v (normal).  a follows P; c wave interrupts the x descent just
    # after QRS; v peaks in late systole; y descent when the tricuspid opens.
    "ra": [
        (0.00, 3.5),   # diastasis
        (0.13, 6.5),   # a wave  (atrial contraction, follows P)
        (0.20, 4.0),   # x descent begins
        (0.25, 5.0),   # c wave  (tricuspid bulge, just after QRS)
        (0.36, 2.0),   # x' descent nadir (atrial relaxation)
        (0.55, 5.5),   # v wave  (atrial filling vs closed TV)  -- a > v
        (0.70, 1.8),   # y descent nadir (TV opens)
        (0.85, 3.2),   # diastasis
    ],
    # SVC: same morphology, slightly damped and 1 mmHg lower.
    "svc": [
        (0.00, 2.8), (0.13, 5.2), (0.20, 3.4), (0.25, 4.1),
        (0.36, 1.8), (0.55, 4.5), (0.70, 1.6), (0.85, 2.6),
    ],
    # RV 25/4 with RVEDP ~5.  Signature features: sharp upstroke, NO dicrotic
    # notch, near-zero early diastole, slow diastolic UPSLOPE to end-diastole.
    "rv": [
        (0.00, 3.0),
        (0.13, 4.5),   # atrial kick bump in RV diastole
        (0.19, 5.0),   # RVEDP -- end diastole, just before upstroke
        (0.22, 12.0),
        (0.28, 24.0),
        (0.33, 25.0),  # systolic peak -- SAME as PA (key teaching point)
        (0.40, 23.0),
        (0.46, 12.0),
        (0.50, 3.0),   # rapid pressure fall
        (0.55, 1.0),   # early diastolic dip toward zero
        (0.68, 2.0),   # slow diastolic upslope begins
        (0.85, 2.7),
    ],
    # PA 25/10.  Same systolic peak as RV; diastolic STEPS UP from 4 to 10;
    # true dicrotic notch (dip then rebound) at pulmonic valve closure.
    "pa": [
        (0.00, 11.5),
        (0.19, 10.0),  # PA diastolic nadir
        (0.22, 15.0),
        (0.28, 24.0),
        (0.32, 25.0),  # systolic peak
        (0.40, 22.0),
        (0.46, 17.0),
        (0.49, 15.5),  # incisura -- the notch itself
        (0.52, 16.5),  # dicrotic rebound
        (0.60, 15.0),
        (0.75, 13.0),
        (0.90, 12.0),
    ],
    # PCWP mean ~10, v > a (reflects LA), and both waves are DELAYED because
    # the signal travels retrograde through the pulmonary bed: a lands after
    # the QRS, v lands after the T wave.  Mean wedge ~= PA diastolic.
    "wedge": [
        (0.00, 9.0),
        (0.10, 8.5),
        (0.28, 11.5),  # a wave -- delayed, appears after QRS
        (0.40, 8.5),   # x descent
        (0.68, 12.5),  # v wave -- delayed, appears after T   -- v > a
        (0.85, 8.8),   # y descent
        (0.95, 8.8),
    ],
    # ABP 120/80, mean ~93, clear dicrotic notch at aortic valve closure.
    "abp": [
        (0.00, 82.0),
        (0.20, 80.0),  # diastolic nadir
        (0.24, 100.0),
        (0.30, 118.0),
        (0.33, 120.0), # systolic peak
        (0.42, 110.0),
        (0.48, 98.0),
        (0.50, 95.0),  # incisura
        (0.53, 99.0),  # dicrotic rebound
        (0.62, 93.0),
        (0.80, 86.0),
    ],
}


def periodic_beat(points, n=SAMPLES_PER_BEAT):
    """PCHIP-interpolate one cardiac cycle from landmark points, seamlessly
    periodic (the end of the beat joins the start with no kink)."""
    pts = sorted(points)
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    # Wrap one cycle on each side so the interpolator sees true periodicity.
    xx = np.concatenate([x - 1.0, x, x + 1.0])
    yy = np.concatenate([y, y, y])
    f = PchipInterpolator(xx, yy)
    return f(np.linspace(0.0, 1.0, n, endpoint=False))


def ecg_beat(n=SAMPLES_PER_BEAT):
    """Clean textbook lead II: P, QRS, T. Scaled so R ~0.42 mV, which fills
    the simulator's fixed -0.5..0.5 mV canvas without clipping."""
    t = np.linspace(0.0, 1.0, n, endpoint=False)
    g = lambda c, w, a: a * np.exp(-((t - c) ** 2) / (2 * w ** 2))
    wave = (
        g(0.100, 0.022, 0.115)    # P
        - g(0.185, 0.006, 0.045)  # Q
        + g(0.200, 0.008, 0.420)  # R
        - g(0.222, 0.009, 0.110)  # S
        + g(0.420, 0.040, 0.170)  # T
    )
    return wave - np.median(wave)


def tile(beat, beats=BEATS):
    return np.tile(beat, beats)


def respiratory(n, amp):
    """Slow, regular respiratory swing. Kept small on purpose: enough that
    learners see PAOP must be read at end-expiration, small enough that the
    textbook morphology stays obvious."""
    if amp <= 0:
        return np.zeros(n)
    t = np.arange(n) / FS
    return amp * np.sin(2 * np.pi * (RESP_RATE / 60.0) * t)


def build_signal(key):
    sig = tile(periodic_beat(LANDMARKS[key]))
    return sig + respiratory(len(sig), RESP_AMP.get(key, 0.0))


# --- Output ------------------------------------------------------------------
CHAMBERS = ["svc", "ra", "rv", "pa", "wedge"]
CHAMBER_LABEL = {"svc": "Superior Vena Cava", "ra": "Right Atrium",
                 "rv": "Right Ventricle", "pa": "Pulmonary Artery",
                 "wedge": "Pulmonary Capillary Wedge"}


VIGNETTE = """
REFERENCE CASE — Idealized Waveforms

This is not a real patient. Every waveform here is generated from a
landmark model so that each teaching feature appears exactly where the
textbook draws it. Use this case first, before the real MIMIC-III
patients, to learn what each chamber is supposed to look like.

Normal hemodynamics, HR 75, normal sinus rhythm.

  SVC     mean 3        damped a / c / v
  RA      mean 4        a > v; x and y descents visible
  RV      25/5          sharp upstroke, no dicrotic notch,
                        diastolic pressure rises toward end-diastole
  PA      25/10         SAME systolic as RV; diastolic STEPS UP;
                        dicrotic notch at pulmonic valve closure
  PCWP    mean 10       loses systolic pulsatility; v > a;
                        waves delayed (retrograde transmission);
                        mean PCWP is approximately PA diastolic

Three things to carry into the real patients:

1. RV and PA systolic pressures are the same. The diastolic step-up
   and the appearance of a dicrotic notch tell you the tip crossed
   the pulmonic valve — not a change in the peak.
2. The a wave follows the P wave. In the wedge position the a and v
   waves arrive late, because the pressure is transmitted backward
   through the pulmonary vasculature.
3. a > v in the right atrium, v > a in the wedge.
"""


def write_csv(path, header, values):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(header + "\n")
        for v in values:
            f.write(f"{v:.4f}\n")


def write_metadata(folder, case_name, description, signals):
    meta = {
        "case_name": case_name,
        "description": description,
        "sampling_rate_hz": FS,
        "num_samples": N,
        "source": {
            "database": "synthetic",
            "method": "landmark model + PCHIP interpolation",
            "heart_rate_bpm": HEART_RATE,
            "respiratory_rate_bpm": RESP_RATE,
            "generator": "setup_ideal_patient.py",
        },
        "signals": [
            {"signal_name": s, "units": u, "num_samples": N, "file": f"{s}.csv"}
            for s, u in signals
        ],
    }
    with open(os.path.join(folder, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)


def build(out_root):
    ecg = tile(ecg_beat())
    abp = build_signal("abp")

    for ch in CHAMBERS:
        pap = build_signal(ch)
        d = os.path.join(out_root, f"pap_{ch}")
        write_csv(os.path.join(d, "PAP.csv"), "PAP (mmHg)", pap)
        write_metadata(d, f"pap_{ch}",
                       f"Idealized PAP waveform - {CHAMBER_LABEL[ch]}",
                       [("PAP", "mmHg")])

        # Per-chamber background so ECG stays phase-locked to the PAP clip
        # when the chamber switches (all clips are the same length).
        bg = os.path.join(out_root, f"background_{ch}")
        write_csv(os.path.join(bg, "II.csv"), "II (mV)", ecg)
        write_csv(os.path.join(bg, "ABP.csv"), "ABP (mmHg)", abp)
        write_metadata(bg, f"background_{ch}",
                       f"Idealized ECG + ABP - {CHAMBER_LABEL[ch]}",
                       [("II", "mV"), ("ABP", "mmHg")])

    d = os.path.join(out_root, "background")
    write_csv(os.path.join(d, "II.csv"), "II (mV)", ecg)
    write_csv(os.path.join(d, "ABP.csv"), "ABP (mmHg)", abp)
    write_metadata(d, "background", "Idealized ECG + ABP - shared default",
                   [("II", "mV"), ("ABP", "mmHg")])

    with open(os.path.join(out_root, "patient.json"), "w") as f:
        json.dump({
            "nickname": "Ida",
            "summary_fictional": "Teaching case: idealized textbook PAC float "
                                 "with normal hemodynamics",
            "waveform_source": "Synthetic (landmark model) - not patient data",
            # Every clip here is a whole number of identical beats, all the same
            # length and phase-aligned, so the simulator can carry the sample
            # index across a chamber switch instead of restarting the beat.
            # Keeps the ECG continuous; the PAP morphology changes mid-beat,
            # the way it does when the tip actually crosses a valve.
            "continuous_phase": True,
        }, f, indent=2)

    with open(os.path.join(out_root, "vignette.txt"), "w", encoding="utf-8") as f:
        f.write(VIGNETTE.strip() + "\n")

    print(f"Built ideal patient in {out_root}")
    print(f"  {SAMPLES_PER_BEAT} samples/beat x {BEATS} beats = {N} samples "
          f"({N / FS:.0f} s) at {FS} Hz")
    for ch in CHAMBERS:
        s = build_signal(ch)
        print(f"  {ch:>5}: {s.min():6.1f} / {s.max():6.1f}  mean {s.mean():5.1f} mmHg")


# =============================================================================
# Installer
# =============================================================================
PATIENT_ORDER_LINE = '    "ideal",             # Ida - synthetic textbook reference case (start here)\n'


def patch_simulator(path="pac_simulator.py"):
    """Register the ideal patient in pac_simulator.py. Idempotent."""
    if not os.path.isfile(path):
        print(f"  !! {path} not found -- run this from the repo root.")
        return False

    src = open(path, encoding="utf-8").read()
    original = src
    changed = []

    # 1. Make it the patient the app boots into.
    if 'DEFAULT_PATIENT = "ideal"' in src:
        print("  - DEFAULT_PATIENT already set to 'ideal' (skipped)")
    else:
        import re
        new, n = re.subn(r'DEFAULT_PATIENT = "[^"]*"',
                         'DEFAULT_PATIENT = "ideal"', src, count=1)
        if n:
            src = new
            changed.append("DEFAULT_PATIENT -> 'ideal'")
        else:
            print("  !! could not find DEFAULT_PATIENT -- edit it by hand")

    # 2. Put it first in the patient tab order.
    if '"ideal",' in src.split("PATIENT_ORDER = [")[-1].split("]")[0]:
        print("  - 'ideal' already first in PATIENT_ORDER (skipped)")
    elif "PATIENT_ORDER = [\n" in src:
        src = src.replace("PATIENT_ORDER = [\n",
                          "PATIENT_ORDER = [\n" + PATIENT_ORDER_LINE, 1)
        changed.append("PATIENT_ORDER -> 'ideal' first")
    else:
        print("  !! could not find PATIENT_ORDER -- edit it by hand")

    if src != original:
        backup = path + ".bak"
        open(backup, "w", encoding="utf-8").write(original)
        open(path, "w", encoding="utf-8").write(src)
        for c in changed:
            print(f"  - {c}")
        print(f"  - original saved as {backup}")
    return True


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = here if os.path.isfile(os.path.join(here, "pac_simulator.py")) else os.getcwd()
    os.chdir(root)

    if not os.path.isfile("pac_simulator.py"):
        print("ERROR: pac_simulator.py not found.")
        print("Put this script in your PAC-Simulator folder and run it there.")
        raise SystemExit(1)

    print("\n[1/2] Generating waveform_data/ideal/ ...")
    build(os.path.join("waveform_data", "ideal"))

    print("\n[2/2] Registering the patient in pac_simulator.py ...")
    patch_simulator("pac_simulator.py")

    print("""
Done. Try it:

    python pac_simulator.py --windowed

Ida should be the first patient tab and the one it opens on.
Use + and - to advance and withdraw the catheter.

To commit:

    git checkout -b ideal-patient
    git add waveform_data/ideal setup_ideal_patient.py pac_simulator.py
    git commit -m "Add Ida: synthetic textbook reference patient"
    git push -u origin ideal-patient

(pac_simulator.py.bak is a local backup -- do not commit it.)
""")


if __name__ == "__main__":
    main()
