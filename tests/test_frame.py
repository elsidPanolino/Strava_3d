import io

import numpy as np
import pytest

from strava3d.frame import FrameParams, build_frame, frame_guide


def test_frame_covers_highest_point_and_fits_the_tile():
    mesh, info = build_frame(FrameParams(tile_w=140, tile_d=120, tile_h=22.3, wall_mm=4, clearance_mm=0.4,
                                         headroom_mm=2, floor_mm=1.6))
    assert mesh.is_watertight() and mesh.volume() > 0
    w, d, h = info["outer_mm"]
    assert info["opening_mm"] == [140.8, 120.8]
    assert h >= 1.6 + 22.3 + 2 - 1e-6  # floor + tile + headroom: every peak is covered
    assert info["headroom_mm"] >= 2 - 1e-6
    assert w == pytest.approx(140.8 + 8) and d == pytest.approx(120.8 + 8)  # no text: plain 4 mm walls
    assert info["fits"]  # 148.8 x 128.8 mm fits the A1 mini
    lo, hi = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)
    assert lo[2] == 0 and hi[0] == pytest.approx(w) and hi[1] == pytest.approx(d)


def test_frame_text_is_raised_on_the_front_band():
    mesh, info = build_frame(FrameParams(tile_w=100, tile_d=100, tile_h=15, title="Morning Ride",
                                         stats="65.8 km · 582 m elevation · 3h 25m"))
    h = info["outer_mm"][2]
    top = mesh.vertices[:, 2].max()
    assert top == pytest.approx(h + 0.6)  # 3 layers of raised text
    raised = mesh.vertices[mesh.vertices[:, 2] > h + 0.3]
    assert raised[:, 1].max() <= info["band_mm"]  # only on the front band
    plain, pinfo = build_frame(FrameParams(tile_w=100, tile_d=100, tile_h=15))
    assert not pinfo["has_text"] and pinfo["band_mm"] == 4.0
    assert plain.vertices[:, 2].max() == pytest.approx(pinfo["outer_mm"][2])


def test_frame_too_big_for_bed_suggests_tile_size():
    _, info = build_frame(FrameParams(tile_w=170, tile_d=170, tile_h=20, title="x"))
    assert not info["fits"]
    assert any("Rebuild the terrain" in w for w in info["warnings"])
    _, open_info = build_frame(FrameParams(tile_w=150, tile_d=150, tile_h=20, floor=False))
    assert open_info["floor_mm"] == 0
    assert any("Drop the terrain tile" in g for g in frame_guide(open_info))


def test_frame_page_flow(tmp_path, monkeypatch):
    import strava3d.model as mm
    from strava3d import web

    monkeypatch.setattr(mm, "elevation_grid",
                        lambda bbox, nx, ny, fetcher=None: 100 + 50 * np.random.default_rng(0).random((ny, nx)))
    monkeypatch.chdir(tmp_path)
    app = web.create_app(tmp_path / "out", token_path=tmp_path / "t.json", credentials_path=tmp_path / "c.json")
    c = app.test_client()
    gpx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Hill Loop</name><trkseg>'
           + "".join(f'<trkpt lat="{46.5 + 0.01 * np.sin(t):.6f}" lon="{8 + 0.012 * np.cos(t):.6f}"/>'
                     for t in np.linspace(0, 6, 40)) + "</trkseg></trk></gpx>")
    r = c.post("/api/build", data={"size": "120", "resolution": "1.5", "gpx": (io.BytesIO(gpx.encode()), "hill.gpx")},
               content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    name = r.get_json()["name"]
    models = c.get("/api/models").get_json()
    assert models[0]["name"] == name and models[0]["title"] == "Hill Loop"
    assert c.get("/frame").status_code == 200
    f = c.post("/api/frame", json={"model": name, "title": "Hill Loop", "stats": "12 km"}).get_json()
    assert f["info"]["has_text"] and f["info"]["tile_mm"] == models[0]["size_mm"]
    assert c.get(f["files"]["3mf"]).status_code == 200
    custom = c.post("/api/frame", json={"tile_w": 100, "tile_d": 80, "tile_h": 12})
    assert custom.status_code == 200 and custom.get_json()["info"]["opening_mm"] == [100.8, 80.8]
    assert c.post("/api/frame", json={"model": "nope"}).status_code == 400
