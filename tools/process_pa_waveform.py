"""Process the PA waveform from a single WebPlotDigitizer trace.

Replaces the older two-part path (stitch_pa_waveform.py), which joined two
overlapping traces with a crossfade and had to flip the second one. The
original PA trace accidentally included the stretch where the catheter falls
back into the RV — that section is unlabeled on the source slide — so PA was
re-traced as one continuous run of true PA beats and no stitching is needed.

Input:  wpd_pa_raw.csv   (WebPlotDigitizer CSV export, columns x,y)
Output: wpd_pa_resampled.csv  + wpd_pa_preview.png

Then run build_powerpoint_patient.py to copy it into the patient folder.
"""
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

RAW_CSV = "wpd_pa_raw.csv"

# Seconds per X unit. NOTE this differs from the older traces: they were
# calibrated against the SMALL grid squares (0.04 s at 25 mm/s), this one
# against the LARGE squares (0.2 s). Confirmed by heart rate — 0.2 gives
# ~93 bpm, matching the 92.3 bpm of the previous PA trace off the same
# recording; 0.04 would imply an impossible 465 bpm.
GRID_TO_SEC = 0.2
SAMPLE_RATE = 125

INVERT_Y = False              # set True if the trace comes out upside down

# --- Pressure calibration ---------------------------------------------------
# ZERO_REF: the FIRST clicked point is the zero-pressure baseline, not data.
# Click it clearly to the LEFT of where the waveform starts, so it stays first
# after sorting by time. It gets removed from the trace and used as 0 mmHg.
# This is what makes diastolic a measured height above zero rather than a
# hardcoded constant.
ZERO_REF = True

# Scale, i.e. how many mmHg one unit of traced height is worth:
#   - If the slide fixes a pressure anywhere (a labelled axis, a scale mark, or
#     gridlines of known mmHg spacing), set MMHG_PER_UNIT and the trace is
#     fully calibrated — both systolic and diastolic come out as real numbers.
#   - Otherwise leave it None: the peak is pinned to SYSTOLIC_TARGET and
#     diastolic falls out of the true geometry relative to zero.
MMHG_PER_UNIT = None
SYSTOLIC_TARGET = 28

# Trim the resampled clip to a whole number of beats, cut at diastolic nadirs,
# so it loops without a visible step. Also drops any lead-in/lead-out fragment
# — including a stray first click that isn't a real nadir (a hand trace often
# starts a little low, which would otherwise be read as the PA diastolic).
TRIM_TO_WHOLE_BEATS = True

# Legacy path, used only when ZERO_REF is False: min-max stretch onto this
# range. Outlier-sensitive — a single stray click at an extreme rescales the
# whole waveform, which is how the old trace went wrong.
PRESSURE_MIN, PRESSURE_MAX = 10, 28

# --- Load raw data ---
# WebPlotDigitizer exports without a header row; tolerate either form.
_probe = pd.read_csv(RAW_CSV, header=None, nrows=1)
_has_header = not np.issubdtype(_probe.dtypes.iloc[0], np.number)
df = (pd.read_csv(RAW_CSV) if _has_header
      else pd.read_csv(RAW_CSV, header=None, names=['x', 'y']))

t = df['x'].values * GRID_TO_SEC
y = df['y'].values
if INVERT_Y:
    y = -y

# Pull off the zero-pressure reference click before it can be mistaken for data.
# This MUST happen in click order, before sorting: the reference is the first
# point *clicked*, and hand placement means a nearby trace point can easily sit
# a hair further left, which would make sorting hand back the wrong point.
y_zero = None
if ZERO_REF:
    y_zero, t_zero = y[0], t[0]
    t, y = t[1:], y[1:]
    print(f"Zero reference: first click at t={t_zero:.2f}s, y={y_zero:.3f}")
    below = int((y < y_zero).sum())
    if below:
        print(f"  !! WARNING: {below} traced points fall BELOW the zero "
              f"reference. Either the zero click landed too high, or the "
              f"trace really does dip below baseline.")

# Sort by time
idx = np.argsort(t)
t, y = t[idx], y[idx]

# Average near-duplicate time points (within 3 ms) — hand tracing produces
# clusters of clicks that would otherwise make the interpolator misbehave.
clean_t, clean_y = [], []
i = 0
while i < len(t):
    j = i + 1
    while j < len(t) and (t[j] - t[i]) < 0.003:
        j += 1
    clean_t.append(np.mean(t[i:j]))
    clean_y.append(np.mean(y[i:j]))
    i = j
t_raw = np.array(clean_t)
y_raw = np.array(clean_y)

print(f"Points: {len(df)} raw -> {len(t_raw)} after dedup")
print(f"Time range: {t_raw[0]:.2f} to {t_raw[-1]:.2f} s "
      f"({t_raw[-1] - t_raw[0]:.1f}s traced)")
print(f"Raw Y range: {y_raw.min():.2f} to {y_raw.max():.2f}")

# --- Scale to pressure ---
if ZERO_REF:
    height = y_raw - y_zero          # traced height above the zero baseline
    if MMHG_PER_UNIT is not None:
        scale = MMHG_PER_UNIT
        print(f"\nCalibrated: {scale:.4f} mmHg per traced unit (from the slide)")
    else:
        scale = SYSTOLIC_TARGET / height.max()
        print(f"\nPeak pinned to {SYSTOLIC_TARGET} mmHg "
              f"-> {scale:.4f} mmHg per traced unit")
    p_scaled = height * scale
    print(f"  => diastolic falls out at {p_scaled.min():.1f} mmHg "
          f"(measured above zero, not assumed)")
else:
    y_norm = (y_raw - y_raw.min()) / (y_raw.max() - y_raw.min())
    p_scaled = y_norm * (PRESSURE_MAX - PRESSURE_MIN) + PRESSURE_MIN
    print(f"\nMin-max stretched onto {PRESSURE_MIN}-{PRESSURE_MAX} mmHg")

# --- Resample to 125 Hz using PCHIP (shape-preserving, no overshoot) ---
t_uniform = np.arange(t_raw[0], t_raw[-1], 1 / SAMPLE_RATE)
p_resampled = PchipInterpolator(t_raw, p_scaled)(t_uniform)
p_resampled = np.clip(p_resampled, 5, 35)

t_plot = t_uniform - t_uniform[0]
total_samples = len(p_resampled)
print(f"\nResampled: {total_samples} samples, {total_samples / SAMPLE_RATE:.1f}s")
print(f"Pressure: {p_resampled.min():.1f} to {p_resampled.max():.1f} mmHg "
      f"(mean {p_resampled.mean():.1f})")

# --- Trim to whole beats so the clip loops cleanly ---
if TRIM_TO_WHOLE_BEATS:
    pk, _ = find_peaks(p_resampled, prominence=3,
                       distance=int(0.35 * SAMPLE_RATE))
    # Diastolic nadir preceding each systolic peak = a beat boundary.
    nadirs = []
    for k in pk:
        lo = max(0, k - int(0.6 * SAMPLE_RATE))
        seg = p_resampled[lo:k]
        if len(seg):
            nadirs.append(lo + int(seg.argmin()))
    nadirs = sorted(set(nadirs))

    if len(nadirs) >= 2:
        # Reject boundaries that aren't really diastolic — a stray click at the
        # start of a hand trace sits well below the true nadirs. Anything more
        # than a third of a pulse pressure off the typical nadir is not one.
        vals = p_resampled[nadirs]
        median_nadir = float(np.median(vals))
        tol = 0.35 * (p_resampled.max() - median_nadir)
        keep = [n for n, v in zip(nadirs, vals)
                if abs(v - median_nadir) <= tol]
        dropped = len(nadirs) - len(keep)
        if dropped:
            bad = ", ".join(f"{v:.1f}" for v in vals
                            if abs(v - median_nadir) > tol)
            print(f"\nRejected {dropped} beat boundary/-ies at {bad} mmHg "
                  f"(typical nadir {median_nadir:.1f}) — not real diastole.")

        if len(keep) >= 2:
            a, b = keep[0], keep[-1]
            before = len(p_resampled)
            p_resampled = p_resampled[a:b]
            t_plot = np.arange(len(p_resampled)) / SAMPLE_RATE
            print(f"Trimmed to whole beats: {before} -> {len(p_resampled)} "
                  f"samples ({len(p_resampled) / SAMPLE_RATE:.2f}s, "
                  f"{len(keep) - 1} beats)")
            print(f"  loop seam: |{p_resampled[0]:.1f} - {p_resampled[-1]:.1f}|"
                  f" = {abs(p_resampled[0] - p_resampled[-1]):.2f} mmHg")
            print(f"  final range: {p_resampled.min():.1f} - "
                  f"{p_resampled.max():.1f} mmHg "
                  f"(mean {p_resampled.mean():.1f})")

# --- Sanity checks: is this actually PA, and is it all PA? ---
peaks, _ = find_peaks(p_resampled, prominence=3,
                      distance=int(0.35 * SAMPLE_RATE))
if len(peaks) > 1:
    hr = 60 / np.mean(np.diff(peaks) / SAMPLE_RATE)
    print(f"Detected {len(peaks)} beats, HR ~{hr:.0f} bpm")

    # Per-beat diastolic minimum. On a true PA run these sit near PA diastolic
    # and stay flat; a stretch of RV shows up as beats whose diastole drops
    # toward zero. This is the check for the problem being re-traced out.
    troughs = [p_resampled[peaks[k]:peaks[k + 1]].min()
               for k in range(len(peaks) - 1)]
    if troughs:
        lo, hi = min(troughs), max(troughs)
        print(f"Per-beat diastolic: {lo:.1f} to {hi:.1f} mmHg "
              f"(spread {hi - lo:.1f})")
        if hi - lo > 4:
            print("  !! WARNING: diastolic varies a lot between beats. On a "
                  "clean PA run it should be nearly flat — a run of much "
                  "lower beats suggests RV is still included.")
        beat_str = "  ".join(f"{v:.0f}" for v in troughs)
        print(f"  beat-by-beat: {beat_str}")

# --- Save ---
pd.DataFrame({"PAP": np.round(p_resampled, 2)}).to_csv(
    "wpd_pa_resampled.csv", index=False)
print("\nSaved wpd_pa_resampled.csv")

# --- Preview plot ---
fig, ax = plt.subplots(figsize=(14, 4), facecolor='black')
ax.set_facecolor('black')
ax.plot(t_plot, p_resampled, color='yellow', linewidth=1.0)
if len(peaks) > 1:
    ax.plot(t_plot[peaks], p_resampled[peaks], 'r.', markersize=6)
ax.set_title('Digitized PA Waveform (re-traced, single pass)',
             color='white', fontsize=14)
ax.set_xlabel('Time (seconds)', color='white')
ax.set_ylabel('Pressure (mmHg)', color='white')
ax.set_xlim(0, t_plot[-1])
ax.set_ylim(0, 35)
if ZERO_REF:
    ax.axhline(y=0, color='white', linestyle='-', alpha=0.5,
               label='zero baseline (clicked)')
    ax.axhline(y=p_resampled.min(), color='cyan', linestyle='--', alpha=0.4,
               label=f'diastolic {p_resampled.min():.0f} (measured)')
    ax.axhline(y=p_resampled.max(), color='cyan', linestyle=':', alpha=0.4,
               label=f'systolic {p_resampled.max():.0f}'
                     + ('' if MMHG_PER_UNIT else ' (pinned)'))
else:
    ax.axhline(y=PRESSURE_MIN, color='cyan', linestyle='--', alpha=0.4,
               label=f'target diastolic {PRESSURE_MIN}')
    ax.axhline(y=PRESSURE_MAX, color='cyan', linestyle=':', alpha=0.4,
               label=f'target systolic {PRESSURE_MAX}')
ax.legend(loc='lower right', facecolor='black', edgecolor='white',
          labelcolor='white')
ax.tick_params(colors='white')

plt.tight_layout()
plt.savefig('wpd_pa_preview.png', dpi=150, facecolor='black')
print("Saved wpd_pa_preview.png")
