"""Rasterise label text (activity name and stats) into a height mask."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BOLD_FONTS = [
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "arialbd.ttf",
    "Arial Bold.ttf",
]


# Inter Bold, then Inter-like humanist sans fallbacks (Segoe UI on Windows, SF on macOS).
INTER_FONTS = [
    "Inter-Bold.ttf",
    "Inter-Bold.otf",
    "C:/Windows/Fonts/Inter-Bold-slnt=0.ttf",
    "C:/Windows/Fonts/Inter-Bold.ttf",
    "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
    "/Library/Fonts/Inter-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/SFNS.ttf",
]
FONTS = {"default": BOLD_FONTS, "inter": INTER_FONTS + BOLD_FONTS}


def bold_font(size: int, family: str = "default"):
    for name in FONTS.get(family, BOLD_FONTS):
        try:
            font = ImageFont.truetype(name, size)
            try:  # variable fonts (e.g. Inter) default to their lightest weight
                font.set_variation_by_name("Bold")
            except (OSError, ValueError, AttributeError):
                pass
            return font, True
        except OSError:
            continue
    return ImageFont.load_default(size=size), False


def text_mask(lines: list[str], nx: int, ny: int, ss: int = 4, family: str = "default",
              upper: bool = False) -> np.ndarray:
    """Coverage (0..1) of centred text lines on an ny x nx grid (row 0 = bottom).

    The first line is the title and gets the larger share of the height.
    `family` picks the font ("default" or "inter"); `upper` capitalises every line.
    """
    if upper:
        lines = [ln.upper() for ln in lines]
    lines = [ln for ln in lines if ln][:3]
    if not lines:
        return np.zeros((ny, nx))
    W, H = nx * ss, ny * ss
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    ratios = {1: (1.0,), 2: (0.6, 0.4), 3: (0.48, 0.26, 0.26)}[len(lines)]
    pad = 0.08 * H
    avail = H - 2 * pad
    y = pad
    for text, ratio in zip(lines, ratios):
        slot = avail * ratio
        size = max(6, int(slot * 0.95))
        font, is_bold = bold_font(size, family)
        # Shrink to fit the width.
        while size > 6 and draw.textlength(text, font=font) > W * 0.94:
            size = int(size * 0.92)
            font, is_bold = bold_font(size, family)
        draw.text((W / 2, y + slot / 2), text, font=font, fill=255, anchor="mm")
        y += slot
    if not is_bold:
        img = img.filter(ImageFilter.MaxFilter(3))
    arr = np.asarray(img, dtype=float) / 255.0
    arr = arr.reshape(ny, ss, nx, ss).mean(axis=(1, 3))
    return arr[::-1]  # image row 0 is the top (north); grid row 0 is the bottom
