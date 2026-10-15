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


def bold_font(size: int):
    for name in BOLD_FONTS:
        try:
            return ImageFont.truetype(name, size), True
        except OSError:
            continue
    return ImageFont.load_default(size=size), False


def text_mask(lines: list[str], nx: int, ny: int, ss: int = 4, line_ratio=(0.6, 0.4)) -> np.ndarray:
    """Coverage (0..1) of centred text lines on an ny x nx grid (row 0 = bottom).

    The first line is the title and gets the larger share of the height.
    """
    lines = [ln for ln in lines if ln][:2]
    if not lines:
        return np.zeros((ny, nx))
    W, H = nx * ss, ny * ss
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    ratios = line_ratio if len(lines) == 2 else (1.0,)
    pad = 0.08 * H
    avail = H - 2 * pad
    y = pad
    for text, ratio in zip(lines, ratios):
        slot = avail * ratio
        size = max(6, int(slot * 0.95))
        font, is_bold = bold_font(size)
        # Shrink to fit the width.
        while size > 6 and draw.textlength(text, font=font) > W * 0.94:
            size = int(size * 0.92)
            font, is_bold = bold_font(size)
        draw.text((W / 2, y + slot / 2), text, font=font, fill=255, anchor="mm")
        y += slot
    if not is_bold:
        img = img.filter(ImageFilter.MaxFilter(3))
    arr = np.asarray(img, dtype=float) / 255.0
    arr = arr.reshape(ny, ss, nx, ss).mean(axis=(1, 3))
    return arr[::-1]  # image row 0 is the top (north); grid row 0 is the bottom
