import json
import time

import numpy as np
import pytest

from strava3d.elevation import choose_zoom, decode_terrarium, latlon_to_pixel
from strava3d.geo import BBox, decode_polyline, read_gpx
from strava3d.mesh import heightmap_solid, hexahedron
from strava3d.model import ModelParams, build_model, render_preview
from strava3d.strava import Credentials, StravaClient

# A small loop (~2 km across) used for model tests.
LOOP = [
    (46.5 + 0.009 * np.sin(t), 8.0 + 0.012 * np.cos(t)) for t in np.linspace(0, 2 * np.pi, 200)
]


def fake_elevation(bbox, nx, ny):
    """A smooth hill in the middle of the bbox, 500-900 m."""
    y, x = np.mgrid[0:1:ny * 1j, 0:1:nx * 1j]
    return 500 + 400 * np.exp(-((x - 0.5) ** 2 + (y - 0.5) ** 2) / 0.05)


def test_decode_polyline():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == pytest.approx([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])


def test_decode_terrarium():
    rgb = np.array([[[128, 0, 0], [128, 100, 128]]], dtype=np.uint8)
    assert decode_terrarium(rgb).tolist() == [[0.0, 100.5]]


def test_latlon_to_pixel_origin():
    px, py = latlon_to_pixel(0.0, 0.0, 0)
    assert (float(px), float(py)) == pytest.approx((128, 128))


def test_choose_zoom_bounded():
    bbox = BBox.around(LOOP)
    z = choose_zoom(bbox, 300)
    assert 10 <= z <= 15


def test_bbox_square_and_padding():
    bbox = BBox.around(LOOP, padding=0.1, square=True)
    w, h = bbox.size_m()
    assert w == pytest.approx(h, rel=1e-6)
    assert w > 2000


def test_heightmap_solid_watertight_and_volume():
    z = np.full((5, 7), 2.0)
    mesh = heightmap_solid(z, dx=1.0, dy=1.0)
    assert mesh.is_watertight()
    assert mesh.volume() == pytest.approx(6 * 4 * 2.0)


def test_heightmap_solid_irregular():
    rng = np.random.default_rng(0)
    z = 3 + rng.random((20, 30))
    mesh = heightmap_solid(z, 0.5, 0.7)
    assert mesh.is_watertight()
    assert mesh.volume() > 0


def test_hexahedron():
    b = np.array([[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0]], float)
    t = b + [0, 0, 3]
    mesh = hexahedron(b, t)
    assert mesh.is_watertight()
    assert mesh.volume() == pytest.approx(6.0)


@pytest.mark.parametrize("style", ["raised", "groove", "none"])
def test_build_model_styles(style):
    params = ModelParams(size_mm=60, cell_mm=1.0, route_style=style)
    model = build_model(LOOP, params, elevation=fake_elevation)
    assert model.terrain.is_watertight()
    assert model.terrain.volume() > 0
    assert model.route is None
    assert max(model.info["size_mm"][:2]) == pytest.approx(60, abs=0.1)
    assert model.heightmap.min() >= params.base_mm - params.route_height_mm - 1e-9


def test_build_model_separate_route(tmp_path):
    params = ModelParams(size_mm=60, cell_mm=1.0, separate_route=True)
    model = build_model(LOOP, params, elevation=fake_elevation)
    assert model.terrain.is_watertight()
    assert model.route is not None and model.route.volume() > 0
    model.route.write_stl(tmp_path / "route.stl")
    render_preview(model, tmp_path / "preview.png")
    assert (tmp_path / "preview.png").stat().st_size > 0


def test_write_stl(tmp_path):
    mesh = heightmap_solid(np.full((3, 3), 1.0), 1, 1)
    path = tmp_path / "x.stl"
    mesh.write_stl(path)
    assert path.stat().st_size == 84 + 50 * len(mesh.faces)


def test_read_gpx(tmp_path):
    gpx = tmp_path / "t.gpx"
    gpx.write_text(
        '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        '<trkpt lat="1.5" lon="2.5"/><trkpt lat="1.6" lon="2.6"/></trkseg></trk></gpx>'
    )
    assert read_gpx(gpx) == [(1.5, 2.5), (1.6, 2.6)]


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.posts, self.gets = [], []

    def post(self, url, data=None, timeout=None):
        self.posts.append(data)
        return FakeResponse(
            {"access_token": "new", "refresh_token": "r2", "expires_at": time.time() + 3600}
        )

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets.append((url, headers))
        return FakeResponse({"latlng": {"data": [[1.0, 2.0], [1.1, 2.1]]}})


def test_strava_client_refreshes_and_reads_streams(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))
    session = FakeSession()
    client = StravaClient(Credentials("id", "secret"), token_path, session)

    assert client.activity_latlng(42) == [(1.0, 2.0), (1.1, 2.1)]
    assert session.posts[0]["grant_type"] == "refresh_token"
    assert session.gets[0][1]["Authorization"] == "Bearer new"
    assert json.loads(token_path.read_text())["refresh_token"] == "r2"


def test_authorize_url():
    client = StravaClient(Credentials("123", "s"), session=FakeSession())
    url = client.authorize_url("http://localhost:8765/callback")
    assert "client_id=123" in url and "activity%3Aread_all" in url
