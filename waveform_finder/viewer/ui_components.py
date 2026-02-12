"""
Streamlit sidebar widgets for patient/segment browsing and display controls.
"""

import streamlit as st
from viewer.data_loader import (
    load_catalog,
    get_patient_list,
    get_segments_for_patient,
    format_segment_label,
    format_duration,
)


def render_sidebar():
    """Render the sidebar and return the user's current selections.

    Returns:
        dict with keys: patient_id, segment, window_seconds,
        offset_seconds, visible_signals.
    """
    st.sidebar.title("PAP Waveform Viewer")
    st.sidebar.caption("Browse MIMIC-III PAP waveforms")

    patient_index = _get_patient_index()
    patient_list = get_patient_list(patient_index)

    if not patient_list:
        st.sidebar.warning("No patients found in catalog.")
        return {"patient_id": None, "segment": None}

    # --- Patient selector ---
    st.sidebar.header("Patient")
    patient_id = st.sidebar.selectbox(
        "Select patient",
        options=patient_list,
        format_func=lambda pid: (
            f"{pid} ({patient_index[pid]['total_segments']} segments, "
            f"{format_duration(patient_index[pid]['total_duration_min'])})"
        ),
    )

    if not patient_id:
        return {"patient_id": None, "segment": None}

    # Show patient summary
    p_info = patient_index[patient_id]
    st.sidebar.caption(
        f"Signals available: {', '.join(p_info['signal_set'])}"
    )

    # --- Segment selector ---
    segments = get_segments_for_patient(patient_index, patient_id)
    st.sidebar.header("Segment")
    segment = st.sidebar.selectbox(
        "Select segment",
        options=segments,
        format_func=format_segment_label,
    )

    if not segment:
        return {"patient_id": patient_id, "segment": None}

    # --- Time controls ---
    st.sidebar.header("Time Window")

    window_options = {
        "5 seconds": 5.0,
        "10 seconds": 10.0,
        "30 seconds": 30.0,
        "1 minute": 60.0,
        "2 minutes": 120.0,
        "5 minutes": 300.0,
    }

    # Clamp options to segment duration
    total_duration = segment["duration_sec"]
    valid_options = {k: v for k, v in window_options.items() if v <= total_duration}
    if not valid_options:
        valid_options = {"Full segment": total_duration}

    window_label = st.sidebar.select_slider(
        "Window size",
        options=list(valid_options.keys()),
        value=list(valid_options.keys())[min(2, len(valid_options) - 1)],
    )
    window_seconds = valid_options[window_label]

    max_offset = max(0.0, total_duration - window_seconds)

    # Initialize slider session state (slider key is the single source of truth)
    slider_key = "offset_slider"
    if slider_key not in st.session_state:
        st.session_state[slider_key] = 0.0
    else:
        st.session_state[slider_key] = float(st.session_state[slider_key])
    # Clamp stored offset to valid range
    if st.session_state[slider_key] > max_offset:
        st.session_state[slider_key] = 0.0

    # Navigation buttons (write directly to the slider's key so it picks up the change)
    col1, col2, col3 = st.sidebar.columns(3)
    with col1:
        if st.button("⏪", help="Jump back"):
            st.session_state[slider_key] = max(
                0.0, st.session_state[slider_key] - window_seconds
            )
    with col2:
        if st.button("⏺", help="Jump to middle"):
            st.session_state[slider_key] = min(max_offset, total_duration / 2)
    with col3:
        if st.button("⏩", help="Jump forward"):
            st.session_state[slider_key] = min(
                max_offset, st.session_state[slider_key] + window_seconds
            )

    # Time slider
    step = max(1.0, window_seconds / 10)
    offset_seconds = st.sidebar.slider(
        "Start time (seconds)",
        min_value=0.0,
        max_value=max(0.1, max_offset),
        step=step,
        format="%.0fs",
        key=slider_key,
    )

    # Position indicator
    pct = (offset_seconds / total_duration * 100) if total_duration > 0 else 0
    st.sidebar.caption(
        f"Viewing {window_label} starting at "
        f"{format_duration(offset_seconds / 60)} "
        f"({pct:.1f}% through segment)"
    )

    # --- Signal visibility toggles ---
    st.sidebar.header("Signals")
    all_signals = segment["all_signals"]
    visible_signals = []
    for sig in all_signals:
        checked = st.sidebar.checkbox(
            sig,
            value=True,
            key=f"sig_{segment['segment_name']}_{sig}",
        )
        if checked:
            visible_signals.append(sig)

    return {
        "patient_id": patient_id,
        "segment": segment,
        "window_seconds": window_seconds,
        "offset_seconds": offset_seconds,
        "visible_signals": visible_signals,
    }


@st.cache_data
def _get_patient_index():
    """Load catalog and build patient index (cached)."""
    catalog = load_catalog()
    segments = catalog["segments"]
    # build_patient_index expects a list of dicts
    patients = {}
    for seg in segments:
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

    for p in patients.values():
        p["segments"].sort(key=lambda s: (s["record_path"], s["segment_index"]))
        p["signal_set"] = sorted(p["signal_set"])

    return patients
