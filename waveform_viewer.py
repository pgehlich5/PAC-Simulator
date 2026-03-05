"""
PAP Waveform Viewer — Streamlit application for exploring PAP waveforms
from the MIMIC-III Waveform Database.

Run with:
    streamlit run waveform_viewer.py
"""

import streamlit as st

from viewer.ui_components import render_sidebar
from viewer.waveform_fetch import fetch_waveform_window, compute_sample_range
from viewer.signal_processing import compute_signal_stats
from viewer.chart_builder import build_waveform_figure
from viewer.signal_config import is_pressure_signal, get_signal_color
from viewer.data_loader import load_catalog, format_duration


def main():
    st.set_page_config(
        page_title="PAP Waveform Viewer",
        page_icon="\u2764",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom CSS (theme colors handled by .streamlit/config.toml)
    st.markdown(
        """
        <style>
        .stats-box {
            background-color: #1a1a2e;
            border-radius: 8px;
            padding: 12px;
            margin: 4px;
            text-align: center;
        }
        .stats-label { color: #aaaaaa !important; font-size: 12px; }
        .stats-value { font-size: 24px; font-weight: bold; font-family: monospace; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Sidebar ---
    selections = render_sidebar()

    segment = selections.get("segment")
    if not segment:
        st.title("PAP Waveform Viewer")
        st.markdown("Select a patient and segment from the sidebar to begin.")
        _render_database_overview()
        return

    # --- Header ---
    patient_id = selections["patient_id"]
    st.markdown(
        f"### Patient `{patient_id}` — Segment `{segment['segment_name']}`"
    )
    _render_segment_info_bar(segment)

    # --- Fetch waveform data ---
    sampfrom, sampto = compute_sample_range(
        offset_seconds=selections["offset_seconds"],
        window_seconds=selections["window_seconds"],
        total_samples=segment["num_samples"],
        fs=segment["sampling_rate_hz"],
    )

    with st.spinner("Downloading waveform data from PhysioNet..."):
        try:
            waveform = fetch_waveform_window(
                segment_name=segment["segment_name"],
                record_dir=segment["record_dir"],
                sampfrom=sampfrom,
                sampto=sampto,
            )
        except Exception as e:
            st.error(f"Failed to download waveform data: {e}")
            st.info("This may be a network issue. Try again or select a different segment.")
            return

    # --- Compute stats for visible signals ---
    visible = selections["visible_signals"]
    stats = {}
    for i, sig_name in enumerate(waveform["signal_names"]):
        if sig_name in visible:
            stats[sig_name] = compute_signal_stats(
                waveform["signal_data"][:, i],
                waveform["fs"],
                sig_name,
            )

    # --- Pressure stats banner ---
    _render_pressure_stats_banner(
        stats, waveform["signal_names"], waveform["units"], visible
    )

    # --- Waveform plot ---
    fig = build_waveform_figure(
        signal_data=waveform["signal_data"],
        signal_names=waveform["signal_names"],
        units=waveform["units"],
        fs=waveform["fs"],
        sampfrom=sampfrom,
        visible_signals=visible,
        stats=stats,
    )

    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )

    # --- Footer ---
    st.caption(
        f"Showing {waveform['num_samples']:,} samples "
        f"({waveform['num_samples'] / waveform['fs']:.1f}s) "
        f"at {waveform['fs']} Hz | "
        f"Samples {sampfrom:,} to {sampto:,} of {segment['num_samples']:,}"
    )


def _render_database_overview():
    """Show summary stats when no segment is selected."""
    catalog = load_catalog()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Patients", f"{catalog['records_with_pap']}")
    col2.metric("PAP Segments", f"{catalog['total_pap_segments']:,}")
    col3.metric("Records Scanned", f"{catalog['records_scanned']:,}")
    col4.metric("Database", "MIMIC-III")

    st.markdown("---")
    st.markdown(
        "This viewer lets you browse **Pulmonary Artery Pressure (PAP)** "
        "waveforms and all co-recorded signals from the MIMIC-III Waveform "
        "Database Matched Subset on PhysioNet. Data is downloaded on demand "
        "— only the time window you're viewing is fetched."
    )


def _render_segment_info_bar(segment):
    """Show compact segment metadata."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Duration", format_duration(segment["duration_min"]))
    c2.metric("Signals", len(segment["all_signals"]))
    c3.metric("Total Samples", f"{segment['num_samples']:,}")
    c4.metric("Sample Rate", f"{segment['sampling_rate_hz']} Hz")


def _render_pressure_stats_banner(stats, signal_names, units, visible):
    """Render sys/dia/mean for visible pressure signals like a monitor readout."""
    pressure_sigs = [
        s for s in visible if is_pressure_signal(s) and s in stats
        and "systolic" in stats[s]
    ]
    if not pressure_sigs:
        return

    cols = st.columns(len(pressure_sigs))
    for col, sig in zip(cols, pressure_sigs):
        stat = stats[sig]
        idx = signal_names.index(sig)
        unit = units[idx]
        color = get_signal_color(sig)
        col.markdown(
            f"<div class='stats-box'>"
            f"<div class='stats-label'>{sig}</div>"
            f"<div class='stats-value' style='color:{color}'>"
            f"{stat['systolic']:.0f}/{stat['diastolic']:.0f}</div>"
            f"<div class='stats-label'>({stat['mean']:.0f}) {unit}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
