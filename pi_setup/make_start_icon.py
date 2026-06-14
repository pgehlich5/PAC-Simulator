"""Generate start_icon.png — a bold green play button for the Start desktop icon.

A large, high-contrast target is easy to find and tap on the Pi touchscreen.
Run from anywhere: python pi_setup/make_start_icon.py
"""

import os
from PIL import Image, ImageDraw

SCALE = 4                     # supersample for smooth edges
SIZE = 256                    # final icon size (px)
S = SIZE * SCALE

GREEN = (40, 167, 69, 255)    # play-button green (#28a745)
GREEN_DARK = (30, 126, 52, 255)  # ring for a little depth
WHITE = (255, 255, 255, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Outer ring (darker) + inner filled circle (green)
margin = int(S * 0.06)
d.ellipse([margin, margin, S - margin, S - margin], fill=GREEN_DARK)
ring = int(S * 0.10)
d.ellipse([ring, ring, S - ring, S - ring], fill=GREEN)

# Centered play triangle, nudged right so it looks optically centered
cx, cy = S / 2, S / 2
r = S * 0.24
dx = S * 0.04  # optical-center nudge
triangle = [
    (cx - r * 0.85 + dx, cy - r),
    (cx - r * 0.85 + dx, cy + r),
    (cx + r + dx,        cy),
]
d.polygon(triangle, fill=WHITE)

img = img.resize((SIZE, SIZE), Image.LANCZOS)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_icon.png")
img.save(out)
print(f"Wrote {out}")
