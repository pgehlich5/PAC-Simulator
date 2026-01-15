#!/usr/bin/env python3
"""
PAC Insertion Simulator with Philips IntelliVue-style Pressure Waveforms
Displays authentic arterial pressure traces as catheter advances through cardiac chambers
"""

import tkinter as tk
import math
import time

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
    "RA": 1000,
    "RV": 2500,
    "PA": 4000,
    "PCWP": 5000,
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

# Waveform generation parameters
HEART_RATE = 75  # bpm
SAMPLES_PER_BEAT = 100
SCROLL_SPEED = 4  # pixels per frame (constant across all chambers)
FRAME_RATE = 60  # Hz

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
            # A-wave (atrial contraction) around 0.1
            a_wave = 0.3 * math.exp(-((t - 0.1) ** 2) / 0.002) if 0.05 < t < 0.2 else 0
            # C-wave (ventricular contraction, tricuspid bulge) around 0.25
            c_wave = 0.15 * math.exp(-((t - 0.25) ** 2) / 0.001) if 0.2 < t < 0.35 else 0
            # V-wave (venous filling) around 0.55
            v_wave = 0.25 * math.exp(-((t - 0.55) ** 2) / 0.004) if 0.4 < t < 0.75 else 0
            # Base pressure
            base = 0.2
            value = base + a_wave + c_wave + v_wave
            points.append(min(1.0, value))

    elif waveform_type == "rv":  # Right Ventricle
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            if t < 0.35:  # Systole - rapid rise and slower fall
                if t < 0.1:
                    value = t / 0.1  # Rapid upstroke
                else:
                    value = 1.0 - ((t - 0.1) / 0.25) * 0.3  # Plateau/slight decline
            else:  # Diastole - rapid drop to low pressure
                value = 0.7 * math.exp(-(t - 0.35) / 0.1)
            points.append(min(1.0, max(0.0, value)))

    elif waveform_type == "pa":  # Pulmonary Artery
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            if t < 0.35:  # Systole
                if t < 0.1:
                    value = t / 0.1  # Rapid upstroke
                else:
                    value = 1.0 - ((t - 0.1) / 0.25) * 0.4  # Gradual decline
            else:  # Diastole with dicrotic notch
                # Dicrotic notch around t=0.35
                notch = -0.15 * math.exp(-((t - 0.36) ** 2) / 0.0005) if 0.34 < t < 0.38 else 0
                value = 0.6 * math.exp(-(t - 0.35) / 0.25) + 0.4 + notch
            points.append(min(1.0, max(0.0, value)))

    elif waveform_type == "wedge":  # PCWP (similar to LA pressure, resembles CVP but damped)
        for i in range(SAMPLES_PER_BEAT):
            t = i / SAMPLES_PER_BEAT
            # A-wave (atrial contraction) - more prominent than CVP
            a_wave = 0.35 * math.exp(-((t - 0.15) ** 2) / 0.003) if 0.05 < t < 0.3 else 0
            # V-wave (venous return during ventricular systole) around 0.5
            v_wave = 0.4 * math.exp(-((t - 0.5) ** 2) / 0.006) if 0.35 < t < 0.7 else 0
            # Base pressure (higher than CVP)
            base = 0.5
            value = base + a_wave + v_wave
            points.append(min(1.0, value))

    return points


# --- Hardware or mock setup ------------------------------------------------------
if _HAS_GPIO:
    encoder = RotaryEncoder(a=17, b=18, max_steps=10000, wrap=False)
    reset_button = Button(2)
    _zero_offset = 0
else:
    encoder = None
    reset_button = None
    _zero_offset = 0


# --- Main Application ------------------------------------------------------------
class PAC_Simulator:
    def __init__(self, root):
        self.root = root
        self.root.title("PAC Simulator - Philips IntelliVue")
        self.root.geometry("1280x800")
        self.root.configure(bg="#000000")

        # Waveform data buffer (stores pressure values for display)
        self.waveform_buffer = []  # List to store all waveform points with x positions
        self.current_waveform = generate_waveform("cvp")
        self.waveform_index = 0
        self.scan_x = 0  # Current x-position of the scan line
        self.current_chamber_name = "SVC"  # Track chamber for smooth transitions

        # Setup UI
        self._create_ui()

        # Animation control
        self.last_update = time.time()
        self.running = True

        # Start update loops
        self._update_waveform()
        self._update_chamber()

    def _create_ui(self):
        """Create Philips IntelliVue-style interface."""

        # Top status bar (dark gray background)
        self.frame_top = tk.Frame(self.root, bg="#1a1a1a", height=60)
        self.frame_top.pack(fill=tk.X, side=tk.TOP)
        self.frame_top.pack_propagate(False)

        # Chamber name label (left side, yellow text)
        self.lbl_chamber_name = tk.Label(
            self.frame_top,
            text="SUPERIOR VENA CAVA",
            font=("Helvetica", 18, "bold"),
            fg="#FFD84D",
            bg="#1a1a1a"
        )
        self.lbl_chamber_name.pack(side=tk.LEFT, padx=20, pady=10)

        # Steps counter (right side)
        self.lbl_steps = tk.Label(
            self.frame_top,
            text="Steps: 0",
            font=("Helvetica", 14),
            fg="#AAAAAA",
            bg="#1a1a1a"
        )
        self.lbl_steps.pack(side=tk.RIGHT, padx=20, pady=10)

        # Main waveform display area
        self.frame_main = tk.Frame(self.root, bg="#000000")
        self.frame_main.pack(fill=tk.BOTH, expand=True)

        # Waveform canvas (scrolling pressure trace) - goes first, takes most space
        self.canvas = tk.Canvas(
            self.frame_main,
            bg="#000000",
            highlightthickness=0
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 5), pady=10)

        # Pressure values display on RIGHT side (like IntelliVue PAP display)
        self.frame_values = tk.Frame(self.frame_main, bg="#000000", width=400)
        self.frame_values.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 35), pady=10)
        self.frame_values.pack_propagate(False)

        # Pressure display in horizontal format: "28/15 (21)" - compact spacing
        self.lbl_pressure = tk.Label(
            self.frame_values,
            text="5/2 (3)",
            font=("Arial", 50, "bold"),  # Arial is narrower than Courier, closer to Philips
            fg="#FFCC00",  # Yellow like PAP display
            bg="#000000",
            justify=tk.CENTER
        )
        self.lbl_pressure.pack(expand=True, padx=20)

        # Bottom control bar
        self.frame_bottom = tk.Frame(self.root, bg="#1a1a1a", height=50)
        self.frame_bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self.frame_bottom.pack_propagate(False)

        # Reset button
        self.btn_reset = tk.Button(
            self.frame_bottom,
            text="RESET TO SVC",
            font=("Helvetica", 12, "bold"),
            bg="#444444",
            fg="#FFFFFF",
            activebackground="#666666",
            command=self.do_reset,
            padx=20,
            pady=5
        )
        self.btn_reset.pack(side=tk.LEFT, padx=20, pady=10)

        # Mode indicator
        if _HAS_GPIO:
            mode_text = "HARDWARE MODE - Rotary Encoder Active"
        else:
            mode_text = "SIMULATION MODE - Use +/- keys or Reset button"

        self.lbl_mode = tk.Label(
            self.frame_bottom,
            text=mode_text,
            font=("Helvetica", 10),
            fg="#888888",
            bg="#1a1a1a"
        )
        self.lbl_mode.pack(side=tk.LEFT, padx=20, pady=10)

        # Draw initial grid
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._draw_grid()

    def _draw_grid(self):
        """Draw IntelliVue-style grid lines with fixed 0-50 mmHg pressure scale."""
        self.canvas.delete("grid")
        self.canvas.delete("scale_labels")

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 1 or h <= 1:
            return

        # Pressure scale: 0-50 mmHg
        min_pressure = 0
        max_pressure = 50
        margin_top = 20
        margin_bottom = 20
        usable_height = h - margin_top - margin_bottom

        grid_color = "#332800"  # Dark yellow/brown for subtle grid
        major_grid_color = "#4a3800"  # Slightly brighter dark yellow

        # Horizontal grid lines (pressure divisions) - every 5 mmHg
        for pressure in range(min_pressure, max_pressure + 1, 5):
            y = margin_top + (max_pressure - pressure) / max_pressure * usable_height

            # Major lines at 0, 10, 20, 30, 40, 50 mmHg
            if pressure % 10 == 0:
                self.canvas.create_line(0, y, w, y, fill=major_grid_color, width=2, tags="grid")
                # Add pressure labels on the left - larger and brighter
                self.canvas.create_text(
                    8, y,
                    text=str(pressure),
                    font=("Helvetica", 12, "bold"),
                    fill="#FFDD00",  # Brighter yellow
                    anchor=tk.W,
                    tags="scale_labels"
                )
            else:
                # Minor lines at 5, 15, 25, 35, 45 mmHg
                self.canvas.create_line(0, y, w, y, fill=grid_color, tags="grid")

    def _on_canvas_resize(self, event=None):
        """Handle canvas resize events."""
        # pylint: disable=unused-argument
        self._draw_grid()

    def _get_steps(self) -> int:
        """Return logical steps with zeroing via offset for hardware or mock."""
        if _HAS_GPIO:
            s = int(getattr(encoder, "steps", 0)) - _zero_offset
            return max(0, s)
        return _steps_sim

    def do_reset(self):
        """Reset encoder position to SVC (zero)."""
        global _steps_sim, _zero_offset
        if _HAS_GPIO:
            _zero_offset = int(getattr(encoder, "steps", 0))
        else:
            _steps_sim = 0

    def _update_chamber(self):
        """Update chamber detection and UI labels based on encoder position."""
        if not self.running:
            return

        steps = self._get_steps()
        new_chamber = map_steps_to_chamber(steps)
        params = CHAMBER_PARAMS[new_chamber]

        # Update labels
        self.lbl_chamber_name.config(text=params["name"].upper())
        # Update pressure display in horizontal format: "SYS/DIA (MEAN)" - compact spacing
        pressure_text = f"{params['systolic']}/{params['diastolic']} ({params['mean']})"
        self.lbl_pressure.config(text=pressure_text)
        self.lbl_steps.config(text=f"Steps: {steps}")

        # Update waveform if chamber changed (but don't reset index - keep scrolling smooth)
        if new_chamber != self.current_chamber_name:
            self.current_chamber_name = new_chamber
            self.current_waveform = generate_waveform(params["waveform_type"])
            # Don't reset waveform_index - let it continue for smooth transition

        # Schedule next update
        self.root.after(50, self._update_chamber)

    def _update_waveform(self):
        """Animate sweeping scan line waveform display."""
        if not self.running:
            return

        w = self.canvas.winfo_width()
        if w <= 1:
            self.root.after(int(1000 / FRAME_RATE), self._update_waveform)
            return

        # Get current parameters from tracked chamber
        params = CHAMBER_PARAMS[self.current_chamber_name]
        systolic = params["systolic"]
        diastolic = params["diastolic"]
        color = params["color"]

        clear_zone_width = 20

        # Remove old points that are about to be overwritten by the scan line
        # Keep only points that are NOT in the zone about to be erased
        self.waveform_buffer = [
            point for point in self.waveform_buffer
            if not (self.scan_x <= point['x'] < self.scan_x + clear_zone_width + SCROLL_SPEED)
        ]

        # Add new waveform samples at scan line position
        for _ in range(SCROLL_SPEED):
            # Get next waveform point (normalized 0-1)
            norm_value = self.current_waveform[self.waveform_index]

            # Scale to actual pressure range
            pressure = diastolic + (systolic - diastolic) * norm_value

            # Store point with its x-position
            self.waveform_buffer.append({
                'x': self.scan_x,
                'pressure': pressure,
                'systolic': systolic,
                'diastolic': diastolic
            })

            # Advance scan line position
            self.scan_x = (self.scan_x + 1) % w

            # Advance waveform index
            self.waveform_index = (self.waveform_index + 1) % len(self.current_waveform)

        # Also clean up points that have wrapped around (old scan from previous loops)
        # Keep only the most recent screen width of data
        if len(self.waveform_buffer) > w * 1.5:
            self.waveform_buffer = self.waveform_buffer[-int(w * 1.2):]

        # Redraw waveform
        self._draw_waveform_sweep(color)

        # Schedule next frame at consistent interval
        frame_delay = int(1000 / FRAME_RATE)
        self.root.after(frame_delay, self._update_waveform)

    def _draw_waveform_sweep(self, color: str):
        """Draw waveform with sweeping scan line (oscilloscope style)."""
        self.canvas.delete("waveform")
        self.canvas.delete("clearzone")

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 1 or h <= 1 or len(self.waveform_buffer) < 2:
            return

        # Fixed pressure scale: 0-50 mmHg
        min_pressure = 0
        max_pressure = 50
        margin_top = 20
        margin_bottom = 20
        usable_height = h - margin_top - margin_bottom
        clear_zone_width = 20

        # Build continuous waveform points, excluding those in the clear zone
        waveform_points = []

        for point in self.waveform_buffer:
            # Skip points that are in the clear zone
            if self.scan_x <= point['x'] < self.scan_x + clear_zone_width:
                continue

            # Map pressure to y-coordinate using fixed 0-50 mmHg scale
            pressure = point['pressure']
            # Clamp pressure to scale range
            pressure = max(min_pressure, min(max_pressure, pressure))
            # Calculate y position (inverted - higher pressure = lower y value)
            y = margin_top + (max_pressure - pressure) / max_pressure * usable_height

            waveform_points.append((point['x'], y))

        # Sort points by x-coordinate to draw properly
        waveform_points.sort(key=lambda p: p[0])

        # Draw waveform as continuous segments, breaking at the clear zone
        if len(waveform_points) >= 2:
            current_segment = [waveform_points[0]]

            for i in range(1, len(waveform_points)):
                # Check if there's a gap (clear zone between points)
                if abs(waveform_points[i][0] - waveform_points[i-1][0]) > clear_zone_width + 5:
                    # Draw current segment
                    if len(current_segment) >= 2:
                        points_flat = []
                        for px, py in current_segment:
                            points_flat.extend([px, py])
                        self.canvas.create_line(
                            *points_flat,
                            fill=color,
                            width=2,
                            smooth=False,
                            tags="waveform"
                        )
                    # Start new segment
                    current_segment = [waveform_points[i]]
                else:
                    current_segment.append(waveform_points[i])

            # Draw final segment
            if len(current_segment) >= 2:
                points_flat = []
                for px, py in current_segment:
                    points_flat.extend([px, py])
                self.canvas.create_line(
                    *points_flat,
                    fill=color,
                    width=2,
                    smooth=False,
                    tags="waveform"
                )

        # Draw clear zone (black eraser area only, no yellow line)
        self.canvas.create_rectangle(
            self.scan_x, 0,
            self.scan_x + clear_zone_width, h,
            fill="#000000",
            outline="",
            tags="clearzone"
        )

    def cleanup(self):
        """Cleanup on exit."""
        self.running = False


# --- Keyboard controls for mock mode ---------------------------------------------
def setup_keyboard_controls(app):
    """Setup keyboard shortcuts for simulation mode."""
    if not _HAS_GPIO:
        def key_plus(event=None):
            # pylint: disable=unused-argument
            global _steps_sim
            _steps_sim += 10

        def key_minus(event=None):
            # pylint: disable=unused-argument
            global _steps_sim
            _steps_sim = max(0, _steps_sim - 10)

        def key_reset(event=None):
            # pylint: disable=unused-argument
            app.do_reset()

        app.root.bind("+", key_plus)
        app.root.bind("=", key_plus)
        app.root.bind("-", key_minus)
        app.root.bind("r", key_reset)
        app.root.bind("R", key_reset)


# --- Main entry point ------------------------------------------------------------
def main():
    root = tk.Tk()
    app = PAC_Simulator(root)

    # Setup hardware button if available
    if _HAS_GPIO and reset_button is not None:
        reset_button.when_pressed = app.do_reset

    # Setup keyboard controls for mock mode
    setup_keyboard_controls(app)

    # Print startup message
    if _HAS_GPIO:
        print("PAC Simulator - Hardware Mode")
        print("Rotary encoder on GPIO17/18, Reset button on GPIO2")
    else:
        print("PAC Simulator - Mock Mode")
        print("Use +/- keys to simulate encoder, R to reset")

    try:
        root.mainloop()
    finally:
        app.cleanup()


if __name__ == "__main__":
    main()
