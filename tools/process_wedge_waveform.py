"""Process PAWP (wedge) waveform from WebPlotDigitizer."""
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# --- Load raw data ---
df = pd.read_csv("wpd_wedge_raw.csv")
x_raw, y_raw = df['x'].values, df['y'].values

print(f"Raw points: {len(x_raw)}")
print(f"X range: {x_raw.min():.1f} to {x_raw.max():.1f}")
print(f"Y range: {y_raw.min():.1f} to {y_raw.max():.1f}")

# --- Remove stray points ---
# Main wedge data is x > 128 and roughly contiguous
# Filter out the 3 stray points at the end that are way out of sequence
# (x=209.98, 137.86, 136.89 appearing after x=264)
# Sort by x first, then remove points that are clearly duplicates/strays
idx_sorted = np.argsort(x_raw)
x_sorted = x_raw[idx_sorted]
y_sorted = y_raw[idx_sorted]

# Remove duplicate/near-duplicate x values (within 0.5 grid units)
clean_x, clean_y = [], []
i = 0
while i < len(x_sorted):
    j = i + 1
    while j < len(x_sorted) and (x_sorted[j] - x_sorted[i]) < 0.3:
        j += 1
    # Average nearby points
    clean_x.append(np.mean(x_sorted[i:j]))
    clean_y.append(np.mean(y_sorted[i:j]))
    i = j

x_clean = np.array(clean_x)
y_clean = np.array(clean_y)
print(f"After dedup: {len(x_clean)} points, X: {x_clean.min():.1f} to {x_clean.max():.1f}")

# --- Convert to time and pressure ---
GRID_TO_SEC = 0.04
SAMPLE_RATE = 125

t = x_clean * GRID_TO_SEC

# Y is inverted (more negative = higher pressure)
y_inv = -y_clean
# Normalize 0-1
y_norm = (y_inv - y_inv.min()) / (y_inv.max() - y_inv.min())

# Wedge pressure range: typically 8-18 mmHg for a normal-ish wedge
# The waveform has small pulsations on a relatively flat baseline
# From the raw Y range (~4 units span), this is a low-amplitude signal
WEDGE_MIN = 8
WEDGE_MAX = 18
p = y_norm * (WEDGE_MAX - WEDGE_MIN) + WEDGE_MIN

print(f"Time range: {t.min():.2f} to {t.max():.2f} s")
print(f"Duration: {t.max() - t.min():.1f} s")
print(f"Pressure range: {p.min():.1f} to {p.max():.1f} mmHg")

# --- Resample to 125 Hz using PCHIP ---
t_uniform = np.arange(t[0], t[-1], 1/SAMPLE_RATE)
interp = PchipInterpolator(t, p)
p_resampled = interp(t_uniform)
p_resampled = np.clip(p_resampled, 5, 25)

# Shift time to start at 0
t_final = t_uniform - t_uniform[0]
total_samples = len(p_resampled)
duration = total_samples / SAMPLE_RATE

print(f"\nResampled: {total_samples} samples, {duration:.1f}s")
print(f"Pressure: {p_resampled.min():.1f} to {p_resampled.max():.1f} mmHg")
print(f"Mean pressure: {p_resampled.mean():.1f} mmHg")

# Detect pulsations (small a/v waves)
peaks, props = find_peaks(p_resampled, prominence=0.5, distance=int(0.4 * SAMPLE_RATE))
if len(peaks) > 1:
    hr = 60 / np.mean(np.diff(peaks) / SAMPLE_RATE)
    print(f"Detected {len(peaks)} pulsations, rate ~{hr:.0f}/min")

# --- Save ---
pd.DataFrame({"PAWP": np.round(p_resampled, 2)}).to_csv("wpd_wedge_resampled.csv", index=False)
print("\nSaved wpd_wedge_resampled.csv")

# --- Preview plot ---
fig, ax = plt.subplots(figsize=(14, 4), facecolor='black')
ax.set_facecolor('black')
ax.plot(t_final, p_resampled, color='yellow', linewidth=0.8)
ax.set_title('Digitized PAWP (Wedge) Waveform', color='white', fontsize=14)
ax.set_xlabel('Time (seconds)', color='white')
ax.set_ylabel('Pressure (mmHg)', color='white')
ax.set_xlim(0, t_final[-1])
ax.set_ylim(5, 25)
ax.axhline(y=p_resampled.mean(), color='cyan', linestyle='--', alpha=0.5, label=f'Mean: {p_resampled.mean():.0f} mmHg')
ax.legend(loc='upper right', facecolor='black', edgecolor='white', labelcolor='white')
ax.tick_params(colors='white')

plt.tight_layout()
plt.savefig('wpd_wedge_preview.png', dpi=150, facecolor='black')
print("Saved wpd_wedge_preview.png")
