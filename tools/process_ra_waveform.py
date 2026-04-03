"""Process RA waveform from WebPlotDigitizer."""
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# --- Load raw data ---
df = pd.read_csv("wpd_ra_raw.csv")
x_raw, y_raw = df['x'].values, df['y'].values

GRID_TO_SEC = 0.04
SAMPLE_RATE = 125

# Sort by x
idx = np.argsort(x_raw)
x_sorted, y_sorted = x_raw[idx], y_raw[idx]

# Convert to time
t = x_sorted * GRID_TO_SEC

# Average near-duplicate time points (within 3ms)
clean_t, clean_p = [], []
i = 0
while i < len(t):
    j = i + 1
    while j < len(t) and (t[j] - t[i]) < 0.003:
        j += 1
    clean_t.append(np.mean(t[i:j]))
    clean_p.append(np.mean(y_sorted[i:j]))
    i = j
t_raw = np.array(clean_t)
p_raw = np.array(clean_p)

print(f"Points after dedup: {len(t_raw)}")
print(f"Time range: {t_raw[0]:.2f} to {t_raw[-1]:.2f} s ({t_raw[-1] - t_raw[0]:.1f}s)")
print(f"Raw Y range: {p_raw.min():.1f} to {p_raw.max():.1f}")

# RA pressure: scale raw y (0.7-8.3) to mmHg
# The raw values are already in a reasonable mmHg-like range
# but let's map to 0-8 mmHg for typical RA
RA_MIN = 0
RA_MAX = 8
p_norm = (p_raw - p_raw.min()) / (p_raw.max() - p_raw.min())
p_scaled = p_norm * (RA_MAX - RA_MIN) + RA_MIN

# --- Resample to 125 Hz using PCHIP ---
t_uniform = np.arange(t_raw[0], t_raw[-1], 1 / SAMPLE_RATE)
interp = PchipInterpolator(t_raw, p_scaled)
p_resampled = interp(t_uniform)
p_resampled = np.clip(p_resampled, 0, 15)

# Shift time to start at 0
t_plot = t_uniform - t_uniform[0]
total_samples = len(p_resampled)
duration = total_samples / SAMPLE_RATE

print(f"\nResampled: {total_samples} samples, {duration:.1f}s")
print(f"Pressure: {p_resampled.min():.1f} to {p_resampled.max():.1f} mmHg")
print(f"Mean pressure: {p_resampled.mean():.1f} mmHg")

# Detect waves
peaks, _ = find_peaks(p_resampled, prominence=0.3, distance=int(0.3 * SAMPLE_RATE))
if len(peaks) > 1:
    hr = 60 / np.mean(np.diff(peaks) / SAMPLE_RATE)
    print(f"Detected {len(peaks)} waves, rate ~{hr:.0f}/min")

# --- Save ---
pd.DataFrame({"RAP": np.round(p_resampled, 2)}).to_csv("wpd_ra_resampled.csv", index=False)
print("\nSaved wpd_ra_resampled.csv")

# --- Preview plot ---
fig, ax = plt.subplots(figsize=(14, 4), facecolor='black')
ax.set_facecolor('black')
ax.plot(t_plot, p_resampled, color='yellow', linewidth=0.8)
ax.set_title('Digitized RA Waveform', color='white', fontsize=14)
ax.set_xlabel('Time (seconds)', color='white')
ax.set_ylabel('Pressure (mmHg)', color='white')
ax.set_xlim(0, t_plot[-1])
ax.set_ylim(0, 12)
ax.axhline(y=p_resampled.mean(), color='cyan', linestyle='--', alpha=0.5,
           label=f'Mean: {p_resampled.mean():.0f} mmHg')
ax.legend(loc='upper right', facecolor='black', edgecolor='white', labelcolor='white')
ax.tick_params(colors='white')

plt.tight_layout()
plt.savefig('wpd_ra_preview.png', dpi=150, facecolor='black')
print("Saved wpd_ra_preview.png")
