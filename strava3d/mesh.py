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


def heightmap_solid(z: np.ndarray, dx: float, dy: float, z_bottom: float = 0.0) -> Mesh:
    """Closed solid whose top surface is the heightmap `z` (ny x nx, row 0 = south/y=0)."""
    ny, nx = z.shape
    if nx < 2 or ny < 2:
        raise ValueError("heightmap must be at least 2x2")
    xs = np.arange(nx) * dx
    ys = np.arange(ny) * dy
    gx, gy = np.meshgrid(xs, ys)
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
    n_top = len(top)
    n_p = len(perim)
    bottom = top[perim].copy()
    bottom[:, 2] = z_bottom
    bot_idx = n_top + np.arange(n_p)
    center_idx = n_top + n_p
    center = np.array([[xs[-1] / 2, ys[-1] / 2, z_bottom]])

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


def hexahedron(bottom: np.ndarray, top: np.ndarray) -> Mesh:
    """Closed 8-vertex solid; `bottom`/`top` are 4x3 corner arrays, CCW seen from above."""
    verts = np.vstack([bottom, top])
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)]
    for k in range(4):
        k1 = (k + 1) % 4
        faces += [(k, k1, k1 + 4), (k, k1 + 4, k + 4)]
    return Mesh(verts, np.array(faces, dtype=np.int64))
