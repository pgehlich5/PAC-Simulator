#!/usr/bin/env python3
"""
PAC Insertion Simulator with Philips IntelliVue-style Pressure Waveforms

Supports two modes:
  Generated mode:
    Displays mathematically generated pressure traces as catheter advances
    through cardiac chambers.  Uses rotary encoder or +/- keys.

  Real advancement mode (default):
    Combines real MIMIC-III waveforms with encoder-driven chamber advancement.
    Switches between chamber-specific waveform clips (RA, RV, PA, Wedge)
    as the user advances the catheter.

Usage:
  python pac_simulator.py                          # real advancement mode
  python pac_simulator.py --mode generated         # generated mode
  python pac_simulator.py --mode real-advancement   # real waveforms + encoder
"""

import argparse
import csv
import json
import math
import os
import time
import tkinter as tk

# --- Optional GPIO imports (graceful fallback for Windows/testing) ---------------
try:
    from gpiozero import RotaryEncoder, Button
    _HAS_GPIO = True
except Exception:
    RotaryEncoder = None
    Button = None
    _HAS_GPIO = False

# --- Chamber Thresholds & Hysteresis ---------------------------------------------
THRESHOLDS = {
    "SVC": 0,
    "RA": 850,
    "RV": 1200,
    "PA": 2600,
    "PCWP": 3000,
}
HYST = 20  # steps of hysteresis to prevent chamber flicker

# --- Physiological Parameters per Chamber ----------------------------------------
CHAMBER_PARAMS = {
    "SVC": {
        "name": "Superior Vena Cava",
        "systolic": 5,
        "diastolic": 2,
        "mean": 3,
        "waveform_type": "cvp",
        "color": "#FFCC00",  # Yellow like IntelliVue PAP
    },
    "RA": {
        "name": "Right Atrium",
        "systolic": 8,
        "diastolic": 2,
        "mean": 5,
        "waveform_type": "cvp",
        "color": "#FFCC00",
    },
    "RV": {
        "name": "Right Ventricle",
        "systolic": 25,
        "diastolic": 4,
        "mean": 12,
        "waveform_type": "rv",
        "color": "#FFCC00",
    },
    "PA": {
        "name": "Pulmonary Artery",
        "systolic": 25,
        "diastolic": 10,
        "mean": 15,
        "waveform_type": "pa",
        "color": "#FFCC00",
    },
    "PCWP": {
        "name": "Pulmonary Capillary Wedge",
        "systolic": 12,
        "diastolic": 8,
        "mean": 10,
        "waveform_type": "wedge",
        "color": "#FFCC00",
    },
}

# --- Chamber-to-case mapping for real advancement mode -----------------------
CHAMBER_CASES = {
    "SVC":  "pac_insertion_ra",      # SVC uses RA waveforms
    "RA":   "pac_insertion_ra",
    "RV":   "pac_insertion_rv",
    "PA":   "pac_insertion_pa",
    "PCWP": "pac_insertion_wedge",
}


def load_clinical_vignette():
    """Load the clinical scenario vignette text from clinical_data/."""
    vignette_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "clinical_data", "clinical_vignette.txt"
    )
    try:
        with open(vignette_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


# Waveform generation parameters
HEART_RATE = 75  # bpm
SAMPLES_PER_BEAT = 100
SCROLL_SPEED = 8  # pixels per frame (constant across all chambers)
FRAME_RATE = 30  # Hz  (real bedside monitors run ~25 Hz)

# --- Signal display configuration for real waveform mode -------------------------
SIGNAL_CONFIG = {
    "II": {
        "color": "#00FF00",       # Green (ECG)
        "label": "ECG II",
        "min_val": -0.5,
        "max_val": 0.5,
        "unit": "mV",
        "grid_major": 0.5,
        "grid_minor": 0.25,
        "grid_color": "#003300",
        "grid_major_color": "#005500",
        "label_color": "#00FF00",
    },
    "ABP": {
        "color": "#FF3333",       # Red (arterial)
        "label": "ABP",
        "min_val": 0,
        "max_val": 150,
        "unit": "mmHg",
        "grid_major": 50,
        "grid_minor": 25,
        "grid_color": "#330000",
        "grid_major_color": "#550000",
        "label_color": "#FF3333",
    },
    "PAP": {
        "color": "#FFCC00",       # Yellow (PA pressure)
        "label": "PAP",
        "min_val": 0,
        "max_val": 50,
        "unit": "mmHg",
        "grid_major": 10,
        "grid_minor": 5,
        "grid_color": "#332800",
        "grid_major_color": "#4a3800",
        "label_color": "#FFCC00",
    },
    "CVP": {
        "color": "#00BFFF",       # Blue (central venous)
        "label": "CVP",
        "min_val": 0,
        "max_val": 20,
        "unit": "mmHg",
        "grid_major": 5,
        "grid_minor": 2.5,
        "grid_color": "#001a33",
        "grid_major_color": "#002a55",
        "label_color": "#00BFFF",
    },
}

# Display priority order — signals are stacked top to bottom in this order
SIGNAL_DISPLAY_ORDER = ["II", "ABP", "PAP"]  # CVP hidden for now (not visible on Pi)

# Pressure signals that get sys/dia/mean readout
PRESSURE_SIGNALS = {"ABP", "PAP", "CVP"}

# Track current chamber
current_chamber = "SVC"

# Internal step counter for mock mode
_steps_sim = 0


def map_steps_to_chamber(steps: int) -> str:
    """Return chamber name based on encoder step count with hysteresis bands."""
    global current_chamber

    if steps < THRESHOLDS["RA"] - HYST:
        current_chamber = "SVC"
    elif steps < THRESHOLDS["RV"] - HYST:
        current_chamber = "RA"
    elif steps < THRESHOLDS["PA"] - HYST:
        current_chamber = "RV"
    elif steps < THRESHOLDS["PCWP"] - HYST:
        current_chamber = "PA"
    else:
        current_chamber = "PCWP"

    return current_chamber


def generate_waveform(waveform_type: str) -> list:
    """
    Generate one cardiac cycle of pressure waveform data.
    Returns list of normalized pressure values (0.0 to 1.0).
    """
    points = []

    if waveform_type == "cvp":  # CVP/RA waveform (a, c, v waves)
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            a_wave = 0.3 * math.exp(-((t - 0.1) ** 2) / 0.002) if 0.05 < t < 0.2 else 0
            c_wave = 0.15 * math.exp(-((t - 0.25) ** 2) / 0.001) if 0.2 < t < 0.35 else 0
            v_wave = 0.25 * math.exp(-((t - 0.55) ** 2) / 0.004) if 0.4 < t < 0.75 else 0
            base = 0.2
            value = base + a_wave + c_wave + v_wave
            points.append(min(1.0, value))

    elif waveform_type == "rv":  # Right Ventricle
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            if t < 0.35:
                if t < 0.1:
                    value = t / 0.1
                else:
                    value = 1.0 - ((t - 0.1) / 0.25) * 0.3
            else:
                value = 0.7 * math.exp(-(t - 0.35) / 0.1)
            points.append(min(1.0, max(0.0, value)))

    elif waveform_type == "pa":  # Pulmonary Artery
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            if t < 0.35:
                if t < 0.1:
                    value = t / 0.1
                else:
                    value = 1.0 - ((t - 0.1) / 0.25) * 0.4
            else:
                notch = -0.15 * math.exp(-((t - 0.36) ** 2) / 0.0005) if 0.34 < t < 0.38 else 0
                value = 0.6 * math.exp(-(t - 0.35) / 0.25) + 0.4 + notch
            points.append(min(1.0, max(0.0, value)))

    elif waveform_type == "wedge":  # PCWP
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            a_wave = 0.35 * math.exp(-((t - 0.15) ** 2) / 0.003) if 0.05 < t < 0.3 else 0
            v_wave = 0.4 * math.exp(-((t - 0.5) ** 2) / 0.006) if 0.35 < t < 0.7 else 0
            base = 0.5
            value = base + a_wave + v_wave
            points.append(min(1.0, value))

    return points


# =============================================================================
# Real Waveform Loader — loads extracted MIMIC-III cases from waveform_data/
# =============================================================================
class RealWaveformLoader:
    """Load and serve real waveform data from exported CSV cases."""

    def __init__(self, case_name):
        self.case_name = case_name
        self.base_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "waveform_data", case_name
        )
        self.metadata = {}
        self.signals = {}       # {signal_name: list of float values}
        self.signal_list = []   # ordered list of available signal names
        self.fs = 125           # sampling rate
        self.num_samples = 0
        self.description = ""

        self._load()

    def _load(self):
        """Load metadata and all signal CSVs."""
        meta_path = os.path.join(self.base_dir, "metadata.json")
        if not os.path.exists(meta_path):
            raise FileNotFoundError(
                f"Waveform case not found: {meta_path}\n"
                f"Run export_waveform_case.py first to create it."
            )

        with open(meta_path, "r") as f:
            self.metadata = json.load(f)

        self.fs = self.metadata["sampling_rate_hz"]
        self.num_samples = self.metadata["num_samples"]
        self.description = self.metadata.get("description", "")

        # Load each signal CSV in display priority order
        available = {s["signal_name"]: s for s in self.metadata["signals"]}
        for sig_name in SIGNAL_DISPLAY_ORDER:
            if sig_name in available:
                csv_path = os.path.join(
                    self.base_dir, available[sig_name]["file"]
                )
                self.signals[sig_name] = self._load_csv(csv_path)
                self.signal_list.append(sig_name)

        if not self.signal_list:
            raise ValueError(
                f"No displayable signals found in case '{self.case_name}'. "
                f"Available: {list(available.keys())}"
            )

        print(f"Loaded case '{self.case_name}': "
              f"{len(self.signal_list)} signals, "
              f"{self.num_samples} samples ({self.num_samples/self.fs:.0f}s)")
        for sig in self.signal_list:
            cfg = SIGNAL_CONFIG.get(sig, {})
            print(f"  {sig}: {cfg.get('label', sig)} ({cfg.get('unit', '?')})")

    @staticmethod
    def _load_csv(path):
        """Load a single-column CSV (with header) into a list of floats."""
        values = []
        with open(path, "r") as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                try:
                    values.append(float(row[0]))
                except (ValueError, IndexError):
                    values.append(0.0)
        return values

    def get_sample(self, signal_name, index):
        """Get a single sample, wrapping around for looping."""
        data = self.signals.get(signal_name)
        if data is None:
            return 0.0
        return data[index % len(data)]

    def compute_pressure_stats(self, signal_name, window_samples=1250):
        """Compute sys/dia/mean from the most recent window of pressure data.

        Uses a simple min/max/mean approach over a window (default ~10 seconds
        at 125 Hz).
        """
        data = self.signals.get(signal_name)
        if data is None:
            return None

        # Use the whole signal for now (it's only 2 minutes)
        vals = data
        if not vals:
            return None

        systolic = max(vals)
        diastolic = min(vals)
        mean_val = sum(vals) / len(vals)
        return {
            "systolic": round(systolic),
            "diastolic": round(diastolic),
            "mean": round(mean_val),
        }


# --- Hardware or mock setup ------------------------------------------------------
if _HAS_GPIO:
    encoder = RotaryEncoder(a=17, b=18, max_steps=10000, wrap=False)
    reset_button = Button(2)
    _zero_offset = 0
else:
    encoder = None
    reset_button = None
    _zero_offset = 0


# =============================================================================
# Generated Mode — original PAC simulator (single pressure trace + encoder)
# =============================================================================
class PAC_Simulator_Generated:
    """Original generated-waveform mode with chamber advancement."""

    def __init__(self, root, parent=None):
        self.root = root
        self.parent = parent or root
        self.root.title("PAC Simulator - Philips IntelliVue")
        if parent is None:
            self.root.geometry("1280x800")
            self.root.configure(bg="#000000")

        # Waveform state
        self.current_waveform = generate_waveform("cvp")
        self.waveform_index = 0
        self.scan_x = 0
        self.current_chamber_name = "SVC"
        self.last_draw_x = None
        self.last_draw_y = None
        self._grid_dirty = False

        self._create_ui()

        self.last_update = time.time()
        self.running = True

        self._update_waveform()
        self._update_chamber()

    def _create_ui(self):
        """Create Philips IntelliVue-style interface."""
        # Top status bar
        self.frame_top = tk.Frame(self.parent, bg="#1a1a1a", height=60)
        self.frame_top.pack(fill=tk.X, side=tk.TOP)
        self.frame_top.pack_propagate(False)

        self.lbl_chamber_name = tk.Label(
            self.frame_top, text="SUPERIOR VENA CAVA",
            font=("Helvetica", 18, "bold"), fg="#FFD84D", bg="#1a1a1a"
        )
        self.lbl_chamber_name.pack(side=tk.LEFT, padx=20, pady=10)

        self.lbl_steps = tk.Label(
            self.frame_top, text="Steps: 0",
            font=("Helvetica", 14), fg="#AAAAAA", bg="#1a1a1a"
        )
        self.lbl_steps.pack(side=tk.RIGHT, padx=20, pady=10)

        # Main area
        self.frame_main = tk.Frame(self.parent, bg="#000000")
        self.frame_main.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            self.frame_main, bg="#000000", highlightthickness=0
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                         padx=(10, 5), pady=10)

        # Pressure values
        self.frame_values = tk.Frame(self.frame_main, bg="#000000", width=400)
        self.frame_values.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 35), pady=10)
        self.frame_values.pack_propagate(False)

        self.lbl_pressure = tk.Label(
            self.frame_values, text="5/2 (3)",
            font=("Arial", 50, "bold"), fg="#FFCC00", bg="#000000",
            justify=tk.CENTER
        )
        self.lbl_pressure.pack(expand=True, padx=20)

        # Bottom bar
        self.frame_bottom = tk.Frame(self.parent, bg="#1a1a1a", height=50)
        self.frame_bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self.frame_bottom.pack_propagate(False)

        self.btn_reset = tk.Button(
            self.frame_bottom, text="RESET TO SVC",
            font=("Helvetica", 12, "bold"), bg="#444444", fg="#FFFFFF",
            activebackground="#666666", command=self.do_reset,
            padx=20, pady=5
        )
        self.btn_reset.pack(side=tk.LEFT, padx=20, pady=10)

        mode_text = ("HARDWARE MODE - Rotary Encoder Active" if _HAS_GPIO
                     else "SIMULATION MODE - Use +/- keys or Reset button")
        self.lbl_mode = tk.Label(
            self.frame_bottom, text=mode_text,
            font=("Helvetica", 10), fg="#888888", bg="#1a1a1a"
        )
        self.lbl_mode.pack(side=tk.LEFT, padx=20, pady=10)

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._draw_grid()

    def _draw_grid(self):
        """Draw grid lines with fixed 0-50 mmHg pressure scale."""
        self.canvas.delete("grid")
        self.canvas.delete("scale_labels")

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return

        max_pressure = 50
        margin_top = 20
        margin_bottom = 20
        usable_height = h - margin_top - margin_bottom

        for pressure in range(0, max_pressure + 1, 5):
            y = margin_top + (max_pressure - pressure) / max_pressure * usable_height
            if pressure % 10 == 0:
                self.canvas.create_line(0, y, w, y, fill="#4a3800", width=2, tags="grid")
                self.canvas.create_text(
                    8, y, text=str(pressure),
                    font=("Helvetica", 12, "bold"), fill="#FFDD00",
                    anchor=tk.W, tags="scale_labels"
                )
            else:
                self.canvas.create_line(0, y, w, y, fill="#332800", tags="grid")

    def _on_canvas_resize(self, event=None):
        self._grid_dirty = True
        self.canvas.delete("waveform")
        self.canvas.delete("clearzone")
        self.last_draw_x = None
        self.last_draw_y = None

    def _get_steps(self) -> int:
        if _HAS_GPIO:
            s = int(getattr(encoder, "steps", 0)) - _zero_offset
            return max(0, s)
        return _steps_sim

    def do_reset(self):
        global _steps_sim, _zero_offset
        if _HAS_GPIO:
            _zero_offset = int(getattr(encoder, "steps", 0))
        else:
            _steps_sim = 0

    def _update_chamber(self):
        if not self.running:
            return

        steps = self._get_steps()
        new_chamber = map_steps_to_chamber(steps)
        params = CHAMBER_PARAMS[new_chamber]

        self.lbl_chamber_name.config(text=params["name"].upper())
        pressure_text = f"{params['systolic']}/{params['diastolic']} ({params['mean']})"
        self.lbl_pressure.config(text=pressure_text)
        self.lbl_steps.config(text=f"Steps: {steps}")

        if new_chamber != self.current_chamber_name:
            self.current_chamber_name = new_chamber
            self.current_waveform = generate_waveform(params["waveform_type"])

        self.root.after(50, self._update_chamber)

    def _pressure_to_y(self, pressure, canvas_height):
        """Convert a pressure value to a y-coordinate."""
        max_pressure = 50
        margin_top = 20
        margin_bottom = 20
        usable_height = canvas_height - margin_top - margin_bottom
        pressure = max(0, min(max_pressure, pressure))
        return margin_top + (max_pressure - pressure) / max_pressure * usable_height

    def _update_waveform(self):
        if not self.running:
            return

        ms = int(1000 / FRAME_RATE)

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            self.root.after(ms, self._update_waveform)
            return

        # Handle deferred grid redraw
        if self._grid_dirty:
            self._draw_grid()
            self._grid_dirty = False

        params = CHAMBER_PARAMS[self.current_chamber_name]
        systolic = params["systolic"]
        diastolic = params["diastolic"]
        color = params["color"]
        clear_zone_width = 20

        # Draw SCROLL_SPEED new pixels incrementally
        for _ in range(SCROLL_SPEED):
            x = self.scan_x

            # Delete old waveform line at the clear zone leading edge
            clear_x = (x + clear_zone_width) % w
            self.canvas.delete(f"wf_{clear_x}")

            norm_value = self.current_waveform[self.waveform_index]
            pressure = diastolic + (systolic - diastolic) * norm_value
            y = self._pressure_to_y(pressure, h)

            # Draw 1-pixel line from previous point (skip on wrap)
            px = self.last_draw_x
            py = self.last_draw_y
            if px is not None and py is not None and abs(x - px) <= 1:
                self.canvas.create_line(
                    px, py, x, y,
                    fill=color, width=2, tags=("waveform", f"wf_{x}"))

            self.last_draw_x = x
            self.last_draw_y = y
            self.scan_x = (self.scan_x + 1) % w
            self.waveform_index = (self.waveform_index + 1) % len(self.current_waveform)

        # Update clear zone rectangle
        self.canvas.delete("clearzone")
        self.canvas.create_rectangle(
            self.scan_x, 0, self.scan_x + clear_zone_width, h,
            fill="#000000", outline="", tags="clearzone")

        self.root.after(ms, self._update_waveform)

    def cleanup(self):
        self.running = False


# =============================================================================
# Real Advancement Mode — real waveforms + encoder-driven chamber switching
# =============================================================================
class PAC_Simulator_RealAdvancement:
    """Real waveform mode with encoder-driven chamber advancement.

    Loads a separate MIMIC-III waveform clip for each chamber (RA, RV, PA, Wedge)
    and switches between them as the user advances the catheter via encoder or
    keyboard +/- keys.
    """

    def __init__(self, root, parent=None):
        self.root = root
        self.parent = parent or root
        self.root.title("PAC Simulator - Real Advancement Mode")
        if parent is None:
            self.root.geometry("1280x800")
            self.root.configure(bg="#000000")

        # Load all chamber cases
        self.loaders = {}
        loaded_cases = set()
        for chamber, case_name in CHAMBER_CASES.items():
            if case_name not in loaded_cases:
                try:
                    self.loaders[case_name] = RealWaveformLoader(case_name)
                    loaded_cases.add(case_name)
                except (FileNotFoundError, ValueError) as e:
                    print(f"WARNING: Could not load case '{case_name}' "
                          f"for chamber {chamber}: {e}")

        if not self.loaders:
            raise ValueError(
                "No waveform cases could be loaded for real advancement mode.\n"
                "Ensure waveform_data/ contains: "
                + ", ".join(set(CHAMBER_CASES.values()))
            )

        # Start in SVC/RA
        self.current_chamber_name = "SVC"
        self.active_loader = self.loaders[CHAMBER_CASES["SVC"]]

        # Determine which signals are available across ALL cases
        # Use the union of signals from all loaders (display what's available)
        all_available = set()
        for loader in self.loaders.values():
            all_available.update(loader.signal_list)
        self.display_signals = [s for s in SIGNAL_DISPLAY_ORDER
                                if s in all_available]

        # Playback state
        self.sample_index = 0
        self.running = True
        self.sample_accumulator = 0.0
        self.samples_per_frame = self.active_loader.fs / FRAME_RATE

        # Incremental drawing state
        self.scan_x = 0
        self.last_draw_x = {sig: None for sig in self.display_signals}
        self.last_draw_y = {sig: None for sig in self.display_signals}
        self._grid_dirty = set()    # signals needing deferred grid redraw

        # Compute initial pressure stats and heart rate
        self.pressure_stats = {}
        self.heart_rate = None
        self._recompute_stats()

        self._create_ui()

        # Start animation and chamber polling
        self._update_waveform()
        self._update_chamber()

    def _recompute_stats(self):
        """Recompute pressure stats and heart rate from the active loader."""
        self.pressure_stats = {}
        for sig in self.display_signals:
            if sig in PRESSURE_SIGNALS and sig in self.active_loader.signals:
                stats = self.active_loader.compute_pressure_stats(sig)
                if stats:
                    self.pressure_stats[sig] = stats

        # Heart rate from ECG
        ecg_data = self.active_loader.signals.get("II")
        if ecg_data is not None:
            self.heart_rate = self._compute_heart_rate_from(ecg_data,
                                                            self.active_loader.fs)
        else:
            self.heart_rate = None

    @staticmethod
    def _compute_heart_rate_from(ecg_data, fs):
        """Estimate heart rate from ECG data using R-peak detection."""
        threshold = max(ecg_data) * 0.6
        min_distance = int(0.4 * fs)
        peaks = []
        i = 0
        while i < len(ecg_data):
            if ecg_data[i] > threshold:
                peak_idx = i
                while i < len(ecg_data) and ecg_data[i] > threshold:
                    if ecg_data[i] > ecg_data[peak_idx]:
                        peak_idx = i
                    i += 1
                peaks.append(peak_idx)
                i = peak_idx + min_distance
            else:
                i += 1

        if len(peaks) < 2:
            return None

        rr_intervals = []
        for j in range(1, len(peaks)):
            rr = (peaks[j] - peaks[j - 1]) / fs
            if 0.3 < rr < 2.0:
                rr_intervals.append(rr)

        if not rr_intervals:
            return None

        avg_rr = sum(rr_intervals) / len(rr_intervals)
        return round(60.0 / avg_rr)

    def _create_ui(self):
        """Create multi-signal bedside monitor with chamber advancement display."""
        # Top status bar with chamber info
        self.frame_top = tk.Frame(self.parent, bg="#1a1a1a", height=60)
        self.frame_top.pack(fill=tk.X, side=tk.TOP)
        self.frame_top.pack_propagate(False)

        params = CHAMBER_PARAMS[self.current_chamber_name]
        self.lbl_chamber_name = tk.Label(
            self.frame_top, text=params["name"].upper(),
            font=("Helvetica", 18, "bold"), fg="#FFD84D", bg="#1a1a1a"
        )
        self.lbl_chamber_name.pack(side=tk.LEFT, padx=20, pady=10)

        self.lbl_steps = tk.Label(
            self.frame_top, text="Steps: 0",
            font=("Helvetica", 14), fg="#AAAAAA", bg="#1a1a1a"
        )
        self.lbl_steps.pack(side=tk.RIGHT, padx=20, pady=10)

        # Bottom bar
        self.frame_bottom = tk.Frame(self.parent, bg="#1a1a1a", height=50)
        self.frame_bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self.frame_bottom.pack_propagate(False)

        self.btn_reset = tk.Button(
            self.frame_bottom, text="RESET TO SVC",
            font=("Helvetica", 12, "bold"), bg="#444444", fg="#FFFFFF",
            activebackground="#666666", command=self.do_reset,
            padx=20, pady=5
        )
        self.btn_reset.pack(side=tk.LEFT, padx=20, pady=10)

        mode_text = ("REAL ADVANCEMENT - Rotary Encoder Active" if _HAS_GPIO
                     else "REAL ADVANCEMENT - Use +/- keys or Reset button")
        self.lbl_mode = tk.Label(
            self.frame_bottom, text=mode_text,
            font=("Helvetica", 10), fg="#888888", bg="#1a1a1a"
        )
        self.lbl_mode.pack(side=tk.LEFT, padx=20, pady=10)

        # Main area: stacked signal rows
        self.frame_main = tk.Frame(self.parent, bg="#000000")
        self.frame_main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Clinical scenario overlay (hidden by default)
        self.vignette_text = load_clinical_vignette()
        self.clinical_panel_visible = False
        self._clinical_overlay = None

        # "Patient History" button — top center, always visible
        if self.vignette_text:
            self._hx_btn = tk.Label(
                self.parent, text=" Patient History ",
                font=("Helvetica", 10, "bold"),
                fg="#CCCCCC", bg="#333333",
                padx=8, pady=4, cursor="hand2",
            )
            self._hx_btn.place(relx=0.5, y=68, anchor=tk.N)
            self._hx_btn.bind("<Button-1>",
                              lambda e: self._toggle_clinical_panel())

        self.canvases = {}
        self.readout_frames = {}
        READOUT_WIDTH = 180

        # Vertical weight per signal — PAP gets more room
        self.frame_main.columnconfigure(0, weight=1)
        ROW_WEIGHT = {"II": 1, "ABP": 2, "PAP": 3}

        for idx, sig_name in enumerate(self.display_signals):
            cfg = SIGNAL_CONFIG.get(sig_name, {})
            color = cfg.get("color", "#FFFFFF")

            # Row frame — use grid for weighted row heights
            self.frame_main.rowconfigure(idx, weight=ROW_WEIGHT.get(sig_name, 1))
            row = tk.Frame(self.frame_main, bg="#000000")
            row.grid(row=idx, column=0, sticky="nsew", pady=1)

            # Waveform canvas
            canvas = tk.Canvas(row, bg="#000000", highlightthickness=0)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            canvas.bind("<Configure>",
                        lambda e, s=sig_name: self._on_canvas_configure(s))
            self.canvases[sig_name] = canvas

            # Numeric readout panel
            readout = tk.Frame(row, bg="#000000", width=READOUT_WIDTH)
            readout.pack(side=tk.RIGHT, fill=tk.Y)
            readout.pack_propagate(False)
            self.readout_frames[sig_name] = readout

        # Build initial readouts
        self._rebuild_readouts()

    def _rebuild_readouts(self):
        """Rebuild the numeric readout panels for all signals."""
        for sig_name in self.display_signals:
            readout = self.readout_frames[sig_name]

            # Clear existing widgets
            for widget in readout.winfo_children():
                widget.destroy()

            cfg = SIGNAL_CONFIG.get(sig_name, {})
            color = cfg.get("color", "#FFFFFF")
            label = cfg.get("label", sig_name)

            if sig_name in PRESSURE_SIGNALS and sig_name in self.pressure_stats:
                stats = self.pressure_stats[sig_name]

                lbl_name = tk.Label(
                    readout, text=label,
                    font=("Helvetica", 11, "bold"), fg=color, bg="#000000"
                )
                lbl_name.pack(pady=(5, 0))

                txt = f"{stats['systolic']}/{stats['diastolic']}"
                lbl_val = tk.Label(
                    readout, text=txt,
                    font=("Arial", 30, "bold"), fg=color, bg="#000000"
                )
                lbl_val.pack()

                lbl_mean = tk.Label(
                    readout, text=f"({stats['mean']})",
                    font=("Arial", 14), fg=color, bg="#000000"
                )
                lbl_mean.pack()

            elif sig_name not in PRESSURE_SIGNALS:
                lbl_name = tk.Label(
                    readout, text=label,
                    font=("Helvetica", 11, "bold"), fg=color, bg="#000000"
                )
                lbl_name.pack(pady=(5, 0))

                if self.heart_rate is not None:
                    lbl_hr = tk.Label(
                        readout, text=f"{self.heart_rate}",
                        font=("Arial", 30, "bold"), fg=color, bg="#000000"
                    )
                    lbl_hr.pack()

                    lbl_bpm = tk.Label(
                        readout, text="bpm",
                        font=("Helvetica", 11), fg=color, bg="#000000"
                    )
                    lbl_bpm.pack()

    def _draw_grid_for_signal(self, signal_name):
        """Draw grid lines for a specific signal's canvas."""
        canvas = self.canvases.get(signal_name)
        if canvas is None:
            return

        canvas.delete("grid")
        canvas.delete("scale_labels")

        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w <= 1 or h <= 1:
            return

        cfg = SIGNAL_CONFIG.get(signal_name, {})
        min_val = cfg.get("min_val", 0)
        max_val = cfg.get("max_val", 100)
        grid_major = cfg.get("grid_major", 10)
        grid_minor = cfg.get("grid_minor", 5)
        grid_color = cfg.get("grid_color", "#222222")
        grid_major_color = cfg.get("grid_major_color", "#333333")
        label_color = cfg.get("label_color", "#888888")

        margin = 5
        usable = h - 2 * margin
        val_range = max_val - min_val
        if val_range <= 0:
            return

        val = min_val
        while val <= max_val:
            y = margin + (max_val - val) / val_range * usable
            is_major = (abs(val % grid_major) < 0.001 or
                        abs(val % grid_major - grid_major) < 0.001)
            if is_major:
                canvas.create_line(0, y, w, y, fill=grid_major_color,
                                   width=1, tags="grid")
                canvas.create_text(
                    6, y, text=f"{val:g}",
                    font=("Helvetica", 9), fill=label_color,
                    anchor=tk.W, tags="scale_labels"
                )
            else:
                canvas.create_line(0, y, w, y, fill=grid_color, tags="grid")
            val += grid_minor

        label_text = cfg.get("label", signal_name)
        unit = cfg.get("unit", "")
        canvas.create_text(
            w - 10, 12, text=f"{label_text} ({unit})",
            font=("Helvetica", 10, "bold"), fill=label_color,
            anchor=tk.E, tags="scale_labels"
        )

    def _value_to_y(self, signal_name, value, canvas_height):
        """Convert a signal value to a y-coordinate on its canvas."""
        cfg = SIGNAL_CONFIG.get(signal_name, {})
        min_val = cfg.get("min_val", 0)
        max_val = cfg.get("max_val", 100)
        margin = 5
        usable = canvas_height - 2 * margin
        val_range = max_val - min_val
        if val_range <= 0:
            return canvas_height // 2
        value = max(min_val, min(max_val, value))
        y = margin + (max_val - value) / val_range * usable
        return y

    def _on_canvas_configure(self, signal_name):
        """Handle canvas resize — defer grid redraw, clear stale waveform."""
        self._grid_dirty.add(signal_name)
        canvas = self.canvases.get(signal_name)
        if canvas:
            canvas.delete("waveform")
            canvas.delete("clearzone")
        self.last_draw_x[signal_name] = None
        self.last_draw_y[signal_name] = None

    def _get_steps(self) -> int:
        if _HAS_GPIO:
            s = int(getattr(encoder, "steps", 0)) - _zero_offset
            return max(0, s)
        return _steps_sim

    def do_reset(self):
        global _steps_sim, _zero_offset
        if _HAS_GPIO:
            _zero_offset = int(getattr(encoder, "steps", 0))
        else:
            _steps_sim = 0

    def _update_chamber(self):
        """Poll encoder and switch chamber/waveform case when needed."""
        if not self.running:
            return

        steps = self._get_steps()
        new_chamber = map_steps_to_chamber(steps)
        params = CHAMBER_PARAMS[new_chamber]

        self.lbl_chamber_name.config(text=params["name"].upper())
        self.lbl_steps.config(text=f"Steps: {steps}")

        if new_chamber != self.current_chamber_name:
            self.current_chamber_name = new_chamber

            # Switch to the new chamber's waveform case
            # No buffer clear — the sweep keeps going seamlessly,
            # just like a real bedside monitor where the waveform
            # morphology changes as the catheter moves.
            new_case = CHAMBER_CASES.get(new_chamber)
            if new_case and new_case in self.loaders:
                self.active_loader = self.loaders[new_case]
                self.sample_index = 0
                self.samples_per_frame = self.active_loader.fs / FRAME_RATE

                # Update numeric readouts for new chamber
                self._recompute_stats()
                self._rebuild_readouts()

                print(f"Chamber: {new_chamber} -> case '{new_case}'")

        self.root.after(50, self._update_chamber)

    def _update_waveform(self):
        """Advance playback and draw only the new pixels (incremental)."""
        if not self.running:
            return

        ms = int(1000 / FRAME_RATE)

        if not self.canvases:
            self.root.after(ms, self._update_waveform)
            return

        first_canvas = list(self.canvases.values())[0]
        w = first_canvas.winfo_width()
        if w <= 1:
            self.root.after(ms, self._update_waveform)
            return

        # Handle deferred grid redraws (from canvas resize / panel toggle)
        if self._grid_dirty:
            for sig in list(self._grid_dirty):
                self._draw_grid_for_signal(sig)
            self._grid_dirty.clear()

        clear_zone_width = 20

        # Draw SCROLL_SPEED new pixels incrementally
        for _ in range(SCROLL_SPEED):
            x = self.scan_x

            # Delete old waveform lines at the clear zone leading edge
            clear_x = (x + clear_zone_width) % w
            for sig_name in self.display_signals:
                self.canvases[sig_name].delete(f"wf_{clear_x}")

            for sig_name in self.display_signals:
                canvas = self.canvases[sig_name]
                h = canvas.winfo_height()
                if h <= 1:
                    continue

                cfg = SIGNAL_CONFIG.get(sig_name, {})
                color = cfg.get("color", "#FFFFFF")

                if sig_name in self.active_loader.signals:
                    value = self.active_loader.get_sample(
                        sig_name, self.sample_index)
                else:
                    value = 0.0

                y = self._value_to_y(sig_name, value, h)

                # Draw 1-pixel line from previous point (skip on wrap)
                px = self.last_draw_x[sig_name]
                py = self.last_draw_y[sig_name]
                if px is not None and py is not None and abs(x - px) <= 1:
                    canvas.create_line(
                        px, py, x, y,
                        fill=color, width=2,
                        tags=("waveform", f"wf_{x}"))

                self.last_draw_x[sig_name] = x
                self.last_draw_y[sig_name] = y

            self.scan_x = (self.scan_x + 1) % w
            self.sample_index = ((self.sample_index + 1)
                                 % self.active_loader.num_samples)

        # Update clear zone rectangle for each canvas
        for sig_name in self.display_signals:
            canvas = self.canvases[sig_name]
            h = canvas.winfo_height()
            if h <= 1:
                continue
            canvas.delete("clearzone")
            canvas.create_rectangle(
                self.scan_x, 0, self.scan_x + clear_zone_width, h,
                fill="#000000", outline="", tags="clearzone")

        self.root.after(ms, self._update_waveform)

    # --- Clinical scenario panel ------------------------------------------------

    def _toggle_clinical_panel(self):
        """Toggle clinical scenario overlay on/off."""
        if self.vignette_text is None:
            return

        self.clinical_panel_visible = not self.clinical_panel_visible

        if not self.clinical_panel_visible:
            # Hide overlay
            if self._clinical_overlay:
                self._clinical_overlay.place_forget()
            return

        # Build overlay if it doesn't exist yet
        if self._clinical_overlay is None:
            self._clinical_overlay = tk.Frame(
                self.parent, bg=CLINICAL_BG,
                highlightbackground=CLINICAL_BORDER, highlightthickness=2)

            # Title bar
            title_bar = tk.Frame(self._clinical_overlay, bg=CLINICAL_BORDER, height=32)
            title_bar.pack(fill=tk.X, side=tk.TOP)
            title_bar.pack_propagate(False)

            tk.Label(
                title_bar, text="CLINICAL SCENARIO",
                font=("Helvetica", 10, "bold"),
                fg=CLINICAL_TITLE_FG, bg=CLINICAL_BORDER,
            ).pack(side=tk.LEFT, padx=8, pady=4)

            close_btn = tk.Label(
                title_bar, text=" X ",
                font=("Helvetica", 10, "bold"),
                fg="#AAAAAA", bg=CLINICAL_BORDER, cursor="hand2",
            )
            close_btn.pack(side=tk.RIGHT, padx=4, pady=4)
            close_btn.bind("<Button-1>", lambda e: self._toggle_clinical_panel())

            # Separator
            tk.Frame(
                self._clinical_overlay, bg=CLINICAL_TITLE_FG, height=1
            ).pack(fill=tk.X, padx=8, pady=(0, 8))

            # Vignette text
            txt = tk.Text(
                self._clinical_overlay, wrap=tk.WORD,
                font=("Helvetica", 11), fg=CLINICAL_TEXT_FG, bg=CLINICAL_BG,
                relief=tk.FLAT, borderwidth=0, highlightthickness=0,
                padx=10, pady=4, cursor="arrow",
            )
            txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 10))
            txt.insert(tk.END, self.vignette_text)
            txt.configure(state=tk.DISABLED)

        # Show overlay — top aligned with the Patient History button
        self._clinical_overlay.place(
            relx=0.5, y=68, anchor=tk.N,
            width=CLINICAL_OVERLAY_W, height=CLINICAL_OVERLAY_H)

    def cleanup(self):
        self.running = False


# --- Keyboard controls for mock mode (generated + real-advancement) -----------
def setup_keyboard_controls(app):
    """Setup keyboard shortcuts for simulation mode."""
    if not _HAS_GPIO and hasattr(app, 'do_reset'):
        def key_plus(event=None):
            global _steps_sim
            _steps_sim += 10

        def key_minus(event=None):
            global _steps_sim
            _steps_sim = max(0, _steps_sim - 10)

        def key_reset(event=None):
            app.do_reset()

        app.root.bind("+", key_plus)
        app.root.bind("=", key_plus)
        app.root.bind("-", key_minus)
        app.root.bind("r", key_reset)
        app.root.bind("R", key_reset)

    # Clinical panel toggle (works regardless of GPIO)
    if hasattr(app, '_toggle_clinical_panel'):
        app.root.bind("h", lambda e: app._toggle_clinical_panel())
        app.root.bind("H", lambda e: app._toggle_clinical_panel())


# --- Mode toggle bar ---------------------------------------------------------
MODES = [
    ("real-advancement", "Real Patient"),
    ("generated", "Simulated Patient"),
]

TOGGLE_ACTIVE_BG = "#333333"
TOGGLE_ACTIVE_FG = "#FFD84D"
TOGGLE_INACTIVE_BG = "#111111"
TOGGLE_INACTIVE_FG = "#555555"

# Clinical scenario panel constants
CLINICAL_BG = "#0a0a0a"
CLINICAL_BORDER = "#333333"
CLINICAL_TITLE_FG = "#FFD84D"
CLINICAL_TEXT_FG = "#CCCCCC"
CLINICAL_OVERLAY_W = 420
CLINICAL_OVERLAY_H = 300


def build_toggle_bar(root, active_mode, switch_callback):
    """Create a persistent mode toggle bar at the top of the window.

    Returns the toggle frame and a dict of label widgets keyed by mode name
    so their styles can be updated when the mode changes.
    """
    bar = tk.Frame(root, bg="#000000", height=36)
    bar.pack(fill=tk.X, side=tk.TOP)
    bar.pack_propagate(False)

    labels = {}
    for mode_key, display_name in MODES:
        is_active = (mode_key == active_mode)
        lbl = tk.Label(
            bar, text=display_name,
            font=("Helvetica", 12, "bold"),
            fg=TOGGLE_ACTIVE_FG if is_active else TOGGLE_INACTIVE_FG,
            bg=TOGGLE_ACTIVE_BG if is_active else TOGGLE_INACTIVE_BG,
            padx=20, pady=6, cursor="hand2",
        )
        lbl.pack(side=tk.LEFT, padx=(2, 0))
        lbl.bind("<Button-1>", lambda e, m=mode_key: switch_callback(m))
        labels[mode_key] = lbl

    # Quit button on the far right
    quit_btn = tk.Label(
        bar, text=" EXIT ",
        font=("Helvetica", 11, "bold"),
        fg="#FF4444", bg="#1a1a1a",
        padx=10, pady=6, cursor="hand2",
    )
    quit_btn.pack(side=tk.RIGHT, padx=(0, 8))
    quit_btn.bind("<Button-1>", lambda e: root.destroy())

    return bar, labels


def update_toggle_highlight(labels, active_mode):
    """Update toggle button styles to reflect the active mode."""
    for mode_key, lbl in labels.items():
        if mode_key == active_mode:
            lbl.configure(fg=TOGGLE_ACTIVE_FG, bg=TOGGLE_ACTIVE_BG)
        else:
            lbl.configure(fg=TOGGLE_INACTIVE_FG, bg=TOGGLE_INACTIVE_BG)


# --- Main entry point --------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="PAC Insertion Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", choices=["generated", "real-advancement"],
        default=None,
        help="Waveform mode: 'generated' (math-based) or "
             "'real-advancement' (real waveforms + encoder chamber switching). "
             "If omitted, a mode selector is shown."
    )
    args = parser.parse_args()

    root = tk.Tk()
    root.title("PAC Simulator - Philips IntelliVue")
    root.configure(bg="#000000")
    root.attributes('-fullscreen', True)
    root.bind("<Escape>", lambda e: root.destroy())

    # Persistent toggle bar at the very top
    current_app = [None]
    current_mode = [None]

    def launch_mode(mode_name):
        global _steps_sim

        # Skip if already in this mode
        if mode_name == current_mode[0]:
            return

        # Cleanup previous mode
        if current_app[0] is not None:
            current_app[0].cleanup()

        # Clear the content area
        for w in content_frame.winfo_children():
            w.destroy()

        # Unbind previous keyboard controls
        for key in ("+", "=", "-", "r", "R"):
            root.unbind(key)

        # Reset step counter
        _steps_sim = 0

        # Instantiate the new mode
        if mode_name == "real-advancement":
            try:
                app = PAC_Simulator_RealAdvancement(root, parent=content_frame)
            except ValueError as e:
                print(f"ERROR: {e}")
                return
        else:
            app = PAC_Simulator_Generated(root, parent=content_frame)

        # Setup hardware/keyboard controls
        if _HAS_GPIO and reset_button is not None:
            reset_button.when_pressed = app.do_reset
        setup_keyboard_controls(app)

        current_app[0] = app
        current_mode[0] = mode_name
        update_toggle_highlight(toggle_labels, mode_name)

    # Default mode
    start_mode = args.mode or "real-advancement"

    toggle_bar, toggle_labels = build_toggle_bar(root, start_mode, launch_mode)

    # Content frame fills the rest of the window below the toggle bar
    content_frame = tk.Frame(root, bg="#000000")
    content_frame.pack(fill=tk.BOTH, expand=True)

    # Launch the initial mode
    launch_mode(start_mode)

    if _HAS_GPIO:
        print("Rotary encoder on GPIO17/18, Reset button on GPIO2")
    else:
        print("Use +/- keys to simulate encoder, R to reset")

    try:
        root.mainloop()
    finally:
        if current_app[0] is not None:
            current_app[0].cleanup()


if __name__ == "__main__":
    main()
