"""Display frame for a terrain tile: a box whose walls rise above the tile's highest point,
with an optional front band carrying a raised title and stats line."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .label import text_mask
from .mesh import Mesh, heightmap_solid, hexahedron
from .model import _filament_grams, _surface_area
from .printers import PRINTERS, snap


@dataclass
class FrameParams:
    tile_w: float  # terrain tile footprint (x), mm
    tile_d: float  # terrain tile footprint (y), mm
    tile_h: float  # terrain tile height including its highest point, mm
    wall_mm: float = 4.0  # side and back wall thickness
    clearance_mm: float = 0.4  # gap per side between tile and frame
    headroom_mm: float = 2.0  # how far the walls rise above the tile's highest point
    floor: bool = True  # a floor the tile drops onto (otherwise an open ring)
    floor_mm: float = 1.6
    title: str = ""
    stats: str = ""
    band_mm: float = 16.0  # depth of the front band when it carries text
    text_mm: float = 0.6  # raised text height
    printer: str = "a1mini"
    layer_mm: float = 0.2


def _box(x0, y0, x1, y1, z0, z1) -> Mesh:
    b = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], float)
    t = b.copy()
    t[:, 2] = z1
    return hexahedron(b, t)


def build_frame(p: FrameParams) -> tuple[Mesh, dict]:
    """Return the frame mesh (several touching closed shells) and info for the UI."""
    printer = PRINTERS.get(p.printer, PRINTERS["a1mini"])
    layer = p.layer_mm
    floor = snap(p.floor_mm, layer) if p.floor else 0.0
    text = [t.strip() for t in (p.title, p.stats) if t and t.strip()]
    text_h = snap(p.text_mm, layer)
    wall = p.wall_mm
    band = max(p.band_mm, wall) if text else wall

    in_w, in_d = p.tile_w + 2 * p.clearance_mm, p.tile_d + 2 * p.clearance_mm
    out_w, out_d = in_w + 2 * wall, in_d + wall + band
    # Walls cover everything: the tile (sitting on the floor) plus headroom above its peak.
    height = snap(floor + p.tile_h + p.headroom_mm, layer)
    x_in0, y_in0 = wall, band  # opening's south-west corner

    shells = [
        _box(0, band, wall, out_d, 0, height),  # west wall
        _box(out_w - wall, band, out_w, out_d, 0, height),  # east wall
        _box(wall, out_d - wall, out_w - wall, out_d, 0, height),  # north wall
    ]
    # Front (south) band along the full width; raised text on its top.
    if text:
        cell = 0.25
        nx = int(round(out_w / cell)) + 1
        ny = int(round(band / cell)) + 1
        z = np.full((ny, nx), height)
        # Text area inset from the edges so it never runs into the wall corners.
        pad = max(1.0, wall / 2)
        i0, i1 = int(pad / cell), nx - int(pad / cell)
        j0, j1 = int(pad / cell), ny - int(pad / cell)
        z[j0:j1, i0:i1] += text_h * text_mask(text, i1 - i0, j1 - j0)
        shells.append(heightmap_solid(z, out_w / (nx - 1), band / (ny - 1)))
    else:
        shells.append(_box(0, 0, out_w, band, 0, height))
    if floor:
        shells.append(_box(x_in0, y_in0, x_in0 + in_w, y_in0 + in_d, 0, floor))
    mesh = Mesh.merge(shells)

    m = 2 * printer.margin
    fits = (out_w <= printer.bed_x - m and out_d <= printer.bed_y - m) or (
        out_w <= printer.bed_y - m and out_d <= printer.bed_x - m)
    warnings = []
    if not fits:
        # Largest square-ish tile whose frame still fits the bed.
        max_tile = min(printer.bed_x, printer.bed_y) - m - 2 * p.clearance_mm - max(2 * wall, wall + band)
        warnings.append(
            f"The frame is {out_w:.0f} x {out_d:.0f} mm, too big for the {printer.name} bed "
            f"({printer.bed_x:.0f} x {printer.bed_y:.0f} mm). Rebuild the terrain with its longest side at "
            f"{max_tile:.0f} mm or less, or use thinner walls / no text band."
        )
    if height > printer.bed_z:
        warnings.append(f"The frame is {height:.0f} mm tall; the {printer.name} prints up to {printer.bed_z:.0f} mm.")

    info = {
        "outer_mm": [round(out_w, 1), round(out_d, 1), round(height, 1)],
        "opening_mm": [round(in_w, 1), round(in_d, 1)],
        "tile_offset_mm": [round(x_in0 + p.clearance_mm, 2), round(y_in0 + p.clearance_mm, 2), floor],
        "tile_mm": [p.tile_w, p.tile_d, p.tile_h],
        "headroom_mm": round(height - floor - p.tile_h, 2),
        "floor_mm": floor,
        "band_mm": band,
        "has_text": bool(text),
        "fits": bool(fits and height <= printer.bed_z),
        "printer": printer.to_dict(),
        "warnings": warnings,
        "filament_g": _filament_grams(mesh.volume(), _surface_area(mesh)),
    }
    return mesh, info


def frame_guide(info: dict) -> list[str]:
    steps = [
        "Open the frame .3mf in Bambu Studio and print it standing on its bottom as it loads. "
        "No supports needed. 3 walls make the thin sides solid.",
        f"Drop the terrain tile in from the top{' onto the floor' if info['floor_mm'] else ''}. "
        f"There's a gap of {info['opening_mm'][0] - info['tile_mm'][0]:.1f} mm in total around it, and the walls "
        f"rise {info['headroom_mm']:.1f} mm above the highest peak.",
    ]
    if info["has_text"]:
        steps.append("The title and stats are raised on the front band. For coloured text, add a filament "
                     f"change at the top of the frame (Z {info['outer_mm'][2]:.2f} mm) in Bambu Studio.")
    steps.append(f"Estimated filament: about {info['filament_g']} g PLA.")
    return steps
