"""Command line interface: `strava3d auth | activities | build | gpx`."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .geo import read_gpx, slug
from .model import ROUTE_STYLES, ModelParams, build_and_export
from .strava import DEFAULT_PORT, Credentials, StravaClient


def _client() -> StravaClient:
    return StravaClient(Credentials.from_env())


def cmd_auth(args) -> None:
    client = _client()
    token = client.login(port=args.port, open_browser=not args.no_browser)
    athlete = token.get("athlete") or {}
    name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
    print(f"Connected to Strava{f' as {name}' if name else ''}. Token saved to {client.token_path}")


def cmd_activities(args) -> None:
    acts = _client().activities(per_page=args.limit, page=args.page)
    print(f"{'ID':>12}  {'Date':10}  {'Type':12} {'km':>7} {'Elev m':>7}  Name")
    for a in acts:
        date = a.get("start_date_local", "")[:10]
        km = a.get("distance", 0) / 1000
        gps = "" if (a.get("map") or {}).get("summary_polyline") else "  (no GPS)"
        print(
            f"{a['id']:>12}  {date:10}  {a.get('sport_type', a.get('type', '')):12} "
            f"{km:7.1f} {a.get('total_elevation_gain', 0):7.0f}  {a.get('name', '')}{gps}"
        )


def _params(args) -> ModelParams:
    return ModelParams(
        size_mm=args.size,
        cell_mm=args.resolution,
        z_exaggeration=args.exaggeration,
        base_mm=args.base,
        padding=args.padding,
        square=args.square,
        route_style=args.route,
        route_width_mm=args.route_width,
        route_height_mm=args.route_height,
        separate_route=args.separate_route,
    )


def _build_and_save(points, name: str, args) -> None:
    print(f"Building model from {len(points)} GPS points (downloading elevation tiles)...")
    result = build_and_export(points, name, _params(args), Path(args.out))
    info = result["info"]
    w, h, t = info["size_mm"]
    print(f"Model size: {w} x {h} mm, max height {t} mm  (horizontal scale {info['scale']})")
    lo, hi = info["elevation_range_m"]
    print(f"Elevation: {lo} m - {hi} m, vertical exaggeration x{args.exaggeration}")
    print(f"Triangles: {info['triangles']:,}")
    for path in result["files"].values():
        print(f"  wrote {path}")


def cmd_build(args) -> None:
    client = _client()
    activity = client.activity(args.activity_id)
    points = client.activity_latlng(args.activity_id)
    date = activity.get("start_date_local", "")[:10]
    name = args.name or f"{date}-{slug(activity.get('name', str(args.activity_id)))}"
    print(f"Activity: {activity.get('name')} ({date})")
    _build_and_save(points, name, args)


def cmd_latest(args) -> None:
    client = _client()
    for act in client.activities(per_page=30):
        if (act.get("map") or {}).get("summary_polyline"):
            args.activity_id = act["id"]
            return cmd_build(args)
    raise SystemExit("No recent activity with GPS data found.")


def cmd_gpx(args) -> None:
    points = read_gpx(args.file)
    name = args.name or Path(args.file).stem
    _build_and_save(points, name, args)


def cmd_ui(args) -> None:
    from .web import serve

    serve(port=args.port, out_dir=args.out, open_browser=not args.no_browser)


def _add_model_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("model options")
    g.add_argument("--size", type=float, default=150, help="longest side in mm (default 150)")
    g.add_argument("--resolution", type=float, default=0.5, help="mesh cell size in mm (default 0.5)")
    g.add_argument("--exaggeration", type=float, default=1.5, help="vertical exaggeration (default 1.5)")
    g.add_argument("--base", type=float, default=3, help="base thickness in mm (default 3)")
    g.add_argument("--padding", type=float, default=0.1, help="terrain margin around route (fraction, default 0.1)")
    g.add_argument("--square", action="store_true", help="make the model square")
    g.add_argument("--route", choices=ROUTE_STYLES, default="raised", help="how to show the route")
    g.add_argument("--route-width", type=float, default=1.6, help="route width in mm (default 1.6)")
    g.add_argument("--route-height", type=float, default=1.0, help="route height/depth in mm (default 1.0)")
    g.add_argument(
        "--separate-route",
        action="store_true",
        help="export the route as a separate STL for multi-colour printing",
    )
    g.add_argument("--name", help="output file name prefix")
    g.add_argument("-o", "--out", default="output", help="output directory (default ./output)")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="strava3d", description="Turn Strava activities into 3D-printable terrain models."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="connect your Strava account (OAuth)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-browser", action="store_true", help="just print the URL")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("activities", help="list your recent activities")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--page", type=int, default=1)
    p.set_defaults(func=cmd_activities)

    p = sub.add_parser("build", help="build a model from a Strava activity ID")
    p.add_argument("activity_id", type=int)
    _add_model_args(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("latest", help="build a model from your most recent GPS activity")
    _add_model_args(p)
    p.set_defaults(func=cmd_latest)

    p = sub.add_parser("gpx", help="build a model from a GPX file (no Strava needed)")
    p.add_argument("file")
    _add_model_args(p)
    p.set_defaults(func=cmd_gpx)

    p = sub.add_parser("ui", help="open the web interface in your browser")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("-o", "--out", default="output", help="output directory (default ./output)")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser")
    p.set_defaults(func=cmd_ui)

    args = parser.parse_args(argv)
    started = datetime.now()
    args.func(args)
    if args.command in ("build", "latest", "gpx"):
        print(f"Done in {(datetime.now() - started).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
