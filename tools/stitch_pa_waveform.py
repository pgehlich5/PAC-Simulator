"""Stitch two PA waveform parts from WebPlotDigitizer with proper alignment and crossfade."""
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# --- Load raw data ---
part1 = pd.read_csv("wpd_pa_raw_part1.csv")
part2 = pd.read_csv("wpd_pa_raw_part2.csv")

GRID_TO_SEC = 0.04
SAMPLE_RATE = 125
PRESSURE_MIN, PRESSURE_MAX = 10, 28  # PA pressure range in mmHg

def prepare_part(df, invert_y=False):
    """Convert raw WPD data to time (s) and pressure (mmHg)."""
    t = df['x'].values * GRID_TO_SEC
    y = df['y'].values
    if invert_y:
        y = -y
    # Normalize to 0-1, then scale to pressure range
    y_norm = (y - y.min()) / (y.max() - y.min())
    p = y_norm * (PRESSURE_MAX - PRESSURE_MIN) + PRESSURE_MIN
    # Sort by time
    idx = np.argsort(t)
    t, p = t[idx], p[idx]
    # Average duplicate/near-duplicate time points (within 3ms)
    clean_t, clean_p = [], []
    i = 0
    while i < len(t):
        j = i + 1
        while j < len(t) and (t[j] - t[i]) < 0.003:
            j += 1
        clean_t.append(np.mean(t[i:j]))
        clean_p.append(np.mean(p[i:j]))
        i = j
    return np.array(clean_t), np.array(clean_p)

def resample(t, p, sr=SAMPLE_RATE):
    """Resample using PCHIP (no overshoot) at given sample rate."""
    t_uniform = np.arange(t[0], t[-1], 1/sr)
    interp = PchipInterpolator(t, p)
    return t_uniform, interp(t_uniform)

# --- Process each part ---
t1, p1 = prepare_part(part1, invert_y=False)
t2, p2 = prepare_part(part2, invert_y=True)

t1u, p1r = resample(t1, p1)
t2u, p2r = resample(t2, p2)

print(f"Part 1: {len(p1r)} samples, {p1r.min():.1f}-{p1r.max():.1f} mmHg")
print(f"Part 2: {len(p2r)} samples, {p2r.min():.1f}-{p2r.max():.1f} mmHg")

# --- Level alignment using overlap means ---
overlap_samples = int(0.5 * SAMPLE_RATE)
tail1_mean = np.mean(p1r[-overlap_samples:])
head2_mean = np.mean(p2r[:overlap_samples])
offset = tail1_mean - head2_mean
print(f"Level offset: {offset:.2f} mmHg (Part 1 tail={tail1_mean:.1f}, Part 2 head={head2_mean:.1f})")

p2_aligned = p2r + offset

# --- Crossfade at the join (0.3s window) ---
fade_len = int(0.3 * SAMPLE_RATE)

body1 = p1r[:-fade_len]
fade_from = p1r[-fade_len:]
fade_to = p2_aligned[:fade_len]
body2 = p2_aligned[fade_len:]

alpha = np.linspace(1, 0, fade_len)
transition = fade_from * alpha + fade_to * (1 - alpha)

stitched = np.concatenate([body1, transition, body2])
stitched = np.clip(stitched, 5, 35)

# Create time axis from 0
total_samples = len(stitched)
t_final = np.arange(total_samples) / SAMPLE_RATE
duration = total_samples / SAMPLE_RATE

print(f"Stitched: {total_samples} samples, {duration:.1f}s, {stitched.min():.1f}-{stitched.max():.1f} mmHg")

# Count beats
peaks, _ = find_peaks(stitched, height=22, distance=int(0.4 * SAMPLE_RATE))
if len(peaks) > 1:
    hr = 60 / np.mean(np.diff(peaks) / SAMPLE_RATE)
    print(f"Detected {len(peaks)} beats, HR ~{hr:.0f} bpm")

# --- Save ---
pd.DataFrame({"PAP": np.round(stitched, 2)}).to_csv("wpd_pa_resampled.csv", index=False)
print("Saved wpd_pa_resampled.csv")

# --- Preview plot ---
fig, axes = plt.subplots(2, 1, figsize=(14, 6), facecolor='black')

stitch_time = len(body1) / SAMPLE_RATE

ax = axes[0]
ax.set_facecolor('black')
ax.plot(t_final, stitched, color='yellow', linewidth=0.8)
ax.set_title('Digitized PA Waveform (stitched, crossfaded)', color='white')
ax.set_ylabel('Pressure (mmHg)', color='white')
ax.set_xlim(0, t_final[-1])
ax.set_ylim(5, 35)
ax.axvspan(stitch_time, stitch_time + fade_len/SAMPLE_RATE, alpha=0.3, color='cyan', label='Crossfade zone')
ax.legend(loc='upper right', facecolor='black', edgecolor='white', labelcolor='white')
ax.tick_params(colors='white')

ax2 = axes[1]
ax2.set_facecolor('black')
zoom_start = max(0, stitch_time - 2)
zoom_end = min(t_final[-1], stitch_time + 2)
mask = (t_final >= zoom_start) & (t_final <= zoom_end)
ax2.plot(t_final[mask], stitched[mask], color='yellow', linewidth=1.2)
ax2.axvspan(stitch_time, stitch_time + fade_len/SAMPLE_RATE, alpha=0.3, color='cyan')
ax2.set_title('Zoom: stitch region', color='white')
ax2.set_xlabel('Time (seconds)', color='white')
ax2.set_ylabel('Pressure (mmHg)', color='white')
ax2.set_ylim(5, 35)
ax2.tick_params(colors='white')

plt.tight_layout()
plt.savefig('wpd_pa_preview.png', dpi=150, facecolor='black')
print("Saved wpd_pa_preview.png")
