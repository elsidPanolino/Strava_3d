"""Local web UI: connect Strava, pick an activity (or upload a GPX), tune and build the model."""

from __future__ import annotations

import json
import tempfile
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory

from .geo import read_gpx, slug
from .guide import print_guide
from .model import ROUTE_STYLES, SHAPES, ModelParams, build_and_export, default_label
from .printers import DEFAULT_PRINTER, PRINTERS
from .strava import Credentials, StravaClient, StravaError, default_token_path

STATIC_DIR = Path(__file__).parent / "static"

# Form field -> (ModelParams attribute, type)
PARAM_FIELDS = {
    "size": ("size_mm", float),
    "resolution": ("cell_mm", float),
    "exaggeration": ("z_exaggeration", float),
    "base": ("base_mm", float),
    "padding": ("padding", float),
    "label_mm": ("label_mm", float),
    "route_width": ("route_width_mm", float),
    "route_height": ("route_height_mm", float),
}
LIMITS = {
    "size_mm": (20, 400),
    "cell_mm": (0.2, 5),
    "z_exaggeration": (0.1, 20),
    "base_mm": (0.5, 30),
    "padding": (0, 2),
    "label_mm": (8, 40),
    "route_width_mm": (0.4, 10),
    "route_height_mm": (0.1, 10),
}


def parse_params(form) -> ModelParams:
    params = ModelParams()
    printer = form.get("printer") or DEFAULT_PRINTER
    if printer not in PRINTERS:
        raise ValueError(f"unknown printer {printer!r}")
    params.printer = printer
    if form.get("layer"):
        params.layer_mm = float(form["layer"])
        if not 0.04 <= params.layer_mm <= 0.4:
            raise ValueError("layer must be between 0.04 and 0.4")
    shape = form.get("shape", "rectangle")
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {SHAPES}")
    params.shape = shape
    params.colors = int(form.get("colors") or 1)
    if params.colors not in (1, 2, 3, 4):
        raise ValueError("colors must be 1-4")
    params.base_color = form.get("base_color") in ("1", "true", "on")
    params.label = form.get("label") in ("1", "true", "on")
    for field, (attr, typ) in PARAM_FIELDS.items():
        if form.get(field) not in (None, ""):
            value = typ(form[field])
            lo, hi = LIMITS[attr]
            if not lo <= value <= hi:
                raise ValueError(f"{field} must be between {lo} and {hi}")
            setattr(params, attr, value)
    route = form.get("route", params.route_style)
    if route not in ROUTE_STYLES:
        raise ValueError(f"route must be one of {ROUTE_STYLES}")
    params.route_style = route
    if form.get("square") in ("1", "true", "on") and shape == "rectangle":
        params.shape = "square"
    params.separate_route = form.get("separate_route") in ("1", "true", "on")
    return params


def create_app(
    out_dir: Path | str = "output",
    token_path: Path | None = None,
    credentials_path: Path | None = None,
    fetcher=None,
) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    out_dir = Path(out_dir).resolve()
    token_path = token_path or default_token_path()

    def client() -> StravaClient:
        creds = Credentials.load(credentials_path)
        if creds is None:
            raise StravaError("Strava API credentials are not set up yet.")
        return StravaClient(creds, token_path)

    @app.errorhandler(StravaError)
    def strava_error(err):
        return jsonify(error=str(err)), 502

    @app.errorhandler(ValueError)
    def value_error(err):
        return jsonify(error=str(err)), 400

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC_DIR, name)

    @app.get("/api/status")
    def status():
        athlete = None
        if token_path.exists():
            try:
                athlete = json.loads(token_path.read_text()).get("athlete") or {}
            except (OSError, json.JSONDecodeError):
                athlete = None
        return jsonify(
            configured=Credentials.load(credentials_path) is not None,
            connected=athlete is not None,
            athlete=athlete,
            callback_url=request.host_url.rstrip("/") + "/callback",
        )

    @app.post("/api/credentials")
    def save_credentials():
        data = request.get_json(force=True)
        cid = str(data.get("client_id", "")).strip()
        secret = str(data.get("client_secret", "")).strip()
        if not cid.isdigit() or not secret:
            raise ValueError("Enter the numeric Client ID and the Client Secret from Strava.")
        Credentials(cid, secret).save(credentials_path)
        return jsonify(ok=True)

    @app.get("/login")
    def login():
        return redirect(client().authorize_url(request.host_url.rstrip("/") + "/callback"))

    @app.get("/callback")
    def callback():
        if "code" not in request.args:
            return redirect("/?error=" + request.args.get("error", "authorization_failed"))
        client().exchange_code(request.args["code"])
        return redirect("/")

    @app.post("/api/logout")
    def logout():
        token_path.unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.get("/api/activities")
    def activities():
        page = int(request.args.get("page", 1))
        acts = client().activities(per_page=30, page=page)
        return jsonify(
            [
                {
                    "id": a["id"],
                    "name": a.get("name", ""),
                    "date": a.get("start_date_local", "")[:10],
                    "type": a.get("sport_type") or a.get("type", ""),
                    "distance_km": round(a.get("distance", 0) / 1000, 1),
                    "elevation_m": round(a.get("total_elevation_gain", 0)),
                    "polyline": (a.get("map") or {}).get("summary_polyline") or "",
                }
                for a in acts
            ]
        )

    @app.post("/api/build")
    def build():
        params = parse_params(request.form)
        upload = request.files.get("gpx")
        if upload and upload.filename:
            with tempfile.NamedTemporaryFile(suffix=".gpx") as tmp:
                upload.save(tmp.name)
                points = read_gpx(tmp.name)
            name = slug(Path(upload.filename).stem)
            title = Path(upload.filename).stem
            label = default_label(title, points)
        elif request.form.get("activity_id"):
            c = client()
            activity_id = int(request.form["activity_id"])
            activity = c.activity(activity_id)
            points = c.activity_latlng(activity_id)
            title = activity.get("name", str(activity_id))
            date = activity.get("start_date_local", "")[:10]
            name = f"{date}-{slug(title)}"
            label = default_label(
                title, points, activity.get("distance", 0) / 1000, activity.get("total_elevation_gain"), date
            )
        else:
            raise ValueError("Pick a Strava activity or upload a GPX file.")

        if request.form.get("label_text", "").strip():
            label[0] = request.form["label_text"].strip()[:60]
        params.label_lines = label
        result = build_and_export(points, name, params, out_dir, fetcher=fetcher)
        files = {k: f"/files/{p.name}" for k, p in result["files"].items()}
        info = result["info"]
        return jsonify(title=title, files=files, info=info, guide=print_guide(info))

    @app.get("/api/printers")
    def printers():
        return jsonify(default=DEFAULT_PRINTER, printers=[p.to_dict() for p in PRINTERS.values()])

    @app.get("/files/<path:name>")
    def files(name):
        return send_from_directory(out_dir, name, as_attachment=request.args.get("download") == "1")

    return app


def serve(host: str = "localhost", port: int = 8765, out_dir="output", open_browser: bool = True):
    app = create_app(out_dir)
    url = f"http://{host}:{port}/"
    print(f"Strava 3D running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host=host, port=port, threaded=True)
