import io
import json

import numpy as np
import pytest

import strava3d.model as model_mod
from strava3d import web
from strava3d.strava import StravaClient

GPX = (
    '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
    + "".join(
        f'<trkpt lat="{46.5 + 0.01 * np.sin(t):.6f}" lon="{8 + 0.012 * np.cos(t):.6f}"/>'
        for t in np.linspace(0, 6.28, 50)
    )
    + "</trkseg></trk></gpx>"
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setattr(
        model_mod,
        "elevation_grid",
        lambda bbox, nx, ny, fetcher=None: 100 + 50 * np.random.default_rng(0).random((ny, nx)),
    )
    a = web.create_app(
        out_dir=tmp_path / "out",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "creds.json",
    )
    a.testing = True
    return a


def test_status_and_credentials(app, tmp_path):
    c = app.test_client()
    s = c.get("/api/status").get_json()
    assert s == {
        "configured": False,
        "connected": False,
        "athlete": None,
        "callback_url": "http://localhost/callback",
    }
    assert c.post("/api/credentials", json={"client_id": "abc", "client_secret": "x"}).status_code == 400
    assert c.post("/api/credentials", json={"client_id": "123", "client_secret": "x"}).status_code == 200
    assert c.get("/api/status").get_json()["configured"] is True

    r = c.get("/login")
    assert r.status_code == 302 and "client_id=123" in r.location

    (tmp_path / "token.json").write_text(json.dumps({"athlete": {"firstname": "Ann"}}))
    assert c.get("/api/status").get_json()["athlete"] == {"firstname": "Ann"}
    c.post("/api/logout")
    assert not (tmp_path / "token.json").exists()


def test_index_served(app):
    r = app.test_client().get("/")
    assert r.status_code == 200 and b"Strava 3D" in r.data


def test_build_from_gpx(app):
    c = app.test_client()
    r = c.post(
        "/api/build",
        data={"size": "60", "resolution": "1", "route": "raised", "gpx": (io.BytesIO(GPX.encode()), "My Ride.gpx")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["title"] == "My Ride"
    assert body["files"]["terrain"] == "/files/my-ride-terrain.stl"
    assert "route" not in body["files"]
    assert max(body["info"]["size_mm"][:2]) == pytest.approx(60, abs=0.1)
    assert len(body["info"]["route_mm"]) > 10
    stl = c.get(body["files"]["terrain"])
    assert stl.status_code == 200 and len(stl.data) == 84 + 50 * body["info"]["triangles"]


def test_build_from_activity(app, monkeypatch):
    c = app.test_client()
    c.post("/api/credentials", json={"client_id": "1", "client_secret": "s"})
    monkeypatch.setattr(StravaClient, "activity", lambda self, i: {"name": "Alp Run", "start_date_local": "2026-05-01T08:00:00Z"})
    monkeypatch.setattr(
        StravaClient, "activity_latlng", lambda self, i: [(46.5, 8.0), (46.51, 8.01), (46.52, 8.0)]
    )
    r = c.post("/api/build", data={"activity_id": "42", "resolution": "1", "size": "50", "separate_route": "1"})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["files"]["terrain"] == "/files/2026-05-01-alp-run-terrain.stl"
    assert body["files"]["route"] == "/files/2026-05-01-alp-run-route.stl"
    assert body["info"]["route_style"] == "separate"


def test_build_validation(app):
    c = app.test_client()
    assert c.post("/api/build", data={}).status_code == 400
    r = c.post("/api/build", data={"size": "9999", "activity_id": "1"})
    assert r.status_code == 400 and "size" in r.get_json()["error"]


def test_activities_requires_setup(app):
    r = app.test_client().get("/api/activities")
    assert r.status_code == 502 and "credentials" in r.get_json()["error"]


def test_printers_endpoint(app):
    r = app.test_client().get("/api/printers").get_json()
    assert r["default"] == "a1mini"
    mini = next(p for p in r["printers"] if p["key"] == "a1mini")
    assert mini["bed"] == [180, 180, 180] and mini["max_size"] == 170


def test_build_with_new_options(app):
    c = app.test_client()
    data = {
        "printer": "a1mini", "layer": "0.16", "shape": "rectangle", "colors": "3", "base_color": "1",
        "label": "1", "label_text": "Sunday Climb", "resolution": "1",
        "gpx": (io.BytesIO(GPX.encode()), "ride.gpx"),
    }
    r = c.post("/api/build", data=data, content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 200, body
    info = body["info"]
    assert info["layer_mm"] == 0.16 and info["fits"]
    assert len(info["bands"]) == 3 and info["label_mm"] > 0
    assert body["files"]["3mf"].endswith(".3mf")
    assert any("0.16mm Optimal @BBL A1M" in g for g in body["guide"])
    assert c.get(body["files"]["3mf"]).status_code == 200


def test_build_rejects_bad_options(app):
    c = app.test_client()
    for bad in ({"printer": "nope"}, {"shape": "star"}, {"colors": "7"}, {"layer": "2"}):
        r = c.post("/api/build", data={"activity_id": "1", **bad})
        assert r.status_code == 400, bad


def test_athlete_stats_endpoint(app, monkeypatch):
    c = app.test_client()
    c.post("/api/credentials", json={"client_id": "1", "client_secret": "s"})
    monkeypatch.setattr(StravaClient, "athlete_stats", lambda self, athlete_id=None: {
        "biggest_ride_distance": 120500.0, "biggest_climb_elevation_gain": 900.0,
        "ytd_ride_totals": {"count": 10, "distance": 500000.0, "elevation_gain": 4000.0, "moving_time": 72000},
    })
    r = c.get("/api/athlete-stats").get_json()
    assert r["biggest_ride_km"] == 120.5
    assert r["ride"]["ytd"] == {"count": 10, "distance_km": 500, "elevation_m": 4000, "moving_time_s": 72000}
    assert r["run"]["all"]["count"] == 0


def test_build_returns_ride_stats(app, monkeypatch):
    c = app.test_client()
    c.post("/api/credentials", json={"client_id": "1", "client_secret": "s"})
    monkeypatch.setattr(StravaClient, "activity", lambda self, i: {
        "name": "Palawan loop", "start_date_local": "2026-09-27T05:30:00Z", "sport_type": "Ride",
        "distance": 60000, "moving_time": 9000, "average_speed": 6.67, "total_elevation_gain": 700})
    monkeypatch.setattr(StravaClient, "activity_latlng", lambda self, i: [(9.74, 118.73), (9.8, 118.75), (9.85, 118.7)])
    r = c.post("/api/build", data={"activity_id": "5", "resolution": "1.5", "label": "1",
                                   "label_stats": "distance,speed", "exaggeration": "auto", "water": "auto"})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["stats"]["avg_speed_kmh"] == 24.0 and body["stats"]["distance_km"] == 60.0
    assert body["info"]["exaggeration"] >= 1
