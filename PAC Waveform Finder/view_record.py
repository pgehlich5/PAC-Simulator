"""
Quick viewer: download and plot a specific PAP segment with all signals.
"""

import numpy as np
import wfdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RECORD_DIR = "mimic3wdb-matched/1.0/p00/p000020"
SEGMENT = "3544749_0005"

# Read first 30 seconds (125 Hz * 30 = 3750 samples)
print(f"Downloading segment {SEGMENT} (first 30s)...")
record = wfdb.rdrecord(SEGMENT, pn_dir=RECORD_DIR, sampfrom=0, sampto=3750)

print(f"  Signals: {record.sig_name}")
print(f"  Units:   {record.units}")
print(f"  Fs:      {record.fs} Hz")
print(f"  Shape:   {record.p_signal.shape}")

t = np.arange(record.p_signal.shape[0]) / record.fs

# Plot all signals stacked
n_sigs = record.n_sig
fig, axes = plt.subplots(n_sigs, 1, figsize=(16, 3 * n_sigs), sharex=True)
if n_sigs == 1:
    axes = [axes]

colors = ['#2196F3', '#4CAF50', '#FF5722', '#E91E63']

for i, (ax, sig_name, unit) in enumerate(zip(axes, record.sig_name, record.units)):
    signal = record.p_signal[:, i]
    color = colors[i % len(colors)]
    ax.plot(t, signal, linewidth=0.6, color=color)
    ax.set_ylabel(f"{sig_name}\n({unit})", fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.3)

    # Stats in corner
    valid = signal[~np.isnan(signal)]
    if len(valid) > 0:
        stats = f"min={np.min(valid):.1f}  max={np.max(valid):.1f}  mean={np.mean(valid):.1f}"
        ax.text(0.99, 0.95, stats, transform=ax.transAxes, fontsize=8,
                va='top', ha='right', fontfamily='monospace',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))

    # Highlight PAP
    if sig_name == 'PAP':
        ax.set_facecolor('#FFF3E0')

axes[-1].set_xlabel("Time (seconds)", fontsize=12)
fig.suptitle(f"Patient p000020 — Segment {SEGMENT} (first 30s)\n"
             f"Signals: {', '.join(record.sig_name)}", fontsize=13, fontweight='bold')
fig.tight_layout()

out = "pap_waveforms/p000020_all_signals_30s.png"
fig.savefig(out, dpi=150, bbox_inches='tight')
print(f"\nPlot saved to: {out}")

# Also do a zoomed 5-second view of just PAP
pap_idx = record.sig_name.index('PAP')
pap = record.p_signal[:, pap_idx]

fig2, ax2 = plt.subplots(figsize=(16, 4))
# 5 seconds = 625 samples
t5 = t[:625]
pap5 = pap[:625]
ax2.plot(t5, pap5, linewidth=1.0, color='#E91E63')
ax2.fill_between(t5, pap5, alpha=0.15, color='#E91E63')
ax2.set_xlabel("Time (seconds)", fontsize=12)
ax2.set_ylabel("PAP (mmHg)", fontsize=12)
ax2.set_title(f"PAP Waveform Detail — Patient p000020 (5 seconds)", fontsize=13, fontweight='bold')
ax2.grid(True, alpha=0.3)
ax2.set_xlim(0, 5)

out2 = "pap_waveforms/p000020_pap_detail_5s.png"
fig2.savefig(out2, dpi=150, bbox_inches='tight')
print(f"Detail plot saved to: {out2}")

# Print raw data sample
print(f"\nFirst 20 PAP samples (mmHg):")
for j in range(20):
    print(f"  t={t[j]:.3f}s  PAP={pap[j]:.1f} mmHg")
