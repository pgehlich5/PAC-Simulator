"""Generate update_icon.png — a blue circular-refresh button for the Update icon.

Matches the Start icon style (filled circle + white glyph) but in blue so the
two desktop icons are easy to tell apart on the Pi touchscreen.
Run from anywhere: python pi_setup/make_update_icon.py
"""

import math
import os
from PIL import Image, ImageDraw

SCALE = 4
SIZE = 256
S = SIZE * SCALE

BLUE = (33, 150, 243, 255)        # update blue (#2196F3)
BLUE_DARK = (21, 101, 192, 255)   # ring for depth
WHITE = (255, 255, 255, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Filled circle with a slightly darker ring
margin = int(S * 0.06)
d.ellipse([margin, margin, S - margin, S - margin], fill=BLUE_DARK)
ring = int(S * 0.10)
d.ellipse([ring, ring, S - ring, S - ring], fill=BLUE)

# White circular-refresh arrow: an open arc with an arrowhead at one end.
cx = cy = S / 2
r = S * 0.24                 # arc radius (midline)
w = int(S * 0.055)          # arc thickness
bbox = [cx - r, cy - r, cx + r, cy + r]
start_deg, end_deg = 35, 300   # leave a gap; arrowhead goes at end_deg
d.arc(bbox, start_deg, end_deg, fill=WHITE, width=w)

# Arrowhead at the end of the arc, pointing along the tangent (clockwise).
end = math.radians(end_deg)
tip = (cx + (r + w * 1.6) * math.cos(end + math.radians(8)),
       cy + (r + w * 1.6) * math.sin(end + math.radians(8)))
base_out = (cx + (r + w * 2.2) * math.cos(end - math.radians(10)),
            cy + (r + w * 2.2) * math.sin(end - math.radians(10)))
base_in = (cx + (r - w * 2.2) * math.cos(end - math.radians(10)),
           cy + (r - w * 2.2) * math.sin(end - math.radians(10)))
d.polygon([tip, base_out, base_in], fill=WHITE)

img = img.resize((SIZE, SIZE), Image.LANCZOS)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_icon.png")
img.save(out)
print(f"Wrote {out}")
