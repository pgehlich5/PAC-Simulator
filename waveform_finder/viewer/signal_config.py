"""
Signal display configuration: clinical color scheme, y-axis ranges,
and type classification for MIMIC-III waveform signals.
"""

# Signal type classification
SIGNAL_TYPES = {
    "ECG": ["I", "II", "III", "AVR", "AVL", "AVF", "V", "MCL", "MCL1"],
    "PRESSURE_ARTERIAL": ["ABP", "ART", "AOBP", "P1"],
    "PRESSURE_PULMONARY": ["PAP"],
    "PRESSURE_VENOUS": ["CVP"],
    "PLETH": ["PLETH"],
    "RESP": ["RESP"],
}

# Clinical color scheme (mimics bedside monitors)
SIGNAL_COLORS = {
    # ECG leads: green
    "I": "#00FF00", "II": "#00FF00", "III": "#00FF00",
    "AVR": "#00FF00", "AVL": "#00FF00", "AVF": "#00FF00",
    "V": "#00FF00", "MCL": "#00FF00", "MCL1": "#00FF00",
    # Arterial pressure: red
    "ABP": "#FF3333", "ART": "#FF3333", "AOBP": "#FF3333", "P1": "#FF3333",
    # PAP: yellow
    "PAP": "#FFFF00",
    # CVP: blue
    "CVP": "#00BFFF",
    # Pleth: cyan
    "PLETH": "#00FFFF",
    # Resp: white
    "RESP": "#CCCCCC",
}

# Typical y-axis ranges for clinical display
SIGNAL_Y_RANGES = {
    "I": (-2.0, 2.0), "II": (-2.0, 2.0), "III": (-2.0, 2.0),
    "AVR": (-2.0, 2.0), "AVL": (-2.0, 2.0), "AVF": (-2.0, 2.0),
    "V": (-2.0, 2.0), "MCL": (-2.0, 2.0), "MCL1": (-2.0, 2.0),
    "ABP": (-10, 200), "ART": (-10, 200), "AOBP": (-10, 200), "P1": (-10, 200),
    "PAP": (-5, 80),
    "CVP": (-5, 30),
}

# Signals for which systolic/diastolic/mean stats are meaningful
PRESSURE_SIGNALS = {"ABP", "ART", "AOBP", "PAP", "CVP", "P1"}


def get_signal_color(sig_name):
    """Return the clinical display color for a signal name."""
    return SIGNAL_COLORS.get(sig_name, "#FFFFFF")


def get_signal_type(sig_name):
    """Return the category type for a signal name."""
    for sig_type, names in SIGNAL_TYPES.items():
        if sig_name in names:
            return sig_type
    return "OTHER"


def get_y_range(sig_name):
    """Return suggested y-axis range tuple, or None for autoscale."""
    return SIGNAL_Y_RANGES.get(sig_name, None)


def is_pressure_signal(sig_name):
    """Return True if the signal is a pressure waveform."""
    return sig_name in PRESSURE_SIGNALS
