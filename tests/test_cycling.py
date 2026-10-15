from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from strava3d.geo import read_gpx_track
from strava3d.model import ModelParams, build_model
from strava3d.stats import fmt_date, fmt_duration, gpx_stats, label_lines, strava_stats

# A ride along a coast: west half of the box is sea (-200 m), east half rises to 800 m.
RIDE = [(10.0 + 0.002 * i, 118.75 + 0.0005 * np.sin(i / 5)) for i in range(60)]


def island(bbox, nx, ny):
    x = np.linspace(-1, 1, nx)[None, :].repeat(ny, axis=0)
    return np.where(x < -0.2, -200.0, (x + 0.2) * 666)


def test_sea_flattened_and_land_lifted():
    m = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80), elevation=island)
    info = m.info
    assert info["water"] and 0.3 < info["sea_fraction"] < 0.5
    z = m.heightmap
    nx = z.shape[1]
    sea = z[:, : int(nx * 0.3)]
    assert np.allclose(sea, info["base_mm"])  # flat sea at the base top
    land = z[:, int(nx * 0.5):]
    assert land.min() >= info["base_mm"] + 0.6 - 1e-6  # land lifted 3 layers
    assert info["elevation_range_m"][0] == 1.0  # land range from the 1 m coastline, not the sea floor
    assert m.terrain.is_watertight()


def test_water_off_and_sea_colour_band():
    off = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, water="off"), elevation=island)
    assert not off.info["water"]
    on = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, base_color=True), elevation=island)
    assert on.info["bands"][0]["z"] == on.info["base_mm"]  # sea/base colour change


def test_auto_exaggeration_targets_relief():
    m = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=100, water="off"), elevation=island)
    relief = m.info["size_mm"][2] - m.info["base_mm"]
    assert relief == pytest.approx(12, rel=0.15)  # 12% of 100 mm
    fixed = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=100, z_exaggeration=1.0), elevation=island)
    assert fixed.info["exaggeration"] == 1.0


def test_strava_stats_and_label():
    a = {"distance": 81234, "moving_time": 12000, "elapsed_time": 13000, "total_elevation_gain": 1234,
         "average_speed": 6.77, "max_speed": 15, "average_watts": 170, "kudos_count": 0,
         "start_date_local": "2026-10-11T06:00:00Z", "sport_type": "GravelRide"}
    s = strava_stats(a)
    assert s["avg_speed_kmh"] == 24.4 and s["max_speed_kmh"] == 54.0 and "kudos" not in s
    lines = label_lines("Gravel day", s, ("distance", "elevation", "time", "speed", "date"))
    assert lines == ["Gravel day", "81.2 km  ·  1,234 m elevation  ·  3h 20m", "24.4 km/h  ·  11 Oct 2026"]
    assert label_lines("x", s, ("distance",)) == ["x", "81.2 km"]
    assert fmt_duration(3540) == "59 min" and fmt_date("2026-01-05") == "5 Jan 2026"


def test_gpx_track_stats(tmp_path):
    t0 = datetime(2026, 9, 27, 5, 30, tzinfo=timezone.utc)
    body = []
    for i in range(101):
        lat, lon = 9.74 + i * 0.0005, 118.73
        stop = 300 if i > 50 else 0  # 5 min stop halfway
        t = t0 + timedelta(seconds=i * 10 + stop)
        ele = 20 + (i if i <= 50 else 100 - i)  # +50 m then -50 m
        body.append(f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele><time>{t:%Y-%m-%dT%H:%M:%SZ}</time></trkpt>')
    gpx = tmp_path / "r.gpx"
    gpx.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><metadata><name>File</name></metadata>'
                   '<trk><name>Morning Ride</name><trkseg>' + "".join(body) + "</trkseg></trk></gpx>")
    track = read_gpx_track(gpx)
    assert track["name"] == "Morning Ride"
    s = gpx_stats(track)
    assert s["distance_km"] == pytest.approx(5.56, abs=0.05)
    assert s["elevation_gain_m"] == pytest.approx(48, abs=4)
    assert s["moving_time_s"] == pytest.approx(1000, abs=10)  # the segment containing the stop is excluded
    assert s["elapsed_time_s"] == 1300
    assert s["avg_speed_kmh"] == pytest.approx(20.0, abs=0.5)
    assert s["date"] == "2026-09-27"


def flat_coast(bbox, nx, ny):
    """Very gentle coastal plain: 0.2 m rise per column, sea on the west."""
    x = np.arange(nx)[None, :].repeat(ny, axis=0)
    return (x - nx * 0.4) * 0.2


def test_coastline_is_a_crisp_step_even_on_flat_coast():
    m = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, shape="square", route_style="none"),
                    elevation=flat_coast)
    base, step = m.info["base_mm"], m.info["coast_step_mm"]
    assert step == pytest.approx(0.4)  # default: subtle 2-layer step
    row = m.heightmap[m.heightmap.shape[0] // 2]
    partial = (row > base + 1e-6) & (row < base + step - 1e-6)
    assert partial.sum() <= 2  # the shoreline ramp is at most ~one cell wide
    assert row[: int(len(row) * 0.35)].max() == pytest.approx(base)
    assert row[int(len(row) * 0.45):].min() >= base + step - 1e-6


def test_sea_colour_is_automatic_and_can_be_turned_off():
    auto = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80), elevation=island)
    assert auto.info["sea_color"] and auto.info["bands"][0]["z"] == auto.info["base_mm"]
    off = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, sea_color=False), elevation=island)
    assert not off.info["sea_color"] and off.info["bands"] == []
    inland = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80), elevation=lambda b, nx, ny: 100 + np.zeros((ny, nx)) + np.arange(nx))
    assert not inland.info["water"] and inland.info["bands"] == []


def sloping_seabed(bbox, nx, ny):
    """Land on the east, sea floor shelving from 0 m at the coast to -300 m in the west."""
    x = np.linspace(-1, 1, nx)[None, :].repeat(ny, axis=0)
    return np.where(x < -0.2, (x + 0.2) * 375, (x + 0.2) * 666)


def test_wave_sea_stays_below_the_surface_and_calms_at_the_shore():
    m = build_model(RIDE, ModelParams(cell_mm=0.5, size_mm=80, shape="square", route_style="none",
                                      sea_style="waves"), elevation=island)
    base, z = m.info["base_mm"], m.heightmap
    sea = z[:, : int(z.shape[1] * 0.3)]
    assert sea.max() <= base + 1e-9  # waves never rise above the sea/land colour change
    assert sea.min() == pytest.approx(base - 0.4, abs=0.05)  # 0.4 mm texture
    assert m.terrain.is_watertight() and m.info["sea_style"] == "waves"


def test_depth_sea_follows_the_seabed_and_keeps_a_solid_floor():
    m = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, shape="square", route_style="none",
                                      sea_style="depth", sea_depth_mm=1.6), elevation=sloping_seabed)
    base, z, info = m.info["base_mm"], m.heightmap, m.info
    row = z[z.shape[0] // 2]
    sea = row[: int(len(row) * 0.38)]
    assert np.all(np.diff(sea) >= -1e-6)  # shallower (higher) towards the coast
    assert sea.min() == pytest.approx(base - 1.6, abs=0.05) and sea.max() <= base + 1e-9
    assert info["sea_depth_mm"] == pytest.approx(1.6)
    # Too deep for the base: limited to base - 1 mm with a warning.
    deep = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, sea_style="depth", sea_depth_mm=3.0),
                       elevation=sloping_seabed)
    assert deep.info["sea_depth_mm"] == pytest.approx(2.0)
    assert any("Sea depth limited" in w for w in deep.info["warnings"])


def test_depth_sea_ignores_corrupt_rows_in_the_bathymetry():
    def corrupt(bbox, nx, ny):
        e = sloping_seabed(bbox, nx, ny)
        e[ny // 2] = np.where(e[ny // 2] < 0, -4865.0, e[ny // 2])  # bad data row seen off Palawan
        return e

    clean = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, shape="square", route_style="none",
                                          sea_style="depth"), elevation=sloping_seabed)
    bad = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, shape="square", route_style="none",
                                        sea_style="depth"), elevation=corrupt)
    assert np.abs(bad.heightmap - clean.heightmap).max() < 0.1  # under half a layer (unfiltered: ~1.6 mm groove)


def test_shallow_water_colour_band():
    from strava3d.guide import print_guide

    m = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, sea_style="depth", shallow_color=True),
                    elevation=sloping_seabed)
    info = m.info
    bands = info["bands"]
    assert info["shallow_color"] and len(bands) == 2
    assert bands[0]["z"] < bands[1]["z"] == info["base_mm"]  # shallow change inside the sea, then land
    assert any("shallow water" in g for g in print_guide({**info, "route_style": "raised"}))
    # Ignored without the depth style.
    flat = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, shallow_color=True), elevation=sloping_seabed)
    assert not flat.info["shallow_color"] and len(flat.info["bands"]) == 1


def test_single_color_overrides_every_colour_option():
    from strava3d.guide import print_guide

    m = build_model(RIDE, ModelParams(cell_mm=1.0, size_mm=80, single_color=True, colors=4, base_color=True,
                                      separate_route=True), elevation=island)
    info = m.info
    assert info["single_color"] and info["bands"] == [] and not info["sea_color"]
    assert m.route is None  # no separate route part
    assert info["water"]  # the sea is still flattened, so the coastline step remains
    guide = print_guide({**info, "route_style": "raised"})
    assert any("One colour" in g for g in guide)
    assert not any("Colour bands" in g or "Tip:" in g for g in guide)
