"""
Signal processing utilities for computing clinical stats on waveform data.
"""

import numpy as np
from viewer.signal_config import is_pressure_signal


def compute_signal_stats(signal, fs, sig_name):
    """Compute display statistics for a single-channel signal array.

    For pressure signals: systolic, diastolic, mean.
    For all signals: min, max, mean.

    Returns dict with computed stats.
    """
    valid = signal[~np.isnan(signal)]
    if len(valid) == 0:
        return {"min": 0, "max": 0, "mean": 0}

    stats = {
        "min": float(np.min(valid)),
        "max": float(np.max(valid)),
        "mean": float(np.mean(valid)),
    }

    if is_pressure_signal(sig_name):
        sys_val, dia_val, mean_val = estimate_pressure_systolic_diastolic(valid, fs)
        stats["systolic"] = sys_val
        stats["diastolic"] = dia_val
        stats["mean"] = mean_val

    return stats


def estimate_pressure_systolic_diastolic(signal, fs):
    """Estimate systolic, diastolic, and mean for a pressure waveform.

    Uses peak detection to find systolic peaks and diastolic troughs,
    then takes the median of each set for a stable estimate.

    Falls back to simple max/min if peak detection fails.

    Returns:
        (systolic, diastolic, mean) as floats.
    """
    from scipy.signal import find_peaks

    if len(signal) < int(fs * 0.5):
        return float(np.max(signal)), float(np.min(signal)), float(np.mean(signal))

    # Peak detection: expect cardiac rate 40-200 bpm -> period 0.3-1.5s
    min_distance = int(fs * 0.3)

    try:
        peaks, _ = find_peaks(signal, distance=min_distance, prominence=2.0)
        troughs, _ = find_peaks(-signal, distance=min_distance, prominence=2.0)

        systolic = float(np.median(signal[peaks])) if len(peaks) > 2 else float(np.max(signal))
        diastolic = float(np.median(signal[troughs])) if len(troughs) > 2 else float(np.min(signal))
    except Exception:
        systolic = float(np.max(signal))
        diastolic = float(np.min(signal))

    mean_p = float(np.mean(signal))
    return systolic, diastolic, mean_p
