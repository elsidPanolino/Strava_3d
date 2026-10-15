"""Elevation data from the free AWS Terrain Tiles (Terrarium encoding, global, no API key)."""

from __future__ import annotations

import io
import math
import os
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from .geo import BBox

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_SIZE = 256
MAX_ZOOM = 15
MAX_TILES = 64


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "strava3d" / "tiles"


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    """Terrarium PNG -> elevation in metres: (R * 256 + G + B / 256) - 32768."""
    rgb = rgb.astype(np.float64)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def latlon_to_pixel(lat, lon, zoom: int):
    """Web Mercator global pixel coordinates at a zoom level."""
    lat = np.clip(np.asarray(lat, dtype=float), -85.05112878, 85.05112878)
    lon = np.asarray(lon, dtype=float)
    n = TILE_SIZE * 2**zoom
    px = (lon + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    py = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return px, py


def choose_zoom(bbox: BBox, samples: int) -> int:
    """Highest zoom (<= 15) whose tile count stays bounded, preferring >= `samples` pixels."""
    best = 1
    for z in range(1, MAX_ZOOM + 1):
        x0, y0 = latlon_to_pixel(bbox.north, bbox.west, z)
        x1, y1 = latlon_to_pixel(bbox.south, bbox.east, z)
        tiles = (int(x1 // TILE_SIZE) - int(x0 // TILE_SIZE) + 1) * (
            int(y1 // TILE_SIZE) - int(y0 // TILE_SIZE) + 1
        )
        if tiles > MAX_TILES:
            break
        best = z
        if max(x1 - x0, y1 - y0) >= samples * 1.5:
            break
    return best


class TileFetcher:
    def __init__(self, cache: Path | None = None, session=None):
        self.cache = cache or cache_dir()
        self.session = session or requests.Session()

    def tile(self, z: int, x: int, y: int) -> np.ndarray:
        path = self.cache / str(z) / str(x) / f"{y}.png"
        if path.exists():
            data = path.read_bytes()
        else:
            resp = self.session.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
            resp.raise_for_status()
            data = resp.content
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return decode_terrarium(np.asarray(img))


def elevation_grid(
    bbox: BBox,
    nx: int,
    ny: int,
    fetcher: TileFetcher | None = None,
    zoom: int | None = None,
) -> np.ndarray:
    """Sample elevation (metres) on an ny x nx lat/lon grid; row 0 is the south edge."""
    fetcher = fetcher or TileFetcher()
    zoom = zoom or choose_zoom(bbox, max(nx, ny))

    px0, py0 = latlon_to_pixel(bbox.north, bbox.west, zoom)
    px1, py1 = latlon_to_pixel(bbox.south, bbox.east, zoom)
    tx0, ty0 = int(px0 // TILE_SIZE), int(py0 // TILE_SIZE)
    tx1, ty1 = int(px1 // TILE_SIZE), int(py1 // TILE_SIZE)
    n_tiles = 2**zoom

    mosaic = np.zeros(((ty1 - ty0 + 1) * TILE_SIZE, (tx1 - tx0 + 1) * TILE_SIZE))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = fetcher.tile(zoom, tx % n_tiles, min(max(ty, 0), n_tiles - 1))
            r, c = (ty - ty0) * TILE_SIZE, (tx - tx0) * TILE_SIZE
            mosaic[r : r + TILE_SIZE, c : c + TILE_SIZE] = tile

    lats = np.linspace(bbox.south, bbox.north, ny)
    lons = np.linspace(bbox.west, bbox.east, nx)
    lon_g, lat_g = np.meshgrid(lons, lats)
    px, py = latlon_to_pixel(lat_g, lon_g, zoom)
    # Pixel centres sit at +0.5; bilinear interpolation in mosaic coordinates.
    fx = px - tx0 * TILE_SIZE - 0.5
    fy = py - ty0 * TILE_SIZE - 0.5
    return _bilinear(mosaic, fx, fy)


def _bilinear(img: np.ndarray, fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    h, w = img.shape
    fx = np.clip(fx, 0, w - 1)
    fy = np.clip(fy, 0, h - 1)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    ax = fx - x0
    ay = fy - y0
    top = img[y0, x0] * (1 - ax) + img[y0, x1] * ax
    bot = img[y1, x0] * (1 - ax) + img[y1, x1] * ax
    return top * (1 - ay) + bot * ay
