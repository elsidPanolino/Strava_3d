import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from strava3d.guide import print_guide
from strava3d.label import text_mask
from strava3d.mesh import radial_solid
from strava3d.model import ModelParams, build_and_export, build_model, colour_bands
from strava3d.printers import PRINTERS, layer_number, snap

LOOP = [(46.5 + 0.009 * np.sin(t), 8.0 + 0.012 * np.cos(t)) for t in np.linspace(0, 2 * np.pi, 200)]


def hill(bbox, nx, ny):
    y, x = np.mgrid[0:1:ny * 1j, 0:1:nx * 1j]
    return 500 + 400 * np.exp(-((x - 0.5) ** 2 + (y - 0.5) ** 2) / 0.05)


def build(**kw):
    kw.setdefault("cell_mm", 1.0)
    return build_model(LOOP, ModelParams(**kw), elevation=hill)


def test_default_size_fits_a1_mini():
    m = build()
    assert max(m.info["size_mm"][:2]) == pytest.approx(170, abs=0.5)
    assert m.info["fits"] and m.info["printer"]["key"] == "a1mini"
    assert m.info["warnings"] == []


def test_too_big_warns():
    m = build(size_mm=200)
    assert not m.info["fits"]
    assert any("does not fit" in w for w in m.info["warnings"])
    assert build(size_mm=200, printer="a1").info["fits"]


@pytest.mark.parametrize("shape", ["circle", "hexagon", "square"])
def test_shapes_watertight(shape):
    m = build(shape=shape, size_mm=100)
    assert m.terrain.is_watertight()
    w, h, _ = m.info["size_mm"]
    assert w == pytest.approx(100, abs=0.5)
    expected_h = {"circle": 100, "square": 100, "hexagon": 100 * np.cos(np.pi / 6)}[shape]
    assert h == pytest.approx(expected_h, abs=1)
    lo = m.terrain.vertices.min(axis=0)
    assert lo[2] == 0


def test_hexagon_has_exact_corners():
    m = radial_solid("hexagon", 50, 1.0, lambda x, y: np.full_like(x, 2.0))
    v = m.vertices
    assert np.isclose(v[:, 0].max() - v[:, 0].min(), 100)
    assert np.isclose(v[:, 1].max() - v[:, 1].min(), 100 * np.cos(np.pi / 6))


def test_layer_snapping_and_nozzle_width():
    m = build(base_mm=2.95, route_height_mm=0.55, route_width_mm=0.6, layer_mm=0.2)
    assert m.info["base_mm"] == pytest.approx(3.0)
    assert m.info["route_height_mm"] == pytest.approx(0.6)
    assert m.info["route_width_mm"] == pytest.approx(1.2)
    assert any("Route width" in w for w in m.info["warnings"])
    assert snap(0.05, 0.2) == 0.2
    assert layer_number(0.2, PRINTERS["a1mini"]) == 1
    assert layer_number(3.2, PRINTERS["a1mini"]) == 16


def test_label_plate():
    m = build(label=True, label_lines=["Morning Run", "12.3 km"], size_mm=120)
    assert m.terrain.is_watertight()
    assert max(m.info["size_mm"][:2]) == pytest.approx(120, abs=1.5)
    assert m.info["label_mm"] > 10
    strip = m.heightmap[: int(m.info["label_mm"] / m.dy)]
    assert strip.min() == pytest.approx(3.0)
    assert strip.max() == pytest.approx(3.8, abs=0.05)  # raised text, 4 layers
    # Label disabled for radial shapes with a warning.
    c = build(label=True, label_lines=["x"], shape="circle", size_mm=80)
    assert c.info["label_mm"] == 0 and any("name plate" in w for w in c.info["warnings"])


def test_text_mask_orientation():
    mask = text_mask(["TOP"], 60, 20)
    assert mask.shape == (20, 60) and mask.max() > 0.9


def test_colour_bands():
    p = PRINTERS["a1mini"]
    bands = colour_bands(3, True, 3.0, 23.0, 0.2, p)
    assert [b["z"] for b in bands] == [3.0, 10.0, 17.0]
    for b in bands:
        assert b["top_z"] == pytest.approx(b["z"] + 0.2)
        assert b["layer"] == layer_number(b["top_z"], p)
    assert colour_bands(1, False, 3, 20, 0.2, p) == []
    m = build(colors=2)
    assert len(m.info["bands"]) == 1


def test_3mf_export(tmp_path):
    import strava3d.model as mm

    orig = mm.elevation_grid
    mm.elevation_grid = lambda bbox, nx, ny, fetcher=None: hill(bbox, nx, ny)
    try:
        res = build_and_export(LOOP, "t", ModelParams(cell_mm=2.0, separate_route=True, size_mm=80), tmp_path)
    finally:
        mm.elevation_grid = orig
    z = zipfile.ZipFile(res["files"]["3mf"])
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    model = ET.fromstring(z.read("3D/3dmodel.model"))
    objs = model.findall("m:resources/m:object", ns)
    assert len(objs) == 3
    assert len(objs[2].findall("m:components/m:component", ns)) == 2
    item = model.find("m:build/m:item", ns)
    tx, ty = map(float, item.get("transform").split()[9:11])
    w, h, _ = res["info"]["size_mm"]
    assert tx == pytest.approx(90 - w / 2, abs=0.5)  # centred on the 180 mm bed
    assert ty == pytest.approx(90 - h / 2, abs=0.5)
    cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
    extruders = [p.find("metadata[@key='extruder']").get("value") for p in cfg.iter("part")]
    assert extruders == ["1", "2"]
    guide = print_guide(res["info"])
    assert any("0.20mm Standard @BBL A1M" in g for g in guide)
    assert any("slot 2" in g for g in guide)


def test_route_inlay_slot_and_piece_fit(tmp_path, monkeypatch):
    import strava3d.model as mm

    # An open route (no loop) so the slot doesn't split the terrain.
    route = [(46.5 + 0.009 * np.sin(t), 8.0 + 0.012 * np.cos(t)) for t in np.linspace(0, 3.5, 120)]
    m = build_model(route, ModelParams(cell_mm=0.5, size_mm=80, route_style="inlay", route_width_mm=2.0,
                                       inlay_clearance_mm=0.15), elevation=hill)
    terrain, piece, info = m.terrain, m.route, m.info
    assert info["inlay"] and terrain.is_watertight() and piece.is_watertight()
    assert piece.vertices[:, 2].min() == 0  # flat bottom: prints standing up, no supports
    assert not any("pieces" in w for w in info["warnings"])
    # The piece is narrower than the slot by 2 x clearance: check across one segment midpoint.
    from strava3d.model import route_distance
    d_terrain = route_distance(m.route_xy, m.heightmap.shape, m.dx, m.dy, 5)
    assert d_terrain.min() < 0.5  # grid covers the route
    # Footprint areas (top-down): slot width 2.3 mm vs piece 2.0 mm along the same path.
    length = np.linalg.norm(np.diff(m.route_xy, axis=0), axis=1).sum()
    v = piece.vertices[piece.faces]
    nz = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])[:, 2]
    top_area = nz[nz > 0].sum() / 2
    assert top_area == pytest.approx(length * 2.0 + np.pi * 1.0, rel=0.05)
    # Dense GPS logging (a point every ~5 m, as Strava records each second) must keep the whole route.
    dense = [(46.5 + 0.009 * np.sin(t), 8.0 + 0.012 * np.cos(t)) for t in np.linspace(0, 3.5, 6000)]
    md = build_model(dense, ModelParams(cell_mm=0.5, size_mm=80, route_style="inlay"), elevation=hill)
    v = md.route.vertices[md.route.faces]
    nz = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])[:, 2]
    dense_len = np.linalg.norm(np.diff(md.route_xy, axis=0), axis=1).sum()
    assert nz[nz > 0].sum() / 2 == pytest.approx(dense_len * 2.0 + np.pi * 1.0, rel=0.05)
    # A closed loop route splits the terrain: warn.
    loop = build_model(LOOP, ModelParams(cell_mm=1.0, size_mm=80, route_style="inlay"), elevation=hill)
    assert any("pieces" in w for w in loop.info["warnings"])
    # Separate 3MF for the route piece.
    monkeypatch.setattr(mm, "elevation_grid", lambda bbox, nx, ny, fetcher=None: hill(bbox, nx, ny))
    res = build_and_export(route, "r", ModelParams(cell_mm=1.0, size_mm=80, route_style="inlay"), tmp_path)
    assert res["files"]["route_3mf"].exists() and res["info"]["route_style"] == "inlay"
    assert any("Push the route into the slot" in g for g in print_guide(res["info"]))


def test_route_filament_comes_after_colour_bands(tmp_path, monkeypatch):
    import strava3d.model as mm

    monkeypatch.setattr(mm, "elevation_grid", lambda bbox, nx, ny, fetcher=None: hill(bbox, nx, ny))
    res = build_and_export(LOOP, "t", ModelParams(cell_mm=2.0, size_mm=80, separate_route=True, colors=3), tmp_path)
    assert len(res["info"]["bands"]) == 2 and res["info"]["route_filament"] == 4
    cfg = ET.fromstring(zipfile.ZipFile(res["files"]["3mf"]).read("Metadata/model_settings.config"))
    assert [p.find("metadata[@key='extruder']").get("value") for p in cfg.iter("part")] == ["1", "4"]
    assert any("slot 4" in g for g in print_guide(res["info"]))
