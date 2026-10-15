"""Geographic helpers: polyline decoding, GPX parsing, bounding boxes, projection."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EARTH_RADIUS_M = 6_378_137.0


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode a Google encoded polyline (as used by Strava's map.summary_polyline)."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    factor = 10**precision
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lat / factor, lon / factor))
    return coords


def read_gpx(path: str | Path) -> list[tuple[float, float]]:
    """Read all track (or route) points from a GPX file as [(lat, lon), ...]."""
    root = ET.parse(path).getroot()
    points = [
        (float(el.get("lat")), float(el.get("lon")))
        for el in root.iter()
        if el.tag.split("}")[-1] in ("trkpt", "rtept")
    ]
    if not points:
        raise ValueError(f"No track points found in {path}")
    return points


@dataclass
class BBox:
    south: float
    west: float
    north: float
    east: float

    @classmethod
    def around(cls, points, padding: float = 0.1, square: bool = False) -> "BBox":
        """Bounding box of points, padded by a fraction of its size on every side."""
        pts = np.asarray(points, dtype=float)
        s, w = pts.min(axis=0)
        n, e = pts.max(axis=0)
        lat0 = (s + n) / 2
        # Work in metres so padding / squaring is isotropic.
        height_m = (n - s) * math.pi / 180 * EARTH_RADIUS_M
        width_m = (e - w) * math.pi / 180 * EARTH_RADIUS_M * math.cos(math.radians(lat0))
        if square:
            width_m = height_m = max(width_m, height_m)
        # Always pad by at least 200 m so tiny activities still show terrain.
        pad = max(padding * max(width_m, height_m), 200.0)
        half_h = height_m / 2 + pad
        half_w = width_m / 2 + pad
        lon0 = (w + e) / 2
        dlat = math.degrees(half_h / EARTH_RADIUS_M)
        dlon = math.degrees(half_w / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
        return cls(lat0 - dlat, lon0 - dlon, lat0 + dlat, lon0 + dlon)

    @property
    def center(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2

    def size_m(self) -> tuple[float, float]:
        """(width, height) in metres."""
        lat0 = self.center[0]
        h = (self.north - self.south) * math.pi / 180 * EARTH_RADIUS_M
        w = (self.east - self.west) * math.pi / 180 * EARTH_RADIUS_M * math.cos(math.radians(lat0))
        return w, h


def project(lat, lon, bbox: BBox):
    """Local equirectangular projection to metres, origin at the bbox's south-west corner."""
    lat0 = math.radians(bbox.center[0])
    x = np.radians(np.asarray(lon) - bbox.west) * EARTH_RADIUS_M * math.cos(lat0)
    y = np.radians(np.asarray(lat) - bbox.south) * EARTH_RADIUS_M
    return x, y
