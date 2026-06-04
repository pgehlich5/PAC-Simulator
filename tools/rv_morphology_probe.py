#!/usr/bin/env python3
"""
RV waveform morphology probe.

Goal: detect a Right Ventricle pressure waveform on a PAP channel using
*morphology* (shape), not absolute pressure thresholds. An RV trace on a PAP
line is highly specific to a PAC float, so this is the basis for finding new
PAC-advancement patients in MIMIC-III.

Why morphology, not numbers: a "diastolic near zero" threshold cleanly
separates RV from PA for some patients (p003914) but FAILS for others
(herbert, whose RV diastolic ~14 ≈ his PA ~17). The shape, however, differs
regardless of absolute pressure:

  RV : after the systolic peak, pressure drops fast to an EARLY nadir, then
       RAMPS UP through diastole (ventricular filling). No dicrotic notch.
  PA : after the systolic peak there is a dicrotic notch, then a runoff
       DECLINE to a LATE nadir right before the next upstroke.

So two shape features discriminate them, independent of absolute pressure:
  1. nadir position within the beat   (early -> RV, late -> PA)
  2. late-diastolic slope sign        (rising -> RV, falling -> PA)

This script validates the detector on the four labeled clips we already own
(herbert + p003914, RV and PA), with RA/wedge as extra negative controls.
No downloads — pure local validation before pointing it at the database.

Usage:
  python rv_morphology_probe.py
"""

import csv
import glob
import os

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FS = 125


def load_pap_csv(folder):
    """Load the PAP signal CSV from a chamber folder into a numpy array."""
    files = glob.glob(os.path.join(folder, "*.csv"))
    pap = [f for f in files if os.path.basename(f).upper().startswith("PAP")]
    path = (pap or files)[0]
    values = []
    with open(path, "r") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            try:
                values.append(float(row[0]))
            except (ValueError, IndexError):
                pass
    return np.asarray(values, dtype=float)


def _smooth(x, w=5):
    """Light moving-average smoothing to stabilize peak/nadir detection."""
    if w < 2:
        return x
    kernel = np.ones(w) / w
    return np.convolve(x, kernel, mode="same")


def find_systolic_peaks(x, fs, max_bpm=200):
    """Return indices of systolic peaks via thresholded local maxima with a
    refractory minimum spacing. Crude but adequate for clean teaching clips."""
    xs = _smooth(x)
    thr = np.percentile(xs, 60)
    min_dist = max(1, int(fs * 60.0 / max_bpm))
    peaks = []
    for i in range(1, len(xs) - 1):
        if xs[i] >= xs[i - 1] and xs[i] > xs[i + 1] and xs[i] > thr:
            if not peaks or (i - peaks[-1]) >= min_dist:
                peaks.append(i)
            elif xs[i] > xs[peaks[-1]]:
                peaks[-1] = i
    return np.array(peaks)


def beat_features(x, fs):
    """Extract per-beat morphology features over a PAP signal.

    Each "beat" is the interval between two consecutive systolic peaks; the
    diastole we analyze lives inside that interval. Returns a list of dicts.
    """
    peaks = find_systolic_peaks(x, fs)
    feats = []
    for k in range(len(peaks) - 1):
        a, b = peaks[k], peaks[k + 1]
        seg = x[a:b + 1]
        L = len(seg)
        if L < 8:
            continue

        nadir_idx = int(np.argmin(seg))
        nadir_frac = nadir_idx / L                # early(RV) vs late(PA)
        systolic = float(max(x[a], x[b]))
        diastolic = float(seg.min())
        pp = systolic - diastolic
        if pp < 3:                                # too flat / not a real beat
            continue

        # Late-diastolic slope over the 40%-90% portion of the beat.
        lo, hi = int(0.40 * L), int(0.90 * L)
        if hi - lo >= 3:
            tt = np.arange(lo, hi)
            slope_per_sample = float(np.polyfit(tt, seg[lo:hi], 1)[0])
        else:
            slope_per_sample = 0.0
        slope_per_s = slope_per_sample * fs       # mmHg/s, sign is what matters

        feats.append({
            "nadir_frac": nadir_frac,
            "slope_per_s": slope_per_s,
            "systolic": systolic,
            "diastolic": diastolic,
            "pp": pp,
        })
    return feats


def rv_likeness(feats):
    """Aggregate per-beat features into a single RV-likeness score in [0,1].

    Built from shape only (nadir position + diastolic slope sign), so it does
    not depend on absolute pressure. >0.5 leans RV, <0.5 leans PA.
    """
    if not feats:
        return None
    nadir = np.median([f["nadir_frac"] for f in feats])
    slope = np.median([f["slope_per_s"] for f in feats])

    # Early nadir -> RV. Map frac 0.0->1.0 (RV) and 0.6+->0.0 (PA).
    nadir_comp = float(np.clip(1.0 - nadir / 0.6, 0.0, 1.0))
    # Rising diastole -> RV. Sigmoid on slope (mmHg/s); ~5 mmHg/s is decisive.
    slope_comp = 1.0 / (1.0 + np.exp(-slope / 3.0))

    score = 0.5 * nadir_comp + 0.5 * slope_comp
    return {
        "score": score,
        "median_nadir_frac": float(nadir),
        "median_slope_per_s": float(slope),
        "median_pp": float(np.median([f["pp"] for f in feats])),
        "median_diastolic": float(np.median([f["diastolic"] for f in feats])),
        "median_systolic": float(np.median([f["systolic"] for f in feats])),
        "n_beats": len(feats),
    }


# Pulse-pressure gate: RV is a ventricle, so it has a wide PP (~40+ here),
# while atrial/wedge traces are narrow (~9-13). PA is also wide, so this gate
# only rejects atrial waveforms — morphology then splits RV from PA. Fixed at
# 20 mmHg (safely between the two clusters); may need to be relative on the
# database for damped/low-output traces.
MIN_RV_PULSE_PRESSURE = 20.0


def classify(r):
    """Two-stage RV decision: magnitude gate (vs atrial) + morphology (vs PA)."""
    if r["median_pp"] < MIN_RV_PULSE_PRESSURE:
        return "PA"          # narrow PP -> atrial/wedge, not RV
    return "RV" if r["score"] >= 0.5 else "PA"


# --- Physiologic-plausibility gate -------------------------------------------
# Real RV/PA pressures live in a bounded range and the waveform is always
# moving. Corrupted / disconnected-transducer traces (like 3931528_0071, which
# railed at 37 and -3 mmHg) violate this. Reject such windows BEFORE scoring so
# clip-to-clip range can't masquerade as a wide RV pulse pressure.
PLAUSIBLE_MIN_MMHG = -10.0
PLAUSIBLE_MAX_MMHG = 120.0
MIN_PLAUSIBLE_DIASTOLIC = -2.0   # diastolic should not sit persistently negative
MAX_FLAT_FRACTION_OF_FS = 0.4    # a flat run > 0.4 s = clipping/railing


def _longest_true_run(mask):
    """Length (in samples) of the longest run of consecutive True values."""
    if not mask.any():
        return 0
    padded = np.concatenate(([False], mask, [False]))
    diff = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return int((ends - starts).max())


def physiologic_ok(w, fs):
    """True if a raw window looks like a real pressure waveform (not corrupted).

    Rejects: too much missing data, out-of-range values, or a long flat run
    (clipping/railing — a real pressure trace is never flat for long).
    """
    finite = w[np.isfinite(w)]
    if len(finite) < 0.6 * len(w):
        return False
    if finite.max() > PLAUSIBLE_MAX_MMHG or finite.min() < PLAUSIBLE_MIN_MMHG:
        return False
    flat = np.abs(np.diff(finite)) < 0.05
    if _longest_true_run(flat) > MAX_FLAT_FRACTION_OF_FS * fs:
        return False
    return True


def main():
    patients = ["herbert", "p003914"]
    # RV/PA are the real test; RA/wedge are negative controls.
    chambers = ["pap_rv", "pap_pa", "pap_ra", "pap_wedge"]
    truth = {"pap_rv": "RV", "pap_pa": "PA", "pap_ra": "PA", "pap_wedge": "PA"}

    print("=" * 78)
    print("RV MORPHOLOGY PROBE — shape-based RV vs PA, validated on labeled clips")
    print("=" * 78)
    print(f"{'patient/chamber':<22}{'beats':>6}{'nadirFrac':>11}"
          f"{'slope/s':>9}{'PP':>6}{'RVscore':>9}  verdict")
    print("-" * 78)

    results = {}
    for pt in patients:
        for ch in chambers:
            folder = os.path.join(_REPO_ROOT, "waveform_data", pt, ch)
            if not os.path.isdir(folder):
                continue
            x = load_pap_csv(folder)
            r = rv_likeness(beat_features(x, DEFAULT_FS))
            if r is None:
                print(f"{pt + '/' + ch:<22}  (no usable beats)")
                continue
            verdict = classify(r)
            ok = "OK" if verdict == truth[ch] else "<-- MISCLASSIFIED"
            results[(pt, ch)] = (r, verdict, truth[ch])
            print(f"{pt + '/' + ch:<22}{r['n_beats']:>6}"
                  f"{r['median_nadir_frac']:>11.2f}"
                  f"{r['median_slope_per_s']:>9.1f}"
                  f"{r['median_pp']:>6.0f}"
                  f"{r['score']:>9.2f}  {verdict} (true {truth[ch]}) {ok}")
        print()

    # Scorecard
    rv_scores = [v[0]["score"] for k, v in results.items() if k[1] == "pap_rv"]
    pa_scores = [v[0]["score"] for k, v in results.items() if k[1] != "pap_rv"]
    correct = sum(1 for v in results.values() if v[1] == v[2])
    print("-" * 78)
    print(f"Accuracy on labeled clips: {correct}/{len(results)}")
    if rv_scores and pa_scores:
        print(f"RV clips score range: {min(rv_scores):.2f}-{max(rv_scores):.2f}  "
              f"|  PA/RA/wedge score range: {min(pa_scores):.2f}-{max(pa_scores):.2f}")
        margin = min(rv_scores) - max(pa_scores)
        print(f"Separation margin (min RV - max non-RV): {margin:+.2f}  "
              f"{'(clean separation)' if margin > 0 else '(OVERLAP — needs work)'}")


if __name__ == "__main__":
    main()
