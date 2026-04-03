"""Preview all 4 digitized chamber waveforms as they'd appear during PAC insertion."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SAMPLE_RATE = 125

# Load all chambers
ra = pd.read_csv("wpd_ra_resampled.csv")["RAP"].values
rv = pd.read_csv("wpd_rv_resampled.csv").iloc[:, 0].values
pa = pd.read_csv("wpd_pa_resampled.csv")["PAP"].values
wedge = pd.read_csv("wpd_wedge_resampled.csv")["PAWP"].values

chambers = [
    ("RA", ra, "0-8 mmHg"),
    ("RV", rv, "0-28 mmHg"),
    ("PA", pa, "10-28 mmHg"),
    ("PAWP", wedge, "8-18 mmHg"),
]

# --- Plot 1: Individual chambers stacked ---
fig, axes = plt.subplots(4, 1, figsize=(16, 10), facecolor='black')
fig.suptitle('Digitized PAC Waveforms — All Chambers', color='white', fontsize=16, y=0.98)

for ax, (name, data, label) in zip(axes, chambers):
    t = np.arange(len(data)) / SAMPLE_RATE
    ax.set_facecolor('black')
    ax.plot(t, data, color='#00FF00', linewidth=0.8)
    ax.set_ylabel('mmHg', color='white', fontsize=10)
    ax.set_title(f'{name}  ({label}, {len(data)} samples, {len(data)/SAMPLE_RATE:.1f}s)',
                 color='yellow', fontsize=11, loc='left')
    ax.tick_params(colors='white')
    ax.set_xlim(0, t[-1])
    # Use consistent y-axis per chamber type
    if name == "RA":
        ax.set_ylim(-1, 10)
    elif name == "RV":
        ax.set_ylim(-2, 32)
    elif name == "PA":
        ax.set_ylim(5, 35)
    elif name == "PAWP":
        ax.set_ylim(5, 25)

axes[-1].set_xlabel('Time (seconds)', color='white', fontsize=11)
plt.tight_layout(rect=[0, 0, 1, 0.96])
plt.savefig('wpd_all_chambers.png', dpi=150, facecolor='black')
print("Saved wpd_all_chambers.png")

# --- Plot 2: Continuous insertion sweep (concatenated) ---
# Simulate what the monitor shows: RA -> RV -> PA -> PAWP in one continuous trace
# Use ~3s of each chamber to keep it readable
clip_sec = 3.0
clip_samples = int(clip_sec * SAMPLE_RATE)

segments = []
labels = []
for name, data, _ in chambers:
    # Take from middle of each clip for best representative data
    mid = len(data) // 2
    start = max(0, mid - clip_samples // 2)
    end = min(len(data), start + clip_samples)
    segments.append(data[start:end])
    labels.append((name, len(segments[-1])))

combined = np.concatenate(segments)
t_combined = np.arange(len(combined)) / SAMPLE_RATE

fig2, ax2 = plt.subplots(figsize=(16, 4), facecolor='black')
ax2.set_facecolor('black')
ax2.plot(t_combined, combined, color='#00FF00', linewidth=0.8)
ax2.set_title('Simulated PAC Insertion Sweep:  RA → RV → PA → PAWP', color='white', fontsize=14)
ax2.set_xlabel('Time (seconds)', color='white')
ax2.set_ylabel('Pressure (mmHg)', color='white')
ax2.set_ylim(-2, 35)
ax2.set_xlim(0, t_combined[-1])
ax2.tick_params(colors='white')

# Add chamber labels
pos = 0
colors = ['#FFD700', '#FF6B6B', '#4ECDC4', '#45B7D1']
for i, (name, n_samples) in enumerate(labels):
    t_start = pos / SAMPLE_RATE
    t_end = (pos + n_samples) / SAMPLE_RATE
    t_mid = (t_start + t_end) / 2
    ax2.axvline(x=t_start, color='white', linestyle=':', alpha=0.4)
    ax2.text(t_mid, 33, name, color=colors[i], fontsize=13, fontweight='bold',
             ha='center', va='top')
    pos += n_samples

plt.tight_layout()
plt.savefig('wpd_insertion_sweep.png', dpi=150, facecolor='black')
print("Saved wpd_insertion_sweep.png")
