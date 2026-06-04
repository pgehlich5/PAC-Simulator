"""
Streamlit sidebar widgets for patient/segment browsing and display controls.
"""

import streamlit as st
from viewer.data_loader import (
    load_catalog,
    build_patient_index,
    get_patient_list,
    get_segments_for_patient,
    format_segment_label,
    format_duration,
    load_dismissed,
    save_dismissed,
)
from viewer.candidates import load_candidates, candidate_label


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

    # --- Scan-candidate jump panel (from tools/rv_scan_results.json) ---
    _render_candidate_panel()

    # --- Patient selector ---
    st.sidebar.header("Patient")

    # Apply any pending programmatic patient selection (candidate jump / dismiss)
    # BEFORE the widget is created. Writing st.session_state[key] is honored on
    # every run; passing index= is only respected on first render and silently
    # ignored once the user has interacted (that was the two-clicks bug).
    if "_pending_patient" in st.session_state:
        pend = st.session_state.pop("_pending_patient")
        if pend in patient_list:
            st.session_state["patient_select"] = pend
    # Drop a stale stored value (e.g. a patient just removed by dismiss)
    if st.session_state.get("patient_select") not in patient_list:
        st.session_state.pop("patient_select", None)

    patient_id = st.sidebar.selectbox(
        "Select patient",
        options=patient_list,
        format_func=lambda pid: (
            f"{pid} ({patient_index[pid]['total_segments']} segments, "
            f"{format_duration(patient_index[pid]['total_duration_min'])})"
        ),
        key="patient_select",
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
    seg_by_name = {s["segment_name"]: s for s in segments}
    seg_names = list(seg_by_name.keys())

    # Honor a pending jump-to-candidate segment selection
    if "_jump_segment" in st.session_state:
        target = st.session_state.pop("_jump_segment")
        if target in seg_by_name:
            st.session_state["segment_select"] = target
    # Reset a stored selection that belongs to a different patient
    if st.session_state.get("segment_select") not in seg_by_name:
        st.session_state["segment_select"] = seg_names[0] if seg_names else None

    if not seg_names:
        return {"patient_id": patient_id, "segment": None}

    seg_name = st.sidebar.selectbox(
        "Select segment",
        options=seg_names,
        format_func=lambda n: format_segment_label(seg_by_name[n]),
        key="segment_select",
    )
    segment = seg_by_name.get(seg_name)

    if not segment:
        return {"patient_id": patient_id, "segment": None}

    # --- Dismiss button ---
    if st.sidebar.button("🚫 Dismiss Segment",
                         help="Hide this segment from the list (bad data)"):
        dismissed = load_dismissed()
        dismissed.add(segment["segment_name"])
        save_dismissed(dismissed)

        # Queue where to land after the rerun. The dismiss button sits below the
        # selectboxes, so we can't set their state directly here — use the same
        # pending keys the candidate jump uses (applied before the widgets next
        # run).
        remaining = [s for s in segments
                     if s["segment_name"] != segment["segment_name"]]
        if remaining:
            st.session_state["_pending_patient"] = patient_id
            st.session_state["_jump_segment"] = remaining[0]["segment_name"]
        else:
            others = [p for p in patient_list if p != patient_id]
            if others:
                cur = patient_list.index(patient_id)
                st.session_state["_pending_patient"] = others[min(cur, len(others) - 1)]

        st.cache_data.clear()
        st.rerun()

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

    # Honor a pending jump-to-candidate timestamp (land slightly before the
    # flagged time so the flag sits inside the visible window).
    if "_jump_offset" in st.session_state:
        target_t = float(st.session_state.pop("_jump_offset"))
        lead = window_seconds * 0.3
        st.session_state[slider_key] = float(
            max(0.0, min(target_t - lead, max_offset))
        )

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

    # --- Dismissed segments info ---
    dismissed = load_dismissed()
    if dismissed:
        st.sidebar.divider()
        st.sidebar.caption(f"🚫 {len(dismissed)} segments dismissed")
        col_undo, col_show = st.sidebar.columns(2)
        with col_undo:
            if st.button("Undo Last"):
                dismissed_list = sorted(dismissed)
                dismissed.remove(dismissed_list[-1])
                save_dismissed(dismissed)
                st.cache_data.clear()
                st.rerun()
        with col_show:
            show_dismissed = st.checkbox("Show list", key="show_dismissed_list")
        if show_dismissed:
            st.sidebar.code("\n".join(sorted(dismissed)))

    # Marker line: show where the scan flagged RV (only on the matching segment)
    marker = st.session_state.get("active_marker")
    marker_time = transition_time = None
    if marker and marker.get("segment_name") == segment["segment_name"]:
        marker_time = marker.get("t_sec")
        transition_time = marker.get("transition_t")

    return {
        "patient_id": patient_id,
        "segment": segment,
        "window_seconds": window_seconds,
        "offset_seconds": offset_seconds,
        "visible_signals": visible_signals,
        "marker_time": marker_time,
        "transition_time": transition_time,
    }


def _queue_candidate_jump(c):
    """Stage a navigation to candidate c. The pending keys are applied before
    the patient/segment/offset widgets render, so the jump takes effect on the
    next pass (callbacks auto-rerun)."""
    patient_list = get_patient_list(_get_patient_index())
    if c["patient_id"] not in patient_list:
        st.session_state["_cand_warn"] = (
            f"{c['patient_id']} / {c['segment_name']} isn't in the catalog "
            "(maybe dismissed)."
        )
        return
    best = c.get("best_rv", {})
    st.session_state["_pending_patient"] = c["patient_id"]
    st.session_state["_jump_segment"] = c["segment_name"]
    st.session_state["_jump_offset"] = best.get("t_sec", 0.0)
    st.session_state["active_marker"] = {
        "segment_name": c["segment_name"],
        "t_sec": best.get("t_sec"),
        "transition_t": c.get("transition_t") if c.get("transition") else None,
    }


def _on_candidate_change():
    """Selectbox on_change: jump to the newly chosen candidate immediately."""
    cands = load_candidates()
    i = st.session_state.get("cand_select")
    if i is not None and 0 <= i < len(cands):
        _queue_candidate_jump(cands[i])


def _render_candidate_panel():
    """Sidebar panel: picking a flagged candidate jumps to it automatically."""
    cands = load_candidates()
    if not cands:
        return
    total = len(cands)
    with st.sidebar.expander(f"🔎 Scan candidates ({total})", expanded=True):
        sel_i = st.selectbox(
            "Flagged candidate (jumps on select)",
            options=list(range(total)),
            format_func=lambda i: candidate_label(cands[i], i, total),
            key="cand_select",
            on_change=_on_candidate_change,
        )
        if "_cand_warn" in st.session_state:
            st.warning(st.session_state.pop("_cand_warn"))
        c = cands[sel_i]
        best = c.get("best_rv", {})
        extra = (f", transition @{c['transition_t']:.0f}s"
                 if c.get("transition") and c.get("transition_t") is not None
                 else "")
        st.caption(
            f"`{c['patient_id']}` / `{c['segment_name']}` — sustained RV run "
            f"{c.get('longest_rv_run', '?')} windows{extra}. "
            f"Best RV @{best.get('t_sec', 0):.0f}s (score {best.get('score', 0):.2f})."
        )
        # Re-jump to the same candidate (on_change won't fire on a re-select,
        # e.g. after you've scrubbed away from the flag).
        st.button("↩  Re-center on flag", key="cand_jump",
                  use_container_width=True,
                  on_click=_queue_candidate_jump, args=(c,))


def _get_patient_index():
    """Load catalog and build patient index, filtering out dismissed segments."""
    catalog = load_catalog()
    dismissed = load_dismissed()
    return build_patient_index(catalog["segments"], dismissed=dismissed)
