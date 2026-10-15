"""Turn a GPS track + elevation data into printable terrain (and route) meshes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .elevation import TileFetcher, elevation_grid
from .geo import BBox, project
from .label import text_mask
from .mesh import Mesh, heightmap_solid, hexahedron, radial_solid
from .printers import DEFAULT_PRINTER, PRINTERS, layer_number, snap
from .threemf import Part, write_3mf

ROUTE_STYLES = ("raised", "groove", "none")
SHAPES = ("rectangle", "square", "circle", "hexagon")
# Colour-band split points as fractions of the terrain relief, by number of colours.
BAND_FRACTIONS = {2: [0.55], 3: [0.35, 0.7], 4: [0.25, 0.5, 0.75]}


@dataclass
class ModelParams:
    size_mm: float | None = None  # longest side of the print; None = fit the printer
    cell_mm: float = 0.5  # horizontal resolution of the terrain mesh
    z_exaggeration: float | None = None  # vertical exaggeration; None = auto (see target_relief)
    target_relief_frac: float = 0.12  # auto exaggeration aims for relief = this x model size
    max_auto_exaggeration: float = 12.0
    water: str = "auto"  # auto | on | off: flatten the sea (elevation <= 0) and lift the land
    # Coastline threshold. Elevation tiles blend coarse bathymetry into a blocky 0-1 m fringe
    # around coasts; tracing the shore at 1 m follows the detailed land data instead.
    sea_level_m: float = 1.0
    coast_step_mm: float = 1.0  # height of the land's edge above the flat sea
    sea_color: bool | None = None  # sea in its own colour (filament change at the sea surface); None = auto
    base_mm: float = 3.0  # thickness of the solid base under the lowest point
    padding: float = 0.1  # extra terrain around the route, as fraction of its size
    shape: str = "rectangle"  # rectangle | square | circle | hexagon
    square: bool = False  # legacy alias for shape="square"
    route_style: str = "raised"  # raised | groove | none
    route_width_mm: float = 1.6
    route_height_mm: float = 1.0  # height (raised) or depth (groove)
    separate_route: bool = False  # also output the route as its own body (multi-colour)
    printer: str = DEFAULT_PRINTER
    layer_mm: float | None = None  # layer height; None = printer default
    label: bool = False  # name plate along the bottom edge (rectangle/square only)
    label_lines: list[str] = field(default_factory=list)  # [title, subtitle]
    label_mm: float = 16.0  # depth of the name plate strip
    label_height_mm: float = 0.8  # how far the text is raised
    colors: int = 1  # colour bands by height (1 = single colour)
    base_color: bool = False  # give the base / plinth its own colour (adds a band)


@dataclass
class TerrainModel:
    terrain: Mesh
    route: Mesh | None
    heightmap: np.ndarray  # final top surface in mm (ny x nx, row 0 = south)
    route_mask: np.ndarray  # 0..1 coverage of the route on the grid
    route_xy: np.ndarray  # route polyline in mm
    dx: float
    dy: float
    shape_mask: np.ndarray | None = None  # 1 inside the printed outline (radial shapes)
    info: dict = field(default_factory=dict)
    sea_mask: np.ndarray | None = None


def _hex_norm(x, y):
    """Distance to a flat-top hexagon's edges, in units of its inradius."""
    c, s_ = np.cos(np.pi / 6), np.sin(np.pi / 6)
    return np.maximum.reduce([np.abs(x * c + y * s_), np.abs(y), np.abs(-x * c + y * s_)])


def build_model(
    points: list[tuple[float, float]],
    params: ModelParams | None = None,
    fetcher: TileFetcher | None = None,
    elevation=None,
) -> TerrainModel:
    """Build the model. `elevation(bbox, nx, ny)` can be injected (defaults to terrain tiles)."""
    p = params or ModelParams()
    shape = "square" if p.square and p.shape == "rectangle" else p.shape
    if p.route_style not in ROUTE_STYLES:
        raise ValueError(f"route_style must be one of {ROUTE_STYLES}")
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {SHAPES}")
    if p.printer not in PRINTERS:
        raise ValueError(f"unknown printer {p.printer!r}")
    printer = PRINTERS[p.printer]
    layer = p.layer_mm or printer.layer
    warnings: list[str] = []
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        raise ValueError("need at least two GPS points")

    # Snap vertical dimensions to whole layers so the slicer reproduces them exactly.
    base_mm = snap(p.base_mm, layer)
    route_h = snap(p.route_height_mm, layer)
    text_h = snap(p.label_height_mm, layer)
    route_w = p.route_width_mm
    if route_w < 3 * printer.nozzle and p.route_style != "none":
        route_w = round(3 * printer.nozzle, 2)
        warnings.append(
            f"Route width raised to {route_w} mm (3 nozzle widths) so it prints cleanly "
            f"with a {printer.nozzle} mm nozzle."
        )
    if p.route_style == "groove" and route_h >= base_mm:
        raise ValueError("groove depth must be smaller than the base thickness")

    size = p.size_mm or min(printer.max_size, 200.0)
    radial = shape in ("circle", "hexagon")
    label = p.label and not radial and any(p.label_lines)
    if p.label and radial:
        warnings.append("The name plate is only available for rectangle and square shapes.")
    label_mm = p.label_mm if label else 0.0

    # --- Area to model ---------------------------------------------------------------
    if radial:
        tmp = BBox.around(pts, padding=0)
        lat0, lon0 = tmp.center
        x_m, y_m = project(pts[:, 0], pts[:, 1], BBox.centered(lat0, lon0, 0, 0))
        if shape == "circle":
            reach = float(np.hypot(x_m, y_m).max())
            radius_m = reach * (1 + p.padding) + 200
        else:
            reach = float(_hex_norm(x_m, y_m).max())
            radius_m = (reach * (1 + p.padding) + 200) / np.cos(np.pi / 6)
        bbox = BBox.centered(lat0, lon0, radius_m, radius_m)
    else:
        bbox = BBox.around(pts, padding=p.padding, square=shape == "square")
    width_m, height_m = bbox.size_m()

    # Scale so the whole footprint (terrain + name plate) has `size` as its longest side.
    scale = min(size / width_m, (size - label_mm) / height_m)  # mm per metre
    width_mm, height_mm = width_m * scale, height_m * scale
    nx = max(2, int(round(width_mm / p.cell_mm)) + 1)
    ny = max(2, int(round(height_mm / p.cell_mm)) + 1)
    dx, dy = width_mm / (nx - 1), height_mm / (ny - 1)

    if elevation is None:
        elev = elevation_grid(bbox, nx, ny, fetcher=fetcher)
    else:
        elev = np.asarray(elevation(bbox, nx, ny), dtype=float)
    x_m, y_m = project(pts[:, 0], pts[:, 1], bbox)
    route_xy = np.column_stack([x_m * scale, y_m * scale])

    shape_mask = None
    if radial:
        # Only terrain inside the outline counts for the height range.
        gx, gy = np.meshgrid(np.arange(nx) * dx - width_mm / 2, np.arange(ny) * dy - height_mm / 2)
        r_mm = width_mm / 2
        if shape == "circle":
            shape_mask = np.hypot(gx, gy) <= r_mm + dx
        else:
            shape_mask = _hex_norm(gx, gy) <= r_mm * np.cos(np.pi / 6) + dx
    inside = elev[shape_mask] if shape_mask is not None else elev
    if p.water not in ("auto", "on", "off"):
        raise ValueError("water must be auto, on or off")
    sea = elev <= p.sea_level_m
    sea_fraction = float((inside <= p.sea_level_m).mean())
    water = p.water == "on" or (p.water == "auto" and sea_fraction >= 0.02)
    if water and sea_fraction < 1.0:
        # Sea becomes a flat surface at the base; land starts a few layers higher so a
        # single filament change at the base top separates sea and land colours.
        ref = p.sea_level_m
        lift = snap(p.coast_step_mm, layer)
        land = inside[inside > ref]
    else:
        water, ref, lift, land = False, float(inside.min()), 0.0, inside
    relief_m = float(land.max() - ref) if land.size else 0.0
    if p.z_exaggeration:
        exaggeration = p.z_exaggeration
    else:
        target = p.target_relief_frac * size
        exaggeration = float(np.clip(target / max(relief_m * scale, 1e-6), 1.0, p.max_auto_exaggeration))
        exaggeration = round(exaggeration, 1)
    if water:
        # A crisp step at the shoreline: land coverage per vertex, anti-aliased from a 2x
        # finer grid so the edge follows the true contour and is at most one cell wide.
        fine = 4
        nxf, nyf = (nx - 1) * fine + 1, (ny - 1) * fine + 1
        if elevation is None:
            elev_f = elevation_grid(bbox, nxf, nyf, fetcher=fetcher)
        else:
            elev_f = np.asarray(elevation(bbox, nxf, nyf), dtype=float)
        coast = land_coverage(elev_f, ref, fine)
        z = base_mm + lift * coast + np.maximum(elev - ref, 0) * scale * exaggeration
    else:
        z = base_mm + (elev - ref) * scale * exaggeration
        z = np.maximum(z, base_mm)

    mask = route_mask(route_xy, nx, ny, dx, dy, route_w)
    terrain_z = z.copy()
    route_mesh = None
    if p.separate_route and p.route_style != "none":
        # Recess the terrain slightly so the separate route body seats into it.
        seat = min(snap(0.4, layer), base_mm / 2)
        terrain_z -= seat * mask
        route_mesh = route_ribbon(route_xy, z, dx, dy, route_w, route_h, seat)
    elif p.route_style == "raised":
        terrain_z += route_h * mask
    elif p.route_style == "groove":
        terrain_z -= route_h * mask

    # --- Name plate --------------------------------------------------------------------
    if label:
        rows = max(2, int(round(label_mm / dy)))
        text = text_mask(p.label_lines, nx, rows)
        strip = base_mm + text_h * text
        terrain_z = np.vstack([strip, terrain_z])
        mask = np.vstack([np.zeros((rows, nx)), mask])
        sea = np.vstack([np.zeros((rows, nx), bool), sea])
        shift = rows * dy
        route_xy = route_xy + [0, shift]
        if route_mesh is not None:
            route_mesh.vertices[:, 1] += shift
        height_mm += shift

    # --- Mesh --------------------------------------------------------------------------
    if radial:
        terrain = radial_solid(
            shape, width_mm / 2, p.cell_mm, lambda x, y: sample_grid(terrain_z, x, y, dx, dy)
        )
        footprint = (width_mm, width_mm if shape == "circle" else width_mm * np.cos(np.pi / 6))
    else:
        terrain = heightmap_solid(terrain_z, dx, dy)
        footprint = (width_mm, height_mm)

    top_z = float(terrain_z[shape_mask].max() if shape_mask is not None else terrain_z.max())
    if route_mesh is not None:
        top_z = max(top_z, float(route_mesh.vertices[:, 2].max()))

    # --- Printer fit -------------------------------------------------------------------
    fits = _fits(footprint, printer)
    if not fits:
        warnings.append(
            f"{footprint[0]:.0f} x {footprint[1]:.0f} mm does not fit the {printer.name} "
            f"bed ({printer.bed_x:.0f} x {printer.bed_y:.0f} mm). Reduce the size to "
            f"{printer.max_size:.0f} mm or less."
        )
    if top_z > printer.bed_z:
        warnings.append(f"Model is {top_z:.0f} mm tall; the {printer.name} prints up to {printer.bed_z:.0f} mm.")
    if shape == "rectangle" and min(footprint) / max(footprint) < 0.6:
        warnings.append(
            "Long, narrow route: the Square shape shows more of the surrounding area at the same "
            "scale, uses the bed better and gives the name plate more room."
        )
    relief = top_z - base_mm
    if relief < 3:
        warnings.append("Very little relief. Try a higher vertical exaggeration, or Auto.")

    # --- Colour bands (filament changes by height) -----------------------------------------
    terrain_top = float(z[shape_mask].max() if shape_mask is not None else z.max())
    sea_color = bool(water and p.sea_color is not False)
    bands = colour_bands(p.colors, p.base_color or sea_color, base_mm, terrain_top, layer, printer, base_mm + lift)

    area = _surface_area(terrain) + (_surface_area(route_mesh) if route_mesh is not None else 0)
    volume = terrain.volume() + (route_mesh.volume() if route_mesh is not None else 0)
    info = {
        "bbox": bbox,
        "grid": (nx, ny),
        "shape": shape,
        "size_mm": (round(float(footprint[0]), 1), round(float(footprint[1]), 1), round(top_z, 1)),
        "elevation_range_m": (round(max(float(inside.min()), ref) if water else float(inside.min()), 1),
                              round(float(inside.max()), 1)),
        "scale": f"1:{round(1000 / scale):,}",
        "printer": printer.to_dict(),
        "layer_mm": layer,
        "fits": bool(fits and top_z <= printer.bed_z),
        "base_mm": base_mm,
        "route_height_mm": route_h,
        "route_width_mm": route_w,
        "label_mm": round(shift, 2) if label else 0,
        "bands": bands,
        "exaggeration": float(exaggeration),
        "water": bool(water),
        "sea_color": sea_color,
        "coast_step_mm": lift,
        "sea_fraction": round(sea_fraction, 3),
        "filament_g": _filament_grams(volume, area),
        "warnings": warnings,
    }
    return TerrainModel(terrain, route_mesh, terrain_z, mask, route_xy, dx, dy, shape_mask, info,
                        sea if water else None)


def _fits(footprint, printer) -> bool:
    w, h = footprint
    m = 2 * printer.margin
    return bool((w <= printer.bed_x - m and h <= printer.bed_y - m) or (
        w <= printer.bed_y - m and h <= printer.bed_x - m
    ))


def _surface_area(mesh: Mesh) -> float:
    v = mesh.vertices[mesh.faces]
    return float(np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1).sum() / 2)


def _filament_grams(volume_mm3: float, area_mm2: float, infill=0.15, shell_mm=0.9, density=1.24) -> int:
    """Rough PLA estimate: solid shell (walls, top, bottom) plus sparse infill."""
    shell = min(area_mm2 * shell_mm, volume_mm3)
    return int(round((shell + (volume_mm3 - shell) * infill) * density / 1000))


def land_coverage(elev_fine: np.ndarray, sea_level: float, fine: int) -> np.ndarray:
    """Fraction (0..1) of land around each grid vertex.

    `elev_fine` is sampled on a grid `fine` times denser than the mesh (vertex i is fine
    index i * fine). Averaging the land/sea test over each vertex's neighbourhood gives an
    anti-aliased shoreline that follows the detailed land data rather than the blocky
    bathymetry pixels.
    """
    land = (elev_fine > sea_level).astype(float)
    # Tent filter spanning one mesh cell on each side of the vertex.
    w = 1 - np.abs(np.arange(-fine + 1, fine)) / fine
    w /= w.sum()
    r = len(w) // 2
    padded = np.pad(land, r, mode="edge")
    rows = sum(w[k] * padded[k:k + land.shape[0], :] for k in range(len(w)))
    cov = sum(w[k] * rows[:, k:k + land.shape[1]] for k in range(len(w)))
    return cov[::fine, ::fine]


def colour_bands(colors: int, base_color: bool, base_mm: float, top_mm: float, layer: float, printer,
                 land_mm: float | None = None) -> list[dict]:
    """Filament-change points: the first layer printed in each new colour.

    `base_color` adds a change at the top of the base (also the sea surface); the
    height bands split the land between `land_mm` (default: base) and `top_mm`.
    """
    fractions = BAND_FRACTIONS.get(colors, [])
    lo = base_mm if land_mm is None else land_mm
    cuts = [lo + f * (top_mm - lo) for f in fractions]
    if base_color:
        cuts = [base_mm] + cuts
    out = []
    for z in cuts:
        # Snap to a layer boundary; the change happens before the next layer starts.
        z = printer.first_layer + max(0, round((z - printer.first_layer) / layer)) * layer
        if out and z <= out[-1]["z"]:
            continue
        out.append({"z": round(z, 3), "layer": layer_number(z + layer, printer, layer), "top_z": round(z + layer, 3)})
    return out


def route_mask(route_xy: np.ndarray, nx: int, ny: int, dx: float, dy: float, width_mm: float):
    """Anti-aliased coverage (0..1) of the route line on the terrain grid."""
    ss = 4  # supersampling factor
    img = Image.new("L", (nx * ss, ny * ss), 0)
    px = route_xy[:, 0] / dx * ss + ss / 2
    py = route_xy[:, 1] / dy * ss + ss / 2
    line_w = max(1, int(round(width_mm / min(dx, dy) * ss)))
    draw = ImageDraw.Draw(img)
    draw.line(list(zip(px.tolist(), py.tolist())), fill=255, width=line_w, joint="curve")
    # Round caps at the ends.
    r = line_w / 2
    for x, y in ((px[0], py[0]), (px[-1], py[-1])):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=255)
    arr = np.asarray(img, dtype=float) / 255.0
    return arr.reshape(ny, ss, nx, ss).mean(axis=(1, 3))


def resample_polyline(xy: np.ndarray, step: float) -> np.ndarray:
    seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    dist = np.concatenate([[0], np.cumsum(seg)])
    if dist[-1] == 0:
        return xy[:1]
    t = np.linspace(0, dist[-1], max(2, int(np.ceil(dist[-1] / step)) + 1))
    return np.column_stack([np.interp(t, dist, xy[:, 0]), np.interp(t, dist, xy[:, 1])])


def sample_grid(z: np.ndarray, x: np.ndarray, y: np.ndarray, dx: float, dy: float):
    ny, nx = z.shape
    fx = np.clip(x / dx, 0, nx - 1)
    fy = np.clip(y / dy, 0, ny - 1)
    x0 = np.minimum(np.floor(fx).astype(int), nx - 2)
    y0 = np.minimum(np.floor(fy).astype(int), ny - 2)
    ax, ay = fx - x0, fy - y0
    return (
        z[y0, x0] * (1 - ax) * (1 - ay)
        + z[y0, x0 + 1] * ax * (1 - ay)
        + z[y0 + 1, x0] * (1 - ax) * ay
        + z[y0 + 1, x0 + 1] * ax * ay
    )


def route_ribbon(
    route_xy: np.ndarray,
    z: np.ndarray,
    dx: float,
    dy: float,
    width_mm: float,
    height_mm: float,
    seat_mm: float,
) -> Mesh:
    """Route as a chain of overlapping blocks that hug the terrain surface."""
    pts = resample_polyline(route_xy, step=max(width_mm, min(dx, dy)))
    half = width_mm / 2
    blocks = []
    for p0, p1 in zip(pts[:-1], pts[1:]):
        d = p1 - p0
        length = np.linalg.norm(d)
        if length < 1e-6:
            continue
        d /= length
        # Extend each block by half a width so consecutive blocks overlap at turns.
        a, b = p0 - d * half, p1 + d * half
        left = np.array([-d[1], d[0]]) * half
        corners = np.array([a - left, b - left, b + left, a + left])
        ground = sample_grid(z, corners[:, 0], corners[:, 1], dx, dy)
        bottom = np.column_stack([corners, np.full(4, ground.min() - seat_mm)])
        top = np.column_stack([corners, ground + height_mm])
        blocks.append(hexahedron(bottom, top))
    return Mesh.merge(blocks)


def render_preview(model: TerrainModel, path, max_px: int = 1000, band_colors=None) -> None:
    """Hillshade PNG of the top surface with the route drawn in Strava orange."""
    z = model.heightmap
    gy, gx = np.gradient(z, model.dy, model.dx)
    # Light from the north-west, 45 degrees up.
    light = np.array([-1.0, 1.0, 1.4])
    light /= np.linalg.norm(light)
    normal = np.dstack([-gx, -gy, np.ones_like(z)])
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    shade = np.clip(normal @ light, 0, 1)
    rel = (z - z.min()) / max(np.ptp(z), 1e-9)
    base = np.dstack([0.55 + 0.35 * rel, 0.62 + 0.25 * rel, 0.50 + 0.30 * rel])
    rgb = base * (0.35 + 0.65 * shade[..., None])
    if model.sea_mask is not None:
        sea = model.sea_mask[..., None]
        rgb = rgb * (1 - sea) + np.array([0.36, 0.58, 0.80]) * (0.85 + 0.15 * shade[..., None]) * sea
    orange = np.array([0.99, 0.32, 0.0])
    m = model.route_mask[..., None]
    rgb = rgb * (1 - m) + orange * m
    rgb = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    if model.shape_mask is not None:
        alpha = (model.shape_mask * 255).astype(np.uint8)
        img = Image.fromarray(np.dstack([rgb, alpha])[::-1], "RGBA")
    else:
        img = Image.fromarray(rgb[::-1])
    factor = max_px / max(img.size)
    img = img.resize((round(img.width * factor), round(img.height * factor)), Image.LANCZOS)
    img.save(path)


def build_and_export(points, name: str, params: ModelParams, out_dir: Path, fetcher=None) -> dict:
    """Build a model and write STL(s), a Bambu Studio-ready 3MF and a preview PNG."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model = build_model(points, params, fetcher=fetcher)
    printer = PRINTERS[params.printer]

    files = {"terrain": out_dir / f"{name}-terrain.stl"}
    model.terrain.write_stl(files["terrain"], name=name)
    parts = [Part("terrain", model.terrain, 1)]
    if model.route is not None:
        files["route"] = out_dir / f"{name}-route.stl"
        model.route.write_stl(files["route"], name=f"{name}-route")
        parts.append(Part("route", model.route, 2))
    files["3mf"] = out_dir / f"{name}.3mf"
    write_3mf(files["3mf"], parts, name=name, bed_center=(printer.bed_x / 2, printer.bed_y / 2))
    files["preview"] = out_dir / f"{name}-preview.png"
    render_preview(model, files["preview"])

    info = {k: v for k, v in model.info.items() if k != "bbox"}
    info["triangles"] = len(model.terrain.faces)
    # Route outline (mm, decimated) so viewers can colour the route on the terrain.
    route = model.route_xy[:: max(1, len(model.route_xy) // 3000)]
    info["route_mm"] = np.round(route, 2).tolist()
    info["route_style"] = "separate" if model.route is not None else params.route_style
    return {"files": files, "info": info}
