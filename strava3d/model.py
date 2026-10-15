"""Turn a GPS track + elevation data into printable terrain (and route) meshes."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .elevation import TileFetcher, elevation_grid
from .geo import BBox, project
from .mesh import Mesh, heightmap_solid, hexahedron

ROUTE_STYLES = ("raised", "groove", "none")


@dataclass
class ModelParams:
    size_mm: float = 150.0  # length of the longest side of the print
    cell_mm: float = 0.5  # horizontal resolution of the terrain mesh
    z_exaggeration: float = 1.5  # vertical exaggeration relative to true scale
    base_mm: float = 3.0  # thickness of the solid base under the lowest point
    padding: float = 0.1  # extra terrain around the route, as fraction of its size
    square: bool = False  # make the tile square instead of fitting the route's shape
    route_style: str = "raised"  # raised | groove | none
    route_width_mm: float = 1.6
    route_height_mm: float = 1.0  # height (raised) or depth (groove)
    separate_route: bool = False  # also output the route as its own body (multi-colour)


@dataclass
class TerrainModel:
    terrain: Mesh
    route: Mesh | None
    heightmap: np.ndarray  # final top surface in mm (ny x nx, row 0 = south)
    route_mask: np.ndarray  # 0..1 coverage of the route on the grid
    route_xy: np.ndarray  # route polyline in mm
    dx: float
    dy: float
    info: dict = field(default_factory=dict)


def build_model(
    points: list[tuple[float, float]],
    params: ModelParams | None = None,
    fetcher: TileFetcher | None = None,
    elevation=None,
) -> TerrainModel:
    """Build the model. `elevation(bbox, nx, ny)` can be injected (defaults to terrain tiles)."""
    p = params or ModelParams()
    if p.route_style not in ROUTE_STYLES:
        raise ValueError(f"route_style must be one of {ROUTE_STYLES}")
    if p.route_style == "groove" and p.route_height_mm >= p.base_mm:
        raise ValueError("groove depth must be smaller than the base thickness")
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        raise ValueError("need at least two GPS points")

    bbox = BBox.around(pts, padding=p.padding, square=p.square)
    width_m, height_m = bbox.size_m()
    scale = p.size_mm / max(width_m, height_m)  # mm per metre
    width_mm, height_mm = width_m * scale, height_m * scale
    nx = max(2, int(round(width_mm / p.cell_mm)) + 1)
    ny = max(2, int(round(height_mm / p.cell_mm)) + 1)
    dx, dy = width_mm / (nx - 1), height_mm / (ny - 1)

    if elevation is None:
        elev = elevation_grid(bbox, nx, ny, fetcher=fetcher)
    else:
        elev = np.asarray(elevation(bbox, nx, ny), dtype=float)
    z_base = elev.min()
    z = p.base_mm + (elev - z_base) * scale * p.z_exaggeration

    x_m, y_m = project(pts[:, 0], pts[:, 1], bbox)
    route_xy = np.column_stack([x_m * scale, y_m * scale])
    mask = route_mask(route_xy, nx, ny, dx, dy, p.route_width_mm)

    terrain_z = z.copy()
    route_mesh = None
    if p.separate_route and p.route_style != "none":
        # Recess the terrain slightly so the separate route body seats into it.
        seat = min(0.4, p.base_mm / 2)
        terrain_z -= seat * mask
        route_mesh = route_ribbon(route_xy, z, dx, dy, p.route_width_mm, p.route_height_mm, seat)
    elif p.route_style == "raised":
        terrain_z += p.route_height_mm * mask
    elif p.route_style == "groove":
        terrain_z -= p.route_height_mm * mask

    terrain = heightmap_solid(terrain_z, dx, dy)
    info = {
        "bbox": bbox,
        "grid": (nx, ny),
        "size_mm": (round(width_mm, 1), round(height_mm, 1), round(float(terrain_z.max()), 1)),
        "elevation_range_m": (round(float(elev.min()), 1), round(float(elev.max()), 1)),
        "scale": f"1:{round(1000 / scale):,}",
    }
    return TerrainModel(terrain, route_mesh, terrain_z, mask, route_xy, dx, dy, info)


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


def render_preview(model: TerrainModel, path, max_px: int = 1000) -> None:
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
    orange = np.array([0.99, 0.32, 0.0])
    m = model.route_mask[..., None]
    rgb = rgb * (1 - m) + orange * m
    img = Image.fromarray((np.clip(rgb, 0, 1)[::-1] * 255).astype(np.uint8))
    factor = max_px / max(img.size)
    img = img.resize((round(img.width * factor), round(img.height * factor)), Image.LANCZOS)
    img.save(path)
