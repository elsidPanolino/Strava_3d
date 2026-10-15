"""Display frame for a terrain tile: a box whose walls rise above the tile's highest point,
with an optional front band carrying a raised title and stats line."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .label import text_mask
from .mesh import Mesh, heightmap_solid, hexahedron, masked_heightmap_solid
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
    text_mm: float = 0.6  # text height (raised) or depth (engraved)
    text_style: str = "raised"  # raised (letters stand up) or engraved (letters cut into the band)
    font: str = "inter"  # "inter" (Inter Bold or an Inter-like sans) or "default"
    uppercase: bool = True  # all capitals for title and stats
    printer: str = "a1mini"
    layer_mm: float = 0.2
    mount: bool = False  # keyhole slots in the back (underside of the floor) for hanging on a wall
    mount_floor_mm: float = 4.0  # floor thickness needed to hide the keyholes
    screw_head_mm: float = 9.0  # keyhole entry diameter (screw head up to ~8.5 mm)
    screw_shank_mm: float = 4.5  # slot width (screw shank up to ~4 mm)
    slot_len_mm: float = 10.0  # how far the screw slides up into the slot
    vents: bool = True  # air holes in the floor so the tile slides in and out without suction
    push_hole_mm: float = 16.0  # central hole: also lets a finger push the tile out from behind
    vent_mm: float = 5.0
    finger_notches: bool = True  # rounded dips in the side walls to pinch the tile's edges
    notch_mm: float = 24.0  # notch width


def _box(x0, y0, x1, y1, z0, z1) -> Mesh:
    b = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], float)
    t = b.copy()
    t[:, 2] = z1
    return hexahedron(b, t)


def _seg_dist(gx, gy, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    t = np.clip(((gx - ax) * vx + (gy - ay) * vy) / max(vx * vx + vy * vy, 1e-12), 0, 1)
    return np.hypot(gx - (ax + t * vx), gy - (ay + t * vy))


def _keyhole_patch(x0, y0, w, h, cx, cy, p: FrameParams, floor: float) -> list[Mesh]:
    """A w x h block of floor with a keyhole cut in from the underside.

    Layers (bottom up): the keyhole outline (entry hole + narrow slot) the screw passes
    through; a channel as wide as the head for it to slide in; a solid roof.
    """
    cell = 0.2
    nx, ny = int(round(w / cell)) + 1, int(round(h / cell)) + 1
    dx, dy = w / (nx - 1), h / (ny - 1)
    gx = np.arange(nx)[None, :] * dx
    gy = np.arange(ny)[:, None] * dy
    R, r, L = p.screw_head_mm / 2, p.screw_shank_mm / 2, p.slot_len_mm
    lcx, lcy = cx - x0, cy - y0
    hole = np.hypot(gx - lcx, gy - lcy) - R
    slot = _seg_dist(gx, gy, lcx, lcy, lcx, lcy + L)
    lip, channel_top, roof = 1.6, floor - 0.6, floor
    shells = []
    for z0, z1, f in ((0.0, lip, np.minimum(hole, slot - r)),  # keep material outside hole + slot
                      (lip, channel_top, slot - R)):  # channel for the head to slide along
        z = np.full((ny, nx), z1)
        m = masked_heightmap_solid(z, f, dx, dy, z_bottom=z0)
        m.vertices[:, 0] += x0
        m.vertices[:, 1] += y0
        shells.append(m)
    shells.append(_box(x0, y0, x0 + w, y0 + h, channel_top, roof))
    return shells


def _hole_patch(x0, y0, w, h, cx, cy, radius, floor) -> list[Mesh]:
    """A w x h block of floor with a round hole right through it."""
    cell = 0.2
    nx, ny = int(round(w / cell)) + 1, int(round(h / cell)) + 1
    dx, dy = w / (nx - 1), h / (ny - 1)
    f = np.hypot(np.arange(nx)[None, :] * dx - (cx - x0), np.arange(ny)[:, None] * dy - (cy - y0)) - radius
    m = masked_heightmap_solid(np.full((ny, nx), floor), f, dx, dy)
    m.vertices[:, 0] += x0
    m.vertices[:, 1] += y0
    return [m]


def _build_floor(x0, y0, w, d, floor, p: FrameParams):
    """Floor slab with optional cut-outs, laid out in rows and surrounded by plain floor.

    Rows: wall-mount keyholes 12 mm below the top edge; air vents plus a central push-out
    hole across the middle. Returns the shells, keyhole centres and hole centres/radii.
    """
    rows = []  # (y0, height, [(x0, width, builder)])
    keyholes, holes = [], []
    if p.mount:
        pw, ph = p.screw_head_mm + 6, p.slot_len_mm + p.screw_head_mm + 6
        py0 = y0 + d - ph - 12
        keyholes = [(x0 + w * f, py0 + 3 + p.screw_head_mm / 2) for f in (0.25, 0.75)]
        rows.append((py0, ph, [(cx - pw / 2, pw, lambda a, b, ww, hh, cx=cx, cy=cy:
                                _keyhole_patch(a, b, ww, hh, cx, cy, p, floor)) for cx, cy in keyholes]))
    if p.vents:
        # Push-out hole in the centre, small vents either side: air gets in and out as the
        # tile goes in or comes out, and a finger can press the tile out from behind.
        big, small = p.push_hole_mm / 2, p.vent_mm / 2
        ph = 2 * big + 4
        cy = y0 + d / 2
        if rows:  # keep clear of the keyhole row on small tiles
            cy = min(cy, rows[0][0] - ph / 2 - 3)
        holes = [(x0 + w / 2, cy, big), (x0 + w * 0.2, cy, small), (x0 + w * 0.8, cy, small)]
        rows.append((cy - ph / 2, ph, [(hx - (2 * r + 4) / 2, 2 * r + 4, lambda a, b, ww, hh, hx=hx, r=r:
                                         _hole_patch(a, b, ww, hh, hx, cy, r, floor)) for hx, _, r in holes]))
    rows.sort(key=lambda r: r[0])
    shells, y = [], y0
    for ry, rh, patches in rows:
        if ry - y > 1e-6:
            shells.append(_box(x0, y, x0 + w, ry, 0, floor))
        x = x0
        for px, pw, build in sorted(patches, key=lambda t: t[0]):
            if px - x > 1e-6:
                shells.append(_box(x, ry, px, ry + rh, 0, floor))
            shells += build(px, ry, pw, rh)
            x = px + pw
        if x0 + w - x > 1e-6:
            shells.append(_box(x, ry, x0 + w, ry + rh, 0, floor))
        y = ry + rh
    if y0 + d - y > 1e-6:
        shells.append(_box(x0, y, x0 + w, y0 + d, 0, floor))
    return shells, keyholes, holes


def _side_wall(x0, x1, y0, y1, height, notch) -> Mesh:
    """A side wall, optionally with a rounded finger notch dipping from its top edge.

    `notch` = (centre y, half width, bottom z): the top follows half an ellipse down to the
    bottom z, so fingers can reach in and pinch the tile's edge.
    """
    if not notch:
        return _box(x0, y0, x1, y1, 0, height)
    cell = 0.25
    nx, ny = max(2, int(round((x1 - x0) / cell)) + 1), int(round((y1 - y0) / cell)) + 1
    yy = y0 + np.arange(ny) * (y1 - y0) / (ny - 1)
    yc, half, bottom = notch
    dip = (height - bottom) * np.sqrt(np.clip(1 - ((yy - yc) / half) ** 2, 0, 1))
    z = np.repeat((height - dip)[:, None], nx, axis=1)
    m = heightmap_solid(z, (x1 - x0) / (nx - 1), (y1 - y0) / (ny - 1))
    m.vertices[:, 0] += x0
    m.vertices[:, 1] += y0
    return m


def build_frame(p: FrameParams) -> tuple[Mesh, dict]:
    """Return the frame mesh (several touching closed shells) and info for the UI."""
    printer = PRINTERS.get(p.printer, PRINTERS["a1mini"])
    layer = p.layer_mm
    mount = p.mount
    floor = snap(max(p.floor_mm, p.mount_floor_mm) if mount else p.floor_mm, layer) if (p.floor or mount) else 0.0
    text = [t.strip() for t in (p.title, p.stats) if t and t.strip()]
    text_h = snap(p.text_mm, layer)
    wall = p.wall_mm
    band = max(p.band_mm, wall) if text else wall

    in_w, in_d = p.tile_w + 2 * p.clearance_mm, p.tile_d + 2 * p.clearance_mm
    out_w, out_d = in_w + 2 * wall, in_d + wall + band
    # Walls cover everything: the tile (sitting on the floor) plus headroom above its peak.
    height = snap(floor + p.tile_h + p.headroom_mm, layer)
    x_in0, y_in0 = wall, band  # opening's south-west corner

    # Finger notches halfway along the side walls, down to just above the floor.
    notch = (y_in0 + in_d / 2, p.notch_mm / 2, floor + 2.0) if p.finger_notches else None
    shells = [
        _side_wall(0, wall, band, out_d, height, notch),  # west wall
        _side_wall(out_w - wall, out_w, band, out_d, height, notch),  # east wall
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
        sign = -1 if p.text_style == "engraved" else 1
        z[j0:j1, i0:i1] += sign * text_h * text_mask(text, i1 - i0, j1 - j0, family=p.font, upper=p.uppercase)
        shells.append(heightmap_solid(z, out_w / (nx - 1), band / (ny - 1)))
    else:
        shells.append(_box(0, 0, out_w, band, 0, height))
    centres, holes = [], []
    if floor:
        floor_shells, centres, holes = _build_floor(x_in0, y_in0, in_w, in_d, floor, p)
        shells += floor_shells
    mesh = Mesh.merge(shells)

    m = 2 * 2.0  # a frame's straight edges print fine close to the bed edge: 2 mm margin
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
        "text_style": p.text_style if text else "",
        "text_mm": text_h if text else 0,
        "fits": bool(fits and height <= printer.bed_z),
        "printer": printer.to_dict(),
        "warnings": warnings,
        "filament_g": _filament_grams(mesh.volume(), _surface_area(mesh)),
        "mount": bool(centres),
        "vents": bool(holes),
        "push_hole_mm": p.push_hole_mm if holes else 0,
        "finger_notches": bool(notch),
        "keyhole_spacing_mm": round(centres[1][0] - centres[0][0], 1) if centres else 0,
        "keyhole_from_top_mm": round(out_d - centres[0][1], 1) if centres else 0,
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
    if info.get("vents") or info.get("finger_notches"):
        how = []
        if info.get("vents"):
            how.append(f"the air holes in the floor let air in and out, so the tile slides without suction; "
                       f"to take it out, push through the {info['push_hole_mm']:.0f} mm hole in the back")
        if info.get("finger_notches"):
            how.append("or pinch its edges through the finger notches in the side walls")
        steps.append("Easy in and out: " + ", ".join(how) + ".")
    if info.get("mount"):
        steps.append(
            f"Wall mount: two keyholes in the back, {info['keyhole_spacing_mm']:.0f} mm apart and "
            f"{info['keyhole_from_top_mm']:.0f} mm below the top edge. Put two screws in the wall at that spacing, "
            "level, leaving the heads about 3 mm out (heads up to 8.5 mm wide, shanks up to 4 mm). Push the frame "
            "onto them and slide it down so it locks. Hang it with the text band at the bottom."
        )
    if info["has_text"] and info.get("text_style") == "engraved":
        steps.append(f"The title and stats are cut {info['text_mm']:.1f} mm into the front band. For coloured "
                     "letters, fill them with acrylic paint or a paint pen and wipe the surface clean.")
    elif info["has_text"]:
        steps.append("The title and stats are raised on the front band. For coloured text, add a filament "
                     f"change at the top of the frame (Z {info['outer_mm'][2]:.2f} mm) in Bambu Studio.")
    steps.append(f"Estimated filament: about {info['filament_g']} g PLA.")
    return steps
