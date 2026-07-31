"""Process the RV waveform from WebPlotDigitizer.

Backfills the one missing step in the powerpoint patient's pipeline: RV had a
wpd_rv_resampled.csv in the repo but nothing that generated it, so it was the
only chamber that could not be regenerated or corrected.

The recipe here was reverse-engineered from the committed output and matches it
to within 0.25 mmHg (0.9% of range, roughly one pixel on screen); the residual
is most likely a scipy version difference. Verified: 0.04 s per grid square,
min-max onto 0-28 mmHg, clipped at 0, CubicSpline interpolation.

Input:  wpd_rv_raw.csv           (WebPlotDigitizer CSV export)
Output: wpd_rv_resampled.csv  +  wpd_rv_preview.png
Then run build_powerpoint_patient.py to copy it into the patient folder.
"""
import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline, PchipInterpolator
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

RAW_CSV = "wpd_rv_raw.csv"
GRID_TO_SEC = 0.04            # small grid squares (25 mm/s paper)
SAMPLE_RATE = 125

# Min-max stretch onto this range. As with the other digitized chambers, the
# slide has no pressure axis, so both ends are chosen rather than measured.
# 0-28 puts RV systolic level with PA systolic, which is the point the slide
# teaches. (process_pa_waveform.py uses a better zero-referenced scheme; RV
# stays on min-max so its appearance does not shift.)
RV_MIN, RV_MAX = 0, 28

# CubicSpline reproduces the committed waveform. It is NOT the repo default —
# everything digitized later uses PCHIP, because cubic overshoots between
# closely spaced hand-clicked points. Switching to "pchip" here removes that
# overshoot but moves the trace by up to 3.65 mmHg at a few peaks, so it is
# left on cubic to keep RV looking as it always has. Change deliberately.
INTERPOLATOR = "cubic"        # "cubic" (as committed) or "pchip" (no overshoot)

# --- Load raw data ---
_probe = pd.read_csv(RAW_CSV, header=None, nrows=1)
_has_header = not np.issubdtype(_probe.dtypes.iloc[0], np.number)
df = (pd.read_csv(RAW_CSV) if _has_header
      else pd.read_csv(RAW_CSV, header=None, names=['x', 'y']))

idx = np.argsort(df['x'].values)
t = df['x'].values[idx] * GRID_TO_SEC
p = df['y'].values[idx]

# Drop points that share a timestamp — the interpolator requires strictly
# increasing x.
keep = np.concatenate([[True], np.diff(t) > 0])
t, p = t[keep], p[keep]

print(f"Points: {len(df)} raw -> {len(t)} usable")
print(f"Time range: {t[0]:.2f} to {t[-1]:.2f} s ({t[-1] - t[0]:.1f}s)")
print(f"Raw Y range: {p.min():.2f} to {p.max():.2f}")

# --- Scale to pressure ---
p_norm = (p - p.min()) / (p.max() - p.min())
p_scaled = p_norm * (RV_MAX - RV_MIN) + RV_MIN

# --- Resample to 125 Hz ---
t_uniform = np.arange(t[0], t[-1], 1 / SAMPLE_RATE)
f = CubicSpline if INTERPOLATOR == "cubic" else PchipInterpolator
p_resampled = np.clip(f(t, p_scaled)(t_uniform), 0, 35)

t_plot = t_uniform - t_uniform[0]
print(f"\nResampled ({INTERPOLATOR}): {len(p_resampled)} samples, "
      f"{len(p_resampled) / SAMPLE_RATE:.2f}s")
print(f"Pressure: {p_resampled.min():.1f} to {p_resampled.max():.1f} mmHg "
      f"(mean {p_resampled.mean():.1f})")

peaks, _ = find_peaks(p_resampled, prominence=5,
                      distance=int(0.35 * SAMPLE_RATE))
if len(peaks) > 1:
    hr = 60 / np.mean(np.diff(peaks) / SAMPLE_RATE)
    print(f"Detected {len(peaks)} beats, HR ~{hr:.0f} bpm")

# Regression check against whatever is already committed, so a future edit
# that shifts the waveform says so out loud instead of doing it silently.
try:
    prev = pd.read_csv("wpd_rv_resampled.csv").iloc[:, 0].values
    if len(prev) == len(p_resampled):
        d = np.abs(np.round(p_resampled, 2) - prev)
        print(f"vs committed: max {d.max():.2f} mmHg, mean {d.mean():.2f}")
    else:
        print(f"vs committed: LENGTH CHANGED {len(prev)} -> {len(p_resampled)}")
except FileNotFoundError:
    pass

# --- Save ---
# Column stays "PAP" — build_powerpoint_patient.py reads it under that legacy
# name for every chamber.
pd.DataFrame({"PAP": np.round(p_resampled, 2)}).to_csv(
    "wpd_rv_resampled.csv", index=False)
print("\nSaved wpd_rv_resampled.csv")

# --- Preview plot ---
fig, ax = plt.subplots(figsize=(14, 4), facecolor='black')
ax.set_facecolor('black')
ax.plot(t_plot, p_resampled, color='yellow', linewidth=1.0)
if len(peaks) > 1:
    ax.plot(t_plot[peaks], p_resampled[peaks], 'r.', markersize=6)
ax.set_title('Digitized RV Waveform', color='white', fontsize=14)
ax.set_xlabel('Time (seconds)', color='white')
ax.set_ylabel('Pressure (mmHg)', color='white')
ax.set_xlim(0, t_plot[-1])
ax.set_ylim(0, 35)
ax.tick_params(colors='white')
plt.tight_layout()
plt.savefig('wpd_rv_preview.png', dpi=150, facecolor='black')
print("Saved wpd_rv_preview.png")
