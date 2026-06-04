"""
Plotly figure construction for clinical waveform display.
"""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from viewer.signal_config import get_signal_color, get_y_range, is_pressure_signal


def build_waveform_figure(
    signal_data,
    signal_names,
    units,
    fs,
    sampfrom,
    visible_signals,
    stats=None,
    marker_time=None,
    transition_time=None,
):
    """Build a Plotly figure with vertically stacked waveforms.

    Args:
        signal_data: (N, num_channels) numpy array
        signal_names: Channel names matching columns of signal_data
        units: Unit strings for each channel
        fs: Sampling frequency (125 Hz)
        sampfrom: Starting sample number (for correct time axis)
        visible_signals: Subset of signal_names to display
        stats: Optional dict mapping signal_name -> stats dict

    Returns:
        plotly.graph_objects.Figure
    """
    visible_indices = [i for i, name in enumerate(signal_names) if name in visible_signals]
    n_visible = len(visible_indices)

    if n_visible == 0:
        fig = go.Figure()
        fig.add_annotation(
            text="No signals selected — use the sidebar checkboxes",
            showarrow=False,
            font=dict(size=16, color="white"),
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0a0a0a",
            plot_bgcolor="#0a0a0a",
            height=300,
        )
        return fig

    # Pressure signals get more vertical space
    row_heights = []
    for idx in visible_indices:
        if is_pressure_signal(signal_names[idx]):
            row_heights.append(2)
        else:
            row_heights.append(1)

    fig = make_subplots(
        rows=n_visible,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=row_heights,
    )

    # Time axis
    n_samples = signal_data.shape[0]
    time_offset_sec = sampfrom / fs
    t = np.arange(n_samples) / fs + time_offset_sec

    for plot_row, ch_idx in enumerate(visible_indices, start=1):
        name = signal_names[ch_idx]
        unit = units[ch_idx]
        color = get_signal_color(name)
        y = signal_data[:, ch_idx]

        fig.add_trace(
            go.Scattergl(
                x=t,
                y=y,
                mode="lines",
                line=dict(color=color, width=1.2),
                name=f"{name} ({unit})",
                hovertemplate=f"{name}: %{{y:.1f}} {unit}<br>Time: %{{x:.2f}}s<extra></extra>",
            ),
            row=plot_row,
            col=1,
        )

        # Y-axis label and range
        y_range = get_y_range(name)
        yaxis_kwargs = dict(
            title=f"{name} ({unit})",
            title_font=dict(color=color, size=11),
        )
        if y_range:
            yaxis_kwargs["range"] = list(y_range)
        fig.update_yaxes(**yaxis_kwargs, row=plot_row, col=1)

        # Stats annotation
        if stats and name in stats:
            annotation_text = _format_stats_annotation(stats[name])
            # Determine the correct axis references
            yaxis_ref = f"y{plot_row}" if plot_row > 1 else "y"
            fig.add_annotation(
                text=annotation_text,
                xref="paper",
                yref=f"{yaxis_ref} domain",
                x=1.0,
                y=1.0,
                showarrow=False,
                font=dict(family="monospace", size=11, color=color),
                bgcolor="rgba(0,0,0,0.7)",
                bordercolor=color,
                borderwidth=1,
                borderpad=4,
                xanchor="right",
                yanchor="top",
            )

    # Scan-flag markers: vertical lines across all stacked subplots so the user
    # can see where the RV detector flagged (and the RV->PA transition). Only
    # draw a marker when it falls INSIDE the current window — otherwise add_vline
    # would stretch the x-axis out to that time, squashing the waveform (looks
    # like a zoom) and pinning the flag to the edge.
    view_start = sampfrom / fs
    view_end = view_start + n_samples / fs

    def _in_view(tv):
        return tv is not None and view_start <= tv <= view_end

    if _in_view(marker_time):
        fig.add_vline(
            x=marker_time, line=dict(color="#FF8C00", width=1.5, dash="dash"),
            annotation_text="RV flag", annotation_position="top right",
            annotation_font=dict(color="#FF8C00", size=11),
            row="all", col=1,
        )
    if _in_view(transition_time):
        fig.add_vline(
            x=transition_time, line=dict(color="#00E5FF", width=1.5, dash="dot"),
            annotation_text="→PA", annotation_position="top left",
            annotation_font=dict(color="#00E5FF", size=11),
            row="all", col=1,
        )

    # Bottom x-axis label
    fig.update_xaxes(title="Time (seconds)", row=n_visible, col=1)

    # Global layout: dark clinical theme
    total_height = sum(row_heights) * 150
    total_height = max(total_height, 400)
    total_height = min(total_height, 1200)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0a0a",
        plot_bgcolor="#0a0a0a",
        height=total_height,
        margin=dict(l=80, r=20, t=30, b=50),
        showlegend=False,
        hovermode="x unified",
    )

    fig.update_xaxes(gridcolor="rgba(255,255,255,0.1)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.1)", zeroline=False)

    return fig


def _format_stats_annotation(stat):
    """Format stats dict into a compact annotation string."""
    if "systolic" in stat:
        return f"{stat['systolic']:.0f}/{stat['diastolic']:.0f} ({stat['mean']:.0f})"
    return f"min={stat.get('min', 0):.1f}  max={stat.get('max', 0):.1f}"
