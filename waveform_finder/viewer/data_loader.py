"""
Catalog loading and patient-centric data grouping for the waveform viewer.
"""

import json
import streamlit as st


@st.cache_data
def load_catalog(json_path="pap_records.json"):
    """Load pap_records.json and return the parsed dict."""
    with open(json_path) as f:
        return json.load(f)


@st.cache_data
def build_patient_index(_catalog_segments):
    """Group segments by patient ID.

    Returns dict mapping patient_id -> {patient_id, segments, total_segments,
    total_duration_min, signal_set}.

    Patient ID extracted from record_dir:
        "mimic3wdb-matched/1.0/p00/p000079" -> "p000079"
    """
    patients = {}
    for seg in _catalog_segments:
        parts = seg["record_dir"].rstrip("/").split("/")
        patient_id = parts[-1]

        if patient_id not in patients:
            patients[patient_id] = {
                "patient_id": patient_id,
                "segments": [],
                "total_segments": 0,
                "total_duration_min": 0.0,
                "signal_set": set(),
            }

        p = patients[patient_id]
        p["segments"].append(seg)
        p["total_segments"] += 1
        p["total_duration_min"] += seg.get("duration_min", 0)
        p["signal_set"].update(seg.get("all_signals", []))

    # Sort segments within each patient
    for p in patients.values():
        p["segments"].sort(key=lambda s: (s["record_path"], s["segment_index"]))
        # Convert set to sorted list for display
        p["signal_set"] = sorted(p["signal_set"])

    return patients


def get_patient_list(patient_index):
    """Return sorted list of patient IDs."""
    return sorted(patient_index.keys())


def get_segments_for_patient(patient_index, patient_id):
    """Return all segment dicts for a given patient."""
    return patient_index.get(patient_id, {}).get("segments", [])


def format_segment_label(seg):
    """Create a human-readable label for a segment."""
    dur = format_duration(seg.get("duration_min", 0))
    sigs = ", ".join(seg.get("all_signals", []))
    n_sigs = len(seg.get("all_signals", []))
    return f"{seg['segment_name']} ({dur}, {n_sigs} signals: {sigs})"


def format_duration(minutes):
    """Format duration as human-readable string."""
    if minutes >= 60:
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if mins == 0:
            return f"{hours}h"
        return f"{hours}h {mins}m"
    elif minutes >= 1:
        return f"{int(minutes)}m"
    else:
        secs = int(minutes * 60)
        return f"{secs}s"
