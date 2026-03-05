"""
Windowed waveform data download from PhysioNet with Streamlit caching.
"""

import wfdb
import streamlit as st


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_waveform_window(segment_name, record_dir, sampfrom, sampto):
    """Download a window of waveform data from PhysioNet.

    Downloads ALL channels in the segment (not just PAP) so the viewer
    can display all simultaneously-recorded signals.

    Args:
        segment_name: e.g., "3544749_0005"
        record_dir: e.g., "mimic3wdb-matched/1.0/p00/p000020"
        sampfrom: Starting sample index (0-based)
        sampto: Ending sample index (exclusive)

    Returns:
        dict with signal_data (numpy array), signal_names, units, fs,
        sampfrom, sampto, num_samples.
    """
    record = wfdb.rdrecord(
        segment_name,
        pn_dir=record_dir,
        sampfrom=sampfrom,
        sampto=sampto,
    )
    return {
        "signal_data": record.p_signal,
        "signal_names": record.sig_name,
        "units": record.units,
        "fs": record.fs,
        "sampfrom": sampfrom,
        "sampto": sampto,
        "num_samples": record.p_signal.shape[0],
    }


def compute_sample_range(offset_seconds, window_seconds, total_samples, fs=125.0):
    """Convert time offset + window size into sample indices.

    Clamps to valid range [0, total_samples].

    Returns:
        (sampfrom, sampto) tuple.
    """
    sampfrom = int(offset_seconds * fs)
    sampto = int((offset_seconds + window_seconds) * fs)
    sampfrom = max(0, min(sampfrom, total_samples - 1))
    sampto = max(sampfrom + 1, min(sampto, total_samples))
    return sampfrom, sampto
