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
  python pac_simulator.py --mode generated         # generated mode (normal scenario)
  python pac_simulator.py --mode generated --scenario septic_shock
  python pac_simulator.py --mode real-advancement  # real waveforms + encoder
"""

import argparse
import csv
import json
import math
import os
import time
import tkinter as tk

import numpy as np

# --- Optional NeuroKit2 import (for synthetic ECG generation) ---------------------
try:
    import neurokit2 as nk
    _HAS_NEUROKIT = True
except ImportError:
    nk = None
    _HAS_NEUROKIT = False

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

# --- Patient and chamber-to-case mapping for real advancement mode -----------
DEFAULT_PATIENT = "herbert"

# Background cases — shared ECG/ABP loop, normally never switches on chamber change.
# BACKGROUND_RV_CASE is used (if it exists) during RV passage to show catheter-
# induced ectopy on the ECG.  Falls back to BACKGROUND_CASE when not in RV.
BACKGROUND_CASE = "background"
BACKGROUND_RV_CASE = "background_rv"

# PAP case per chamber — switches when catheter moves
PAP_CHAMBER_CASES = {
    "SVC":  "pap_svc",
    "RA":   "pap_ra",
    "RV":   "pap_rv",
    "PA":   "pap_pa",
    "PCWP": "pap_wedge",
}

# Signals that play from the shared background loader (never reset)
BACKGROUND_SIGNALS = {"II", "ABP"}


def discover_patients():
    """Scan waveform_data/ for available patient folders.

    A valid patient folder must contain a patient.json and at least one
    pap_* subfolder.  Returns a list of dicts sorted by nickname:
        [{"folder": "herbert", "nickname": "Herbert", ...}, ...]
    """
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "waveform_data")
    patients = []
    if not os.path.isdir(data_dir):
        return patients
    for name in sorted(os.listdir(data_dir)):
        pj = os.path.join(data_dir, name, "patient.json")
        if os.path.isfile(pj):
            try:
                with open(pj) as f:
                    info = json.load(f)
                info["folder"] = name
                patients.append(info)
            except (json.JSONDecodeError, KeyError):
                pass
    # Sort by nickname (or folder name as fallback)
    patients.sort(key=lambda p: (p.get("nickname") or p["folder"]).lower())
    return patients


def load_clinical_vignette(patient=None):
    """Load the clinical scenario vignette text for a patient."""
    patient = patient or DEFAULT_PATIENT
    vignette_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "waveform_data", patient, "clinical_data", "clinical_vignette.txt"
    )
    try:
        with open(vignette_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def load_scenario(scenario_name="normal"):
    """Load a hemodynamic scenario from scenarios/{name}.json.

    Scenarios define heart rate, ABP pressures, and per-chamber PAP pressures
    for synthetic waveform generation.
    """
    scenario_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "scenarios"
    )
    path = os.path.join(scenario_dir, f"{scenario_name}.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"WARNING: Scenario '{scenario_name}' not found at {path}, "
              f"falling back to defaults.")
        return {
            "name": "Default",
            "heart_rate": 75,
            "abp": {"systolic": 120, "diastolic": 80},
            "pap": {
                "svc":   {"mean": 3},
                "ra":    {"mean": 5},
                "rv":    {"systolic": 25, "diastolic": 4},
                "pa":    {"systolic": 25, "diastolic": 10},
                "wedge": {"mean": 10},
            },
        }


# Waveform generation parameters
HEART_RATE = 75  # bpm
SAMPLES_PER_BEAT = 100
SCROLL_SPEED = 6  # pixels per frame (constant across all chambers)
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

    def __init__(self, case_name, patient=None):
        self.case_name = case_name
        self.patient = patient or DEFAULT_PATIENT
        self.base_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "waveform_data", self.patient, case_name
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

        # Smooth coarsely-quantized ECG signals to remove stair-step artifacts.
        # Some MIMIC-III records have low ADC resolution (~0.04 mV steps vs
        # typical ~0.004 mV).  A small moving-average filter fills in the gaps.
        if "II" in self.signals:
            arr = np.array(self.signals["II"])
            unique_steps = np.diff(np.sort(np.unique(arr)))
            if len(unique_steps) > 0 and unique_steps.min() > 0.01:
                # Coarse quantization detected — apply 5-point moving average
                kernel = np.ones(5) / 5
                smoothed = np.convolve(arr, kernel, mode="same")
                self.signals["II"] = smoothed.tolist()
                print(f"  Smoothed coarse ECG (step={unique_steps.min():.4f} mV)")

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

    def compute_pressure_stats(self, signal_name, window_samples=1250,
                               current_index=None):
        """Compute sys/dia/mean from a window of pressure data.

        Uses percentiles (2nd/98th) instead of raw min/max to reject
        artifact spikes in real patient data. If current_index is given,
        uses a window centered around that position; otherwise uses the
        whole signal.
        """
        data = self.signals.get(signal_name)
        if data is None:
            return None
        if not data:
            return None

        if current_index is not None:
            half = window_samples // 2
            start = max(0, current_index - half)
            end = min(len(data), current_index + half)
            arr = np.array(data[start:end])
        else:
            arr = np.array(data)

        if len(arr) == 0:
            return None

        systolic = float(np.percentile(arr, 98))
        diastolic = float(np.percentile(arr, 2))
        mean_val = float(arr.mean())
        return {
            "systolic": round(systolic),
            "diastolic": round(diastolic),
            "mean": round(mean_val),
        }


# =============================================================================
# Synthetic Waveform Loader — generates waveforms from mathematical models
# =============================================================================

class SyntheticWaveformLoader:
    """Generate synthetic waveform data with same interface as RealWaveformLoader.

    Uses NeuroKit2 for ECG and Gaussian-component models for ABP/PAP.
    Beat-by-beat ring buffer architecture allows dynamic parameter changes
    (heart rate, pressures) that take effect at the next beat boundary.
    """

    # Respiratory variation amplitude per chamber (mmHg)
    RESP_AMP = {"svc": 2.0, "ra": 2.0, "rv": 2.0, "pa": 4.0, "wedge": 3.0}
    RESP_AMP_ABP = 1.5

    def __init__(self, signals_to_generate, scenario_params, fs=125):
        """Create synthetic waveform loader with ring buffer.

        Args:
            signals_to_generate: list of signal names, e.g. ["II", "ABP"] or ["PAP"]
            scenario_params: dict with heart_rate, and signal-specific params:
                - abp: {systolic, diastolic}
                - pap: {systolic, diastolic} or {mean} depending on chamber
                - chamber_key: "svc"|"ra"|"rv"|"pa"|"wedge" (for PAP routing)
            fs: sampling rate in Hz
        """
        self.fs = fs
        self.signal_list = [s for s in signals_to_generate if s in ("II", "ABP", "PAP")]

        # Mutable parameters — can be changed at runtime
        self._hr = scenario_params.get("heart_rate", 75)
        self._pending_hr = None  # queued HR change, applied at next beat boundary
        self._resp_rate = scenario_params.get("respiratory_rate", 14)
        self._abp_params = scenario_params.get("abp", {"systolic": 120, "diastolic": 80})
        self._pap_params = scenario_params.get("pap", {"mean": 10})
        self._chamber_key = scenario_params.get("chamber_key", "pa")

        # Ring buffer — 10 seconds of audio at fs
        self._buf_seconds = 10
        self._buf_size = int(self._buf_seconds * fs)
        self.num_samples = self._buf_size  # for modular index wrapping
        self._buffers = {sig: np.zeros(self._buf_size) for sig in self.signal_list}
        self._write_pos = 0  # next position to write in circular buffer

        # Respiratory phase — continuous across beats (radians)
        self._resp_phase = 0.0

        # ECG template — generate once, stretch for different HRs
        if "II" in self.signal_list:
            self._ecg_template = self._build_ecg_template(self._hr, fs)
        else:
            self._ecg_template = None

        # Track which signals exist (for interface compatibility)
        self.signals = {sig: True for sig in self.signal_list}

        # Order signal_list by SIGNAL_DISPLAY_ORDER
        self.signal_list = [s for s in SIGNAL_DISPLAY_ORDER
                            if s in self.signals]

        # Pre-fill the buffer with several seconds of data
        self._prefill()

        print(f"Synthetic loader (ring buffer): {self.signal_list}, "
              f"HR={self._hr}, buf={self._buf_size} samples")

    def _prefill(self):
        """Fill the entire ring buffer with beats at current parameters."""
        self._write_pos = 0
        filled = 0
        while filled < self._buf_size:
            beat_len = self._fill_next_beat()
            filled += beat_len

    # --- Public API ---

    def get_sample(self, signal_name, index):
        """Get a single sample from the ring buffer."""
        buf = self._buffers.get(signal_name)
        if buf is None:
            return 0.0
        return float(buf[index % self._buf_size])

    def ensure_filled_to(self, target_index):
        """Ensure the buffer has data up to target_index.

        Called by the animation loop before reading samples. Generates
        new beats as needed to stay ahead of the read position.
        """
        # How far ahead is the write position from the target?
        target_wrapped = target_index % self._buf_size
        # Fill if write_pos is within 2 beats of being caught
        margin = int(60.0 / max(self._hr, 40) * self.fs * 2)
        distance = (self._write_pos - target_wrapped) % self._buf_size
        while distance < margin:
            self._fill_next_beat()
            distance = (self._write_pos - target_wrapped) % self._buf_size

    def set_heart_rate(self, new_hr):
        """Queue a heart rate change; takes effect at the next beat boundary."""
        new_hr = max(40, min(180, int(new_hr)))
        self._pending_hr = new_hr

    @property
    def heart_rate(self):
        """Current heart rate."""
        return self._hr

    def set_pressures(self, abp=None, pap=None):
        """Update pressure parameters; takes effect at the next beat."""
        if abp is not None:
            self._abp_params = abp
        if pap is not None:
            self._pap_params = pap

    def compute_pressure_stats(self, signal_name, window_samples=1250):
        """Compute sys/dia/mean from recent buffer data."""
        buf = self._buffers.get(signal_name)
        if buf is None:
            return None
        # Use the most recent window_samples worth of data
        end = self._write_pos
        if window_samples >= self._buf_size:
            segment = buf
        else:
            indices = np.arange(end - window_samples, end) % self._buf_size
            segment = buf[indices]

        return {
            "systolic": round(float(segment.max())),
            "diastolic": round(float(segment.min())),
            "mean": round(float(segment.mean())),
        }

    # --- Beat-by-beat generation core ---

    def _fill_next_beat(self):
        """Generate one beat of all signals and write into the ring buffer.

        Returns the number of samples written (= samples_per_beat).
        """
        # Apply any pending HR change at beat boundary
        if self._pending_hr is not None:
            self._hr = self._pending_hr
            self._pending_hr = None
            # Rebuild ECG template for new HR
            if self._ecg_template is not None:
                self._ecg_template = self._build_ecg_template(self._hr, self.fs)

        samples_per_beat = int(60.0 / self._hr * self.fs)
        t_warped = self._build_warped_time(self._hr, samples_per_beat)

        # Compute respiratory offset for this beat
        beat_duration_s = samples_per_beat / self.fs
        resp_center_phase = self._resp_phase + np.pi * (self._resp_rate / 60.0) * beat_duration_s
        resp_offset_per_sample = np.sin(
            self._resp_phase + 2 * np.pi * (self._resp_rate / 60.0)
            * np.arange(samples_per_beat) / self.fs
        )

        for sig_name in self.signal_list:
            if sig_name == "II":
                beat = self._generate_ecg_beat(samples_per_beat)
            elif sig_name == "ABP":
                beat = self._generate_abp_beat(t_warped, samples_per_beat)
                # Add respiratory variation
                beat += self.RESP_AMP_ABP / 2.0 * resp_offset_per_sample
            elif sig_name == "PAP":
                beat = self._generate_pap_beat(t_warped, samples_per_beat)
                # Add respiratory variation (chamber-specific amplitude)
                resp_amp = self.RESP_AMP.get(self._chamber_key, 0.0)
                if resp_amp > 0:
                    beat += resp_amp / 2.0 * resp_offset_per_sample

            # Write into ring buffer (wrapping)
            buf = self._buffers[sig_name]
            for i in range(len(beat)):
                buf[(self._write_pos + i) % self._buf_size] = beat[i]

        # Advance write position and respiratory phase
        self._write_pos = (self._write_pos + samples_per_beat) % self._buf_size
        self._resp_phase += 2 * np.pi * (self._resp_rate / 60.0) * beat_duration_s
        # Keep phase in [0, 2π) to avoid float drift
        self._resp_phase %= (2 * np.pi)

        return samples_per_beat

    # --- ECG template generation and stretching ---

    @staticmethod
    def _build_ecg_template(heart_rate, fs):
        """Generate a single ECG beat template using NeuroKit2.

        Returns a 1D numpy array representing one beat at the given HR.
        """
        if not _HAS_NEUROKIT:
            # Flat line fallback
            samples = int(60.0 / heart_rate * fs)
            return np.zeros(samples)

        # Generate a few seconds of ECG to extract a clean beat
        duration = 5
        raw = nk.ecg_simulate(
            duration=duration,
            sampling_rate=fs,
            heart_rate=heart_rate,
            heart_rate_std=0,  # no variability for template
            method="ecgsyn",
            noise=0.005,
        )
        ecg = np.array(raw) * 0.4

        # Find R-peaks and extract one beat (second beat to avoid edge effects)
        samples_per_beat = int(60.0 / heart_rate * fs)
        threshold = ecg.max() * 0.5
        min_dist = int(0.4 * fs)
        peaks = []
        i = 0
        while i < len(ecg):
            if ecg[i] > threshold:
                peak_idx = i
                while i < len(ecg) and ecg[i] > threshold:
                    if ecg[i] > ecg[peak_idx]:
                        peak_idx = i
                    i += 1
                peaks.append(peak_idx)
                i = peak_idx + min_dist
            else:
                i += 1

        if len(peaks) >= 3:
            # Extract from second R-peak to third R-peak
            start = peaks[1]
            end = peaks[2]
            template = ecg[start:end].copy()
        else:
            # Fallback: take one beat-length from the middle
            mid = len(ecg) // 2
            template = ecg[mid:mid + samples_per_beat].copy()

        return template

    def _generate_ecg_beat(self, target_samples):
        """Stretch/compress the ECG template to match current HR."""
        if self._ecg_template is None or len(self._ecg_template) == 0:
            return np.zeros(target_samples)

        template = self._ecg_template
        if len(template) == target_samples:
            return template.copy()

        # Interpolate template to target length
        x_old = np.linspace(0, 1, len(template))
        x_new = np.linspace(0, 1, target_samples)
        return np.interp(x_new, x_old, template)

    # --- Warped time axis for tachycardia realism ---

    @staticmethod
    def _build_warped_time(heart_rate, samples_per_beat):
        """Build a non-linear time axis where systole is relatively fixed.

        At any heart rate, systole occupies ~300ms (slight shortening at high HR).
        Diastole absorbs the remaining time.  The returned array maps each sample
        index to a position in template-space [0, 1) where 0-0.4 is systole and
        0.4-1.0 is diastole.
        """
        beat_duration_s = 60.0 / heart_rate
        systole_s = max(0.25, 0.32 - 0.0003 * heart_rate)

        systole_frac = systole_s / beat_duration_s
        systole_samples = int(round(samples_per_beat * systole_frac))
        diastole_samples = samples_per_beat - systole_samples

        t_systole = np.linspace(0, 0.4, systole_samples, endpoint=False)
        t_diastole = np.linspace(0.4, 1.0, diastole_samples, endpoint=False)

        return np.concatenate([t_systole, t_diastole])

    # --- ABP beat generation ---

    def _generate_abp_beat(self, t_warped, samples_per_beat):
        """Generate one ABP beat scaled to current pressure parameters."""
        t = t_warped
        systolic = self._abp_params.get("systolic", 120)
        diastolic = self._abp_params.get("diastolic", 80)

        systolic_peak = np.exp(-((t - 0.22) ** 2) / 0.010)
        dicrotic_bump = 0.28 * np.exp(-((t - 0.50) ** 2) / 0.018)
        beat = systolic_peak + dicrotic_bump

        beat_min, beat_max = beat.min(), beat.max()
        if beat_max > beat_min:
            beat = (beat - beat_min) / (beat_max - beat_min)

        pulse_pressure = systolic - diastolic
        return diastolic + beat * pulse_pressure

    # --- PAP beat generation (chamber-specific) ---

    def _generate_pap_beat(self, t_warped, samples_per_beat):
        """Generate one PAP beat for the current chamber and pressure params."""
        t = t_warped
        chamber_key = self._chamber_key
        pap_params = self._pap_params

        if chamber_key in ("svc", "ra"):
            mean_p = pap_params.get("mean", 5)
            amplitude = max(mean_p * 0.6, 2)
            systolic = mean_p + amplitude
            diastolic = max(mean_p - amplitude * 0.5, 0)

            a_wave = 0.35 * np.exp(-((t - 0.12) ** 2) / 0.006)
            c_wave = 0.15 * np.exp(-((t - 0.25) ** 2) / 0.004)
            v_wave = 0.30 * np.exp(-((t - 0.55) ** 2) / 0.008)
            beat = 0.2 + a_wave + c_wave + v_wave

        elif chamber_key == "rv":
            systolic = pap_params.get("systolic", 25)
            diastolic = pap_params.get("diastolic", 4)
            sys_plateau = np.exp(-((t - 0.22) ** 2) / 0.016)
            beat = sys_plateau

        elif chamber_key == "pa":
            systolic = pap_params.get("systolic", 25)
            diastolic = pap_params.get("diastolic", 10)
            sys_peak = np.exp(-((t - 0.22) ** 2) / 0.012)
            dicrotic = 0.28 * np.exp(-((t - 0.50) ** 2) / 0.020)
            beat = sys_peak + dicrotic

        elif chamber_key == "wedge":
            mean_p = pap_params.get("mean", 10)
            amplitude = max(mean_p * 0.4, 2)
            systolic = mean_p + amplitude
            diastolic = max(mean_p - amplitude * 0.5, 0)
            a_wave = 0.30 * np.exp(-((t - 0.15) ** 2) / 0.008)
            v_wave = 0.35 * np.exp(-((t - 0.52) ** 2) / 0.012)
            beat = 0.45 + a_wave + v_wave

        else:
            mean_p = pap_params.get("mean", 10)
            beat = np.full(samples_per_beat, 0.5)
            systolic = mean_p + 2
            diastolic = max(mean_p - 2, 0)

        beat_min, beat_max = beat.min(), beat.max()
        if beat_max > beat_min:
            beat = (beat - beat_min) / (beat_max - beat_min)

        pulse_pressure = systolic - diastolic
        return diastolic + beat * pulse_pressure


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
        self.frame_bottom = tk.Frame(self.parent, bg="#1a1a1a", height=38)
        self.frame_bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self.frame_bottom.pack_propagate(False)

        self.btn_reset = tk.Button(
            self.frame_bottom, text="RESET TO SVC",
            font=("Helvetica", 10, "bold"), bg="#444444", fg="#FFFFFF",
            activebackground="#444444", activeforeground="#FFFFFF",
            relief=tk.FLAT, overrelief=tk.FLAT,
            borderwidth=0, highlightthickness=0,
            command=self.do_reset, padx=15, pady=3
        )
        self.btn_reset.pack(side=tk.LEFT, padx=15, pady=6)

        mode_text = ("HARDWARE MODE - Rotary Encoder Active" if _HAS_GPIO
                     else "SIMULATION MODE - Use +/- keys or Reset button")
        self.lbl_mode = tk.Label(
            self.frame_bottom, text=mode_text,
            font=("Helvetica", 9), fg="#888888", bg="#1a1a1a"
        )
        self.lbl_mode.pack(side=tk.LEFT, padx=15, pady=6)

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
        global _zero_offset
        if _HAS_GPIO:
            raw = int(getattr(encoder, "steps", 0))
            if raw < _zero_offset:
                _zero_offset = raw  # Follow encoder down so forward turns register immediately
            return raw - _zero_offset
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
    """Waveform mode with encoder-driven chamber advancement.

    Supports two data sources:
      - "real": Loads MIMIC-III waveform clips from CSV files
      - "synthetic": Generates waveforms from math models (NeuroKit2 ECG,
        Gaussian ABP/PAP) using configurable scenario files

    Both use the same multi-signal display and background/PAP split architecture.
    """

    def __init__(self, root, parent=None, data_source="real", scenario=None,
                 patient=None):
        self.root = root
        self.parent = parent or root
        self.data_source = data_source
        self.patient = patient or DEFAULT_PATIENT
        self._scenario = None  # set by _init_synthetic_loaders if applicable
        self._hr_label = None  # set by _create_ui if synthetic mode
        self._pa_label = None  # set by _create_ui if synthetic mode

        if data_source == "synthetic":
            self.root.title("PAC Simulator - Generated Mode")
        else:
            self.root.title("PAC Simulator - Real Advancement Mode")
        if parent is None:
            self.root.geometry("1280x800")
            self.root.configure(bg="#000000")

        if data_source == "synthetic":
            self._init_synthetic_loaders(scenario)
        else:
            self._init_real_loaders()

        # Start in SVC
        self.current_chamber_name = "SVC"
        self.active_pap_loader = self.pap_loaders[PAP_CHAMBER_CASES["SVC"]]

        # Determine display signals from background + PAP loaders
        all_available = set(self.bg_loader.signal_list)
        for loader in self.pap_loaders.values():
            all_available.update(loader.signal_list)
        self.display_signals = [s for s in SIGNAL_DISPLAY_ORDER
                                if s in all_available]

        # Playback state — two independent sample indices
        self.bg_sample_index = 0       # never resets on chamber switch
        self.pap_sample_index = 0      # resets to 0 on chamber switch
        self.running = True
        self.sample_accumulator = 0.0
        self.samples_per_frame = self.bg_loader.fs / FRAME_RATE

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

        # Start animation, chamber polling, and periodic stats refresh
        self._update_waveform()
        self._update_chamber()
        self._periodic_stats_update()

    def _init_real_loaders(self):
        """Load waveform data from MIMIC-III CSV files."""
        pt = self.patient

        # Background loader (ECG II, ABP — shared, normally never switches)
        try:
            self.bg_loader = RealWaveformLoader(BACKGROUND_CASE, patient=pt)
        except (FileNotFoundError, ValueError) as e:
            raise ValueError(
                f"Could not load background case '{BACKGROUND_CASE}': {e}\n"
                f"Ensure waveform_data/{pt}/{BACKGROUND_CASE}/ "
                f"exists with II.csv, ABP.csv, and metadata.json"
            )
        self.bg_loader_default = self.bg_loader  # remember the normal bg

        # Optional RV-specific background (e.g., catheter-induced ectopy)
        self.bg_loader_rv = None
        try:
            self.bg_loader_rv = RealWaveformLoader(BACKGROUND_RV_CASE,
                                                   patient=pt)
            print(f"Loaded RV-specific background for {pt} "
                  f"({self.bg_loader_rv.num_samples} samples)")
        except (FileNotFoundError, ValueError):
            pass  # no RV background — that's fine, just use the default

        # Per-chamber PAP loaders
        self.pap_loaders = {}
        loaded_pap_cases = set()
        for chamber, case_name in PAP_CHAMBER_CASES.items():
            if case_name not in loaded_pap_cases:
                try:
                    self.pap_loaders[case_name] = RealWaveformLoader(
                        case_name, patient=pt)
                    loaded_pap_cases.add(case_name)
                except (FileNotFoundError, ValueError) as e:
                    print(f"WARNING: Could not load PAP case '{case_name}' "
                          f"for chamber {chamber}: {e}")

        if not self.pap_loaders:
            raise ValueError(
                "No PAP waveform cases could be loaded.\n"
                f"Ensure waveform_data/{pt}/ contains: "
                + ", ".join(set(PAP_CHAMBER_CASES.values()))
            )

    def _init_synthetic_loaders(self, scenario):
        """Generate waveform data from mathematical models."""
        scenario = scenario or load_scenario("normal")
        self._scenario = scenario  # keep reference for dynamic changes
        hr = scenario["heart_rate"]
        resp_rate = scenario.get("respiratory_rate", 14)
        print(f"Loading scenario: {scenario.get('name', 'Unknown')} "
              f"(HR={hr}, ABP={scenario['abp']['systolic']}/"
              f"{scenario['abp']['diastolic']})")

        # Background: ECG + ABP (shared, never switches)
        self.bg_loader = SyntheticWaveformLoader(
            signals_to_generate=["II", "ABP"],
            scenario_params={
                "heart_rate": hr,
                "respiratory_rate": resp_rate,
                "abp": scenario["abp"],
            },
        )
        self.bg_loader_default = self.bg_loader
        self.bg_loader_rv = None  # synthetic mode has no separate RV background

        # Per-chamber PAP loaders
        self.pap_loaders = {}
        chamber_map = {
            "pap_svc":   "svc",
            "pap_ra":    "ra",
            "pap_rv":    "rv",
            "pap_pa":    "pa",
            "pap_wedge": "wedge",
        }
        for case_name, chamber_key in chamber_map.items():
            pap_params = scenario["pap"].get(chamber_key, {"mean": 10})
            self.pap_loaders[case_name] = SyntheticWaveformLoader(
                signals_to_generate=["PAP"],
                scenario_params={
                    "heart_rate": hr,
                    "respiratory_rate": resp_rate,
                    "pap": pap_params,
                    "chamber_key": chamber_key,
                },
            )

    def _recompute_stats(self):
        """Recompute pressure stats and heart rate from appropriate loaders."""
        self.pressure_stats = {}
        for sig in self.display_signals:
            if sig not in PRESSURE_SIGNALS:
                continue
            # Route to correct loader and get current playback index
            if sig in BACKGROUND_SIGNALS:
                loader = self.bg_loader
                idx = getattr(self, 'bg_sample_index', None)
            else:
                loader = self.active_pap_loader
                idx = getattr(self, 'pap_sample_index', None)
            if sig in loader.signals:
                stats = loader.compute_pressure_stats(sig, current_index=idx)
                if stats:
                    self.pressure_stats[sig] = stats

        # Heart rate — in synthetic mode, use the loader's known HR directly;
        # in real mode, estimate from ECG R-peak detection.
        if self.data_source == "synthetic":
            self.heart_rate = self.bg_loader.heart_rate
        else:
            ecg_data = self.bg_loader.signals.get("II")
            if ecg_data is not None:
                self.heart_rate = self._compute_heart_rate_from(ecg_data,
                                                                self.bg_loader.fs)
            else:
                self.heart_rate = None

    def _periodic_stats_update(self):
        """Refresh pressure stats and readouts every 3 seconds."""
        if not self.running:
            return
        old_stats = {sig: dict(s) for sig, s in self.pressure_stats.items()}
        self._recompute_stats()
        # Only rebuild readouts if numbers actually changed
        if self.pressure_stats != old_stats:
            self._rebuild_readouts()
        self.root.after(3000, self._periodic_stats_update)

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
        self.frame_top = tk.Frame(self.parent, bg="#1a1a1a", height=40)
        self.frame_top.pack(fill=tk.X, side=tk.TOP)
        self.frame_top.pack_propagate(False)

        params = CHAMBER_PARAMS[self.current_chamber_name]
        self.lbl_chamber_name = tk.Label(
            self.frame_top, text=params["name"].upper(),
            font=("Helvetica", 14, "bold"), fg="#FFD84D", bg="#1a1a1a"
        )
        self.lbl_chamber_name.pack(side=tk.LEFT, padx=15, pady=6)

        self.lbl_steps = tk.Label(
            self.frame_top, text="Steps: 0",
            font=("Helvetica", 11), fg="#AAAAAA", bg="#1a1a1a"
        )
        self.lbl_steps.pack(side=tk.RIGHT, padx=15, pady=6)

        # Bottom bar
        self.frame_bottom = tk.Frame(self.parent, bg="#1a1a1a", height=38)
        self.frame_bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self.frame_bottom.pack_propagate(False)

        self.btn_reset = tk.Button(
            self.frame_bottom, text="RESET TO SVC",
            font=("Helvetica", 10, "bold"), bg="#444444", fg="#FFFFFF",
            activebackground="#444444", activeforeground="#FFFFFF",
            relief=tk.FLAT, overrelief=tk.FLAT,
            borderwidth=0, highlightthickness=0,
            command=self.do_reset, padx=15, pady=3
        )
        self.btn_reset.pack(side=tk.LEFT, padx=15, pady=6)

        if self.data_source == "synthetic":
            mode_text = f"GENERATED MODE - {self._scenario.get('name', 'Custom')}"
        elif _HAS_GPIO:
            mode_text = "REAL ADVANCEMENT - Rotary Encoder Active"
        else:
            mode_text = "REAL ADVANCEMENT - Use +/- keys or Reset button"
        self.lbl_mode = tk.Label(
            self.frame_bottom, text=mode_text,
            font=("Helvetica", 9), fg="#888888", bg="#1a1a1a"
        )
        self.lbl_mode.pack(side=tk.LEFT, padx=15, pady=6)

        # HR adjustment controls (generated mode only)
        self._hr_label = None
        if self.data_source == "synthetic":
            btn_style = dict(
                font=("Helvetica", 12, "bold"), bg="#444444", fg="#FFFFFF",
                activebackground="#555555", activeforeground="#FFFFFF",
                relief=tk.FLAT, overrelief=tk.FLAT,
                borderwidth=0, highlightthickness=0,
                padx=12, pady=3,
            )
            # HR +5 button (packed first = rightmost)
            tk.Button(
                self.frame_bottom, text="HR +5",
                command=lambda: self._change_hr(5), **btn_style
            ).pack(side=tk.RIGHT, padx=2, pady=6)

            # HR label
            self._hr_label = tk.Label(
                self.frame_bottom,
                text=f"HR: {self.bg_loader.heart_rate}",
                font=("Helvetica", 12, "bold"), fg="#00FF00", bg="#1a1a1a",
                padx=8,
            )
            self._hr_label.pack(side=tk.RIGHT, padx=2, pady=6)

            # HR -5 button
            tk.Button(
                self.frame_bottom, text="HR -5",
                command=lambda: self._change_hr(-5), **btn_style
            ).pack(side=tk.RIGHT, padx=2, pady=6)

            # Spacer between HR and PA controls
            tk.Label(
                self.frame_bottom, text="  ", bg="#1a1a1a",
            ).pack(side=tk.RIGHT, padx=4)

            # PA pressure +5 button (packed first = rightmost)
            tk.Button(
                self.frame_bottom, text="PA +5",
                command=lambda: self._change_pa_pressure(5), **btn_style
            ).pack(side=tk.RIGHT, padx=2, pady=6)

            # PA pressure label
            self._pa_label = tk.Label(
                self.frame_bottom,
                text=self._pa_label_text(),
                font=("Helvetica", 12, "bold"), fg="#FFFF00", bg="#1a1a1a",
                padx=8,
            )
            self._pa_label.pack(side=tk.RIGHT, padx=2, pady=6)

            # PA pressure -5 button
            tk.Button(
                self.frame_bottom, text="PA -5",
                command=lambda: self._change_pa_pressure(-5), **btn_style
            ).pack(side=tk.RIGHT, padx=2, pady=6)

        # Main area: stacked signal rows
        self.frame_main = tk.Frame(self.parent, bg="#000000")
        self.frame_main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Clinical scenario overlay (hidden by default)
        self.vignette_text = load_clinical_vignette(self.patient)
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
        global _zero_offset
        if _HAS_GPIO:
            raw = int(getattr(encoder, "steps", 0))
            if raw < _zero_offset:
                _zero_offset = raw  # Follow encoder down so forward turns register immediately
            return raw - _zero_offset
        return _steps_sim

    def do_reset(self):
        global _steps_sim, _zero_offset
        if _HAS_GPIO:
            _zero_offset = int(getattr(encoder, "steps", 0))
        else:
            _steps_sim = 0

    def _change_hr(self, delta):
        """Change heart rate by delta bpm across all synthetic loaders."""
        if self.data_source != "synthetic":
            return
        new_hr = max(40, min(180, self.bg_loader.heart_rate + delta))
        # Apply to background loader and all PAP loaders
        self.bg_loader.set_heart_rate(new_hr)
        for loader in self.pap_loaders.values():
            loader.set_heart_rate(new_hr)
        # Update HR label immediately
        if self._hr_label:
            self._hr_label.config(text=f"HR: {new_hr}")
        # Recompute stats and readouts shortly after the beat changes
        self.root.after(500, self._recompute_stats)
        self.root.after(500, self._rebuild_readouts)

    def _pa_label_text(self):
        """Build label text for PA pressure controls based on current chamber."""
        loader = self.active_pap_loader
        if not hasattr(loader, '_pap_params'):
            return "PA: --"
        params = loader._pap_params
        chamber = loader._chamber_key
        if chamber in ("rv", "pa"):
            sys = params.get("systolic", 25)
            dia = params.get("diastolic", 10)
            return f"PA: {sys}/{dia}"
        else:
            mean = params.get("mean", 10)
            return f"PA: {mean}"

    def _change_pa_pressure(self, delta):
        """Adjust PA pressures on the active PAP loader."""
        if self.data_source != "synthetic":
            return
        loader = self.active_pap_loader
        if not hasattr(loader, '_pap_params'):
            return
        params = loader._pap_params.copy()
        chamber = loader._chamber_key

        if chamber in ("rv", "pa"):
            # Adjust systolic; diastolic follows proportionally
            new_sys = max(5, params.get("systolic", 25) + delta)
            new_dia = max(0, params.get("diastolic", 10) + delta // 2)
            params["systolic"] = new_sys
            params["diastolic"] = new_dia
        else:
            # Adjust mean for SVC/RA/Wedge
            new_mean = max(1, params.get("mean", 10) + delta)
            params["mean"] = new_mean

        loader.set_pressures(pap=params)
        # Update label
        if hasattr(self, '_pa_label') and self._pa_label:
            self._pa_label.config(text=self._pa_label_text())
        self.root.after(500, self._recompute_stats)
        self.root.after(500, self._rebuild_readouts)

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
            old_chamber = self.current_chamber_name
            self.current_chamber_name = new_chamber

            # Switch the PAP loader for the new chamber
            new_pap_case = PAP_CHAMBER_CASES.get(new_chamber)
            if new_pap_case and new_pap_case in self.pap_loaders:
                self.active_pap_loader = self.pap_loaders[new_pap_case]
                self.pap_sample_index = 0  # only PAP restarts

                # Swap background loader for RV if an RV-specific
                # background exists (e.g., catheter-induced ectopy)
                if (self.data_source == "real"
                        and getattr(self, "bg_loader_rv", None) is not None):
                    if new_chamber == "RV" and old_chamber != "RV":
                        self.bg_loader = self.bg_loader_rv
                        self.bg_sample_index = 0
                        print("  -> Switched to RV background (ectopy)")
                    elif new_chamber != "RV" and old_chamber == "RV":
                        self.bg_loader = self.bg_loader_default
                        self.bg_sample_index = 0
                        print("  -> Switched back to default background")

                # Update numeric readouts and PA label for new chamber
                self._recompute_stats()
                self._rebuild_readouts()
                if hasattr(self, '_pa_label') and self._pa_label:
                    self._pa_label.config(text=self._pa_label_text())

                print(f"Chamber: {new_chamber} -> PAP case '{new_pap_case}'")

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

        # Ensure synthetic ring buffers are filled ahead of read position
        if self.data_source == "synthetic":
            self.bg_loader.ensure_filled_to(self.bg_sample_index + SCROLL_SPEED + 50)
            self.active_pap_loader.ensure_filled_to(
                self.pap_sample_index + SCROLL_SPEED + 50)

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

                # Route to correct loader and index
                if sig_name in BACKGROUND_SIGNALS:
                    loader = self.bg_loader
                    idx = self.bg_sample_index
                else:
                    loader = self.active_pap_loader
                    idx = self.pap_sample_index

                if sig_name in loader.signals:
                    value = loader.get_sample(sig_name, idx)
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
            self.bg_sample_index = ((self.bg_sample_index + 1)
                                    % self.bg_loader.num_samples)
            self.pap_sample_index = ((self.pap_sample_index + 1)
                                     % self.active_pap_loader.num_samples)

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

# Patient tab styling (cyan theme to distinguish from mode tabs)
PT_ACTIVE_BG = "#1a3a4a"
PT_ACTIVE_FG = "#00CCFF"
PT_INACTIVE_BG = "#111111"
PT_INACTIVE_FG = "#336677"

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
    bar = tk.Frame(root, bg="#000000", height=45)
    bar.pack(fill=tk.X, side=tk.TOP)
    bar.pack_propagate(False)

    labels = {}
    for mode_key, display_name in MODES:
        is_active = (mode_key == active_mode)
        lbl = tk.Label(
            bar, text=display_name,
            font=("Helvetica", 15, "bold"),
            fg=TOGGLE_ACTIVE_FG if is_active else TOGGLE_INACTIVE_FG,
            bg=TOGGLE_ACTIVE_BG if is_active else TOGGLE_INACTIVE_BG,
            padx=24, pady=8, cursor="hand2",
        )
        lbl.pack(side=tk.LEFT, padx=(2, 0))
        lbl.bind("<Button-1>", lambda e, m=mode_key: switch_callback(m))
        labels[mode_key] = lbl

    # Quit button on the far right
    quit_btn = tk.Label(
        bar, text=" EXIT ",
        font=("Helvetica", 14, "bold"),
        fg="#FF4444", bg="#1a1a1a",
        padx=12, pady=8, cursor="hand2",
    )
    quit_btn.pack(side=tk.RIGHT, padx=(0, 10))
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
    parser.add_argument(
        "--scenario", default="normal",
        help="Scenario name for generated mode (e.g., 'normal', 'septic_shock'). "
             "Loads from scenarios/{name}.json"
    )
    parser.add_argument(
        "--patient", default=None,
        help="Patient folder name (e.g., 'herbert', 'p003914'). "
             "If omitted, the first discovered patient is used."
    )
    args = parser.parse_args()

    root = tk.Tk()
    root.title("PAC Simulator - Philips IntelliVue")
    root.configure(bg="#000000")
    root.attributes('-fullscreen', True)
    root.bind("<Escape>", lambda e: root.destroy())

    # Discover available patients
    available_patients = discover_patients()
    if not available_patients:
        print("ERROR: No patients found in waveform_data/")
        return
    for p in available_patients:
        nick = p.get("nickname") or p["folder"]
        print(f"  Found patient: {nick} ({p['folder']})")

    # Select starting patient
    current_patient_idx = [0]
    if args.patient:
        for i, p in enumerate(available_patients):
            if p["folder"] == args.patient:
                current_patient_idx[0] = i
                break

    # Persistent toggle bar at the very top
    current_app = [None]
    current_mode = [None]

    def _get_patient_folder():
        return available_patients[current_patient_idx[0]]["folder"]

    def _get_patient_label():
        p = available_patients[current_patient_idx[0]]
        nick = p.get("nickname") or p["folder"]
        return nick

    def launch_mode(mode_name, force=False):
        global _steps_sim

        # Skip if already in this mode (unless forced, e.g. patient change)
        if mode_name == current_mode[0] and not force:
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

        patient_folder = _get_patient_folder()

        # Instantiate the new mode
        if mode_name == "real-advancement":
            try:
                app = PAC_Simulator_RealAdvancement(
                    root, parent=content_frame, data_source="real",
                    patient=patient_folder
                )
            except ValueError as e:
                print(f"ERROR: {e}")
                return
        else:
            # Generated mode uses synthetic waveforms via same UI
            try:
                scenario = load_scenario(args.scenario)
                app = PAC_Simulator_RealAdvancement(
                    root, parent=content_frame,
                    data_source="synthetic", scenario=scenario
                )
            except Exception as e:
                print(f"ERROR loading generated mode: {e}")
                # Fallback to old generated mode
                app = PAC_Simulator_Generated(root, parent=content_frame)

        # Setup hardware/keyboard controls
        if _HAS_GPIO and reset_button is not None:
            reset_button.when_pressed = app.do_reset
        setup_keyboard_controls(app)

        current_app[0] = app
        current_mode[0] = mode_name
        update_toggle_highlight(toggle_labels, mode_name)

        # Show patient tabs only in real-advancement mode
        _show_patient_tabs(mode_name == "real-advancement")

    def select_patient(idx):
        """Switch to a specific patient and reload real-advancement mode."""
        if idx == current_patient_idx[0]:
            return
        current_patient_idx[0] = idx
        print(f"Switching to patient: {_get_patient_label()}")
        _update_patient_tabs()
        # Force reload real-advancement mode with new patient
        current_mode[0] = None  # clear so launch_mode doesn't skip
        launch_mode("real-advancement", force=True)

    def _update_patient_tabs():
        """Highlight the active patient tab."""
        for i, lbl in patient_tabs.items():
            if i == current_patient_idx[0]:
                lbl.configure(fg=PT_ACTIVE_FG, bg=PT_ACTIVE_BG)
            else:
                lbl.configure(fg=PT_INACTIVE_FG, bg=PT_INACTIVE_BG)

    def _show_patient_tabs(visible):
        """Show or hide patient tabs based on current mode."""
        for lbl in patient_tabs.values():
            if visible:
                lbl.pack(side=tk.RIGHT, padx=(2, 0))
            else:
                lbl.pack_forget()
        if visible:
            patient_sep.pack(side=tk.RIGHT, padx=(4, 0))
        else:
            patient_sep.pack_forget()

    # Default mode
    start_mode = args.mode or "real-advancement"

    toggle_bar, toggle_labels = build_toggle_bar(root, start_mode, launch_mode)

    # Patient tabs (right side of toggle bar, before EXIT)
    # Separator label between mode tabs and patient tabs
    patient_sep = tk.Label(
        toggle_bar, text="│", font=("Helvetica", 15),
        fg="#333333", bg="#000000", pady=8,
    )
    patient_tabs = {}
    if len(available_patients) > 1:
        patient_sep.pack(side=tk.RIGHT, padx=(4, 0))
        for i, p in reversed(list(enumerate(available_patients))):
            nick = p.get("nickname") or p["folder"]
            is_active = (i == current_patient_idx[0])
            lbl = tk.Label(
                toggle_bar, text=f" {nick} ",
                font=("Helvetica", 14, "bold"),
                fg=PT_ACTIVE_FG if is_active else PT_INACTIVE_FG,
                bg=PT_ACTIVE_BG if is_active else PT_INACTIVE_BG,
                padx=14, pady=8, cursor="hand2",
            )
            lbl.pack(side=tk.RIGHT, padx=(2, 0))
            lbl.bind("<Button-1>", lambda e, idx=i: select_patient(idx))
            patient_tabs[i] = lbl

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
