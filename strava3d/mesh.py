"""Watertight mesh construction and STL export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Mesh:
    vertices: np.ndarray  # (N, 3) float, millimetres
    faces: np.ndarray  # (M, 3) int, counter-clockwise when seen from outside

    @staticmethod
    def merge(meshes: list["Mesh"]) -> "Mesh":
        verts, faces, offset = [], [], 0
        for m in meshes:
            verts.append(m.vertices)
            faces.append(m.faces + offset)
            offset += len(m.vertices)
        return Mesh(np.vstack(verts), np.vstack(faces))

    def volume(self) -> float:
        v = self.vertices[self.faces]
        return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0)

    def is_watertight(self) -> bool:
        """Every edge is shared by exactly two faces with opposite orientation."""
        f = self.faces
        edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        # A directed edge must appear once, and its reverse must also appear once.
        directed, counts = np.unique(edges, axis=0, return_counts=True)
        if (counts != 1).any():
            return False
        fwd = set(map(tuple, directed))
        return all((b, a) in fwd for a, b in directed)

    def write_stl(self, path: str | Path, name: str = "strava3d") -> None:
        """Write a binary STL file."""
        tri = self.vertices[self.faces].astype(np.float32)
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
        record = np.zeros(
            len(tri),
            dtype=[("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")],
        )
        record["normal"] = normals
        record["v"] = tri
        header = name.encode()[:80].ljust(80, b" ")
        with open(path, "wb") as fh:
            fh.write(header)
            fh.write(np.uint32(len(tri)).tobytes())
            fh.write(record.tobytes())


def close_surface(top: np.ndarray, top_faces: np.ndarray, perim: np.ndarray, z_bottom=0.0) -> Mesh:
    """Turn an upward-facing surface into a closed solid.

    `perim` lists the surface's boundary vertex indices counter-clockwise seen from above;
    the outline must be star-shaped around its centroid (true for all our shapes).
    """
    n_top, n_p = len(top), len(perim)
    bottom = top[perim].copy()
    bottom[:, 2] = z_bottom
    bot_idx = n_top + np.arange(n_p)
    center_idx = n_top + n_p
    center = np.array([[*bottom[:, :2].mean(axis=0), z_bottom]])

    k = np.arange(n_p)
    k1 = (k + 1) % n_p
    walls = np.concatenate(
        [
            np.column_stack([perim[k], bot_idx[k], bot_idx[k1]]),
            np.column_stack([perim[k], bot_idx[k1], perim[k1]]),
        ]
    )
    floor = np.column_stack([np.full(n_p, center_idx), bot_idx[k1], bot_idx[k]])
    return Mesh(
        np.vstack([top, bottom, center]),
        np.vstack([top_faces, walls, floor]).astype(np.int64),
    )


def heightmap_solid(z: np.ndarray, dx: float, dy: float, z_bottom: float = 0.0) -> Mesh:
    """Closed solid whose top surface is the heightmap `z` (ny x nx, row 0 = south/y=0)."""
    ny, nx = z.shape
    if nx < 2 or ny < 2:
        raise ValueError("heightmap must be at least 2x2")
    gx, gy = np.meshgrid(np.arange(nx) * dx, np.arange(ny) * dy)
    top = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()])

    def idx(j, i):
        return j * nx + i

    # Top surface: two triangles per cell, normals up.
    jj, ii = np.meshgrid(np.arange(ny - 1), np.arange(nx - 1), indexing="ij")
    a = idx(jj, ii).ravel()
    b = idx(jj, ii + 1).ravel()
    c = idx(jj + 1, ii + 1).ravel()
    d = idx(jj + 1, ii).ravel()
    top_faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])

    # Perimeter walked counter-clockwise (seen from above), corners not duplicated.
    perim = np.concatenate(
        [
            idx(0, np.arange(0, nx - 1)),  # south, west -> east
            idx(np.arange(0, ny - 1), nx - 1),  # east, south -> north
            idx(ny - 1, np.arange(nx - 1, 0, -1)),  # north, east -> west
            idx(np.arange(ny - 1, 0, -1), 0),  # west, north -> south
        ]
    )
    return close_surface(top, top_faces, perim, z_bottom)


def radial_outline(shape: str, radius: float, n: int):
    """Outline angles and radii (circumradius `radius`) for 'circle' or 'hexagon'.

    Hexagons have flat top/bottom edges; `n` is rounded up to a multiple of 6 so the
    corners are exact vertices.
    """
    if shape == "circle":
        theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return theta, np.full(n, radius)
    if shape == "hexagon":
        n = int(np.ceil(n / 6) * 6)
        theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
        inradius = radius * np.cos(np.pi / 6)
        phi = np.mod(theta - np.pi / 6, np.pi / 3)
        phi = np.where(phi > np.pi / 6, phi - np.pi / 3, phi)
        return theta, inradius / np.cos(phi)
    raise ValueError(f"unknown radial shape {shape!r}")


def radial_solid(shape: str, radius: float, cell: float, height_fn, z_bottom=0.0) -> Mesh:
    """Closed circle/hexagon solid centred at (radius, radius); top z = height_fn(x, y).

    Vertices sit on concentric rings; inner rings use fewer vertices (halving the count
    each time the radius halves) so the mesh density stays roughly uniform.
    """
    rings = max(2, int(np.ceil(radius / cell)))
    levels = min(4, int(np.log2(rings)))
    unit = 6 * 2**levels
    n_outer = max(unit, int(np.ceil(2 * np.pi * radius / cell / unit)) * unit)
    counts = [n_outer >> min(levels, int(np.log2(rings / k))) for k in range(1, rings + 1)]

    xs, ys, starts = [np.array([radius])], [np.array([radius])], []
    offset = 1
    for k, n in enumerate(counts, start=1):
        theta, r_out = radial_outline(shape, radius * k / rings, n)
        xs.append(radius + r_out * np.cos(theta))
        ys.append(radius + r_out * np.sin(theta))
        starts.append(offset)
        offset += n
    x, y = np.concatenate(xs), np.concatenate(ys)
    top = np.column_stack([x, y, height_fn(x, y)])

    n0 = counts[0]
    j = np.arange(n0)
    faces = [np.column_stack([np.zeros(n0, int), starts[0] + j, starts[0] + (j + 1) % n0])]
    for k in range(rings - 1):
        n_in, n_out = counts[k], counts[k + 1]
        si, so = starts[k], starts[k + 1]
        j = np.arange(n_in)
        a, d = si + j, si + (j + 1) % n_in
        if n_out == n_in:
            b, c = so + j, so + (j + 1) % n_out
            faces += [np.column_stack([a, b, c]), np.column_stack([a, c, d])]
        else:  # n_out == 2 * n_in
            o0, o1, o2 = so + 2 * j, so + 2 * j + 1, so + (2 * j + 2) % n_out
            faces += [np.column_stack([a, o0, o1]), np.column_stack([a, o1, o2]), np.column_stack([a, o2, d])]
    perim = starts[-1] + np.arange(counts[-1])
    return close_surface(top, np.vstack(faces), perim, z_bottom)


def masked_heightmap_solid(z: np.ndarray, f: np.ndarray, dx: float, dy: float, z_bottom: float = 0.0) -> Mesh:
    """Closed solid over the region where f > 0, with top surface z (both ny x nx, row 0 = south).

    The region's edge is traced with marching squares: wherever f changes sign along a cell
    side, the boundary point is interpolated linearly, so curved outlines (e.g. a slot along
    a route) stay smooth and accurate at sub-cell precision. Vertical walls run down to
    z_bottom along every boundary, including the grid's outer edge.
    """
    ny, nx = z.shape
    f = np.where(f == 0, -1e-9, f)
    inside = f > 0
    xs, ys = np.arange(nx) * dx, np.arange(ny) * dy
    gx, gy = np.meshgrid(xs, ys)
    verts = [np.column_stack([gx.ravel(), gy.ravel(), z.ravel()])]
    extra: list[tuple[float, float, float]] = []
    edge_idx: dict = {}
    n_corner = nx * ny

    def corner(j, i):
        return j * nx + i

    def crossing(key, ja, ia, jb, ib):
        if key not in edge_idx:
            fa, fb = f[ja, ia], f[jb, ib]
            t = fa / (fa - fb)
            extra.append((xs[ia] + t * (xs[ib] - xs[ia]), ys[ja] + t * (ys[jb] - ys[ja]),
                          z[ja, ia] + t * (z[jb, ib] - z[ja, ia])))
            edge_idx[key] = n_corner + len(extra) - 1
        return edge_idx[key]

    c00, c10 = inside[:-1, :-1], inside[:-1, 1:]
    c11, c01 = inside[1:, 1:], inside[1:, :-1]
    n_in = c00.astype(int) + c10 + c11 + c01
    faces = []
    # Cells entirely inside: two triangles each (vectorised).
    jj, ii = np.nonzero(n_in == 4)
    a, b = corner(jj, ii), corner(jj, ii + 1)
    c, d = corner(jj + 1, ii + 1), corner(jj + 1, ii)
    faces += [np.column_stack([a, b, c]), np.column_stack([a, c, d])]
    # Boundary cells: walk the cell's sides counter-clockwise, collecting inside corners and
    # sign crossings into one convex polygon, then fan-triangulate it.
    tri = []
    for j, i in zip(*np.nonzero((n_in > 0) & (n_in < 4))):
        cs = [(j, i), (j, i + 1), (j + 1, i + 1), (j + 1, i)]
        keys = [("h", j, i), ("v", j, i + 1), ("h", j + 1, i), ("v", j, i)]
        poly = []
        for k in range(4):
            (ja, ia), (jb, ib) = cs[k], cs[(k + 1) % 4]
            if inside[ja, ia]:
                poly.append(corner(ja, ia))
            if inside[ja, ia] != inside[jb, ib]:
                poly.append(crossing(keys[k], ja, ia, jb, ib))
        for k in range(1, len(poly) - 1):
            tri.append((poly[0], poly[k], poly[k + 1]))
    if tri:
        faces.append(np.array(tri, dtype=np.int64))
    top_faces = np.vstack(faces).astype(np.int64)
    if extra:
        verts.append(np.array(extra))
    top = np.vstack(verts)
    n = len(top)
    bottom = top.copy()
    bottom[:, 2] = z_bottom

    # Boundary = directed top edges whose reverse is not also a top edge; each gets a wall.
    e = np.concatenate([top_faces[:, [0, 1]], top_faces[:, [1, 2]], top_faces[:, [2, 0]]])
    code = e[:, 0] * n + e[:, 1]
    rev = e[:, 1] * n + e[:, 0]
    edge = e[~np.isin(rev, code)]
    walls = np.concatenate([
        np.column_stack([edge[:, 0], edge[:, 0] + n, edge[:, 1] + n]),
        np.column_stack([edge[:, 0], edge[:, 1] + n, edge[:, 1]]),
    ])
    bottom_faces = top_faces[:, ::-1] + n
    return Mesh(np.vstack([top, bottom]), np.vstack([top_faces, bottom_faces, walls]))


def hexahedron(bottom: np.ndarray, top: np.ndarray) -> Mesh:
    """Closed 8-vertex solid; `bottom`/`top` are 4x3 corner arrays, CCW seen from above."""
    verts = np.vstack([bottom, top])
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)]
    for k in range(4):
        k1 = (k + 1) % 4
        faces += [(k, k1, k1 + 4), (k, k1 + 4, k + 4)]
    return Mesh(verts, np.array(faces, dtype=np.int64))
