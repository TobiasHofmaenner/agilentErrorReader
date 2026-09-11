#!/usr/bin/env python3
"""Generate the application icon: a turbo rotor glyph with a status dot.

Run:  uv run --group icon python packaging/make_icon.py

Produces (committed to the repo):
    agilent_gui/assets/app.png   256x256 window / taskbar icon
    packaging/app.ico            multi-size Windows icon for PyInstaller
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ASSETS = os.path.join(ROOT, "agilent_gui", "assets")

PANEL = (23, 28, 38, 255)      # #171c26
BORDER = (44, 55, 74, 255)     # #2c374a
BLADE = (79, 195, 247, 255)    # #4fc3f7
HUB = (200, 210, 225, 255)
OK = (105, 219, 124, 255)      # #69db7c status dot

S = 1024
OUT = 256


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, rad = 70, 190
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=rad, fill=PANEL, outline=BORDER, width=18)

    cx, cy = 470, 512
    # eight rotor blades
    for i in range(8):
        a0 = math.radians(i * 45)
        a1 = a0 + math.radians(22)
        inner, outer = 120, 330
        pts = [
            (cx + inner * math.cos(a0), cy + inner * math.sin(a0)),
            (cx + outer * math.cos(a0 + math.radians(8)), cy + outer * math.sin(a0 + math.radians(8))),
            (cx + outer * math.cos(a1 + math.radians(8)), cy + outer * math.sin(a1 + math.radians(8))),
            (cx + inner * math.cos(a1), cy + inner * math.sin(a1)),
        ]
        d.polygon(pts, fill=BLADE)
    d.ellipse([cx - 110, cy - 110, cx + 110, cy + 110], fill=HUB)
    d.ellipse([cx - 45, cy - 45, cx + 45, cy + 45], fill=PANEL)
    # status dot, bottom right
    d.ellipse([730, 700, 900, 870], fill=OK, outline=PANEL, width=16)
    return img.resize((OUT, OUT), Image.LANCZOS)


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    icon = render()
    icon.save(os.path.join(ASSETS, "app.png"))
    icon.save(os.path.join(ROOT, "packaging", "app.ico"),
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote agilent_gui/assets/app.png and packaging/app.ico")


if __name__ == "__main__":
    main()
