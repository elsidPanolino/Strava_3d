"""Command line interface: `strava3d auth | activities | build | gpx`."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .geo import read_gpx_track, slug
from .guide import print_guide
from .model import ROUTE_STYLES, SEA_STYLES, SHAPES, ModelParams, build_and_export
from .stats import DEFAULT_LABEL_FIELDS, LABEL_FIELDS, RIDE_TYPES, fmt_duration, format_stats, gpx_stats, label_lines, strava_stats
from .printers import DEFAULT_PRINTER, PRINTERS
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
    if args.rides:
        acts = [a for a in acts if (a.get("sport_type") or a.get("type")) in RIDE_TYPES]
    print(f"{'ID':>12}  {'Date':10}  {'Type':12} {'km':>7} {'Elev m':>7} {'Time':>8} {'km/h':>6}  Name")
    for a in acts:
        date = a.get("start_date_local", "")[:10]
        km = a.get("distance", 0) / 1000
        gps = "" if (a.get("map") or {}).get("summary_polyline") else "  (no GPS)"
        print(
            f"{a['id']:>12}  {date:10}  {a.get('sport_type', a.get('type', '')):12} "
            f"{km:7.1f} {a.get('total_elevation_gain', 0):7.0f} {fmt_duration(a.get('moving_time')):>8} "
            f"{(a.get('average_speed') or 0) * 3.6:6.1f}  {a.get('name', '')}{gps}"
        )


def _params(args, plate=None) -> ModelParams:
    shape = "square" if args.square and args.shape == "rectangle" else args.shape
    return ModelParams(
        size_mm=args.size,
        cell_mm=args.resolution,
        z_exaggeration=None if args.exaggeration == "auto" else float(args.exaggeration),
        water=args.water,
        sea_color={"auto": None, "on": True, "off": False}[args.sea_color],
        coast_step_mm=args.coast_step,
        sea_style=args.sea_style,
        sea_depth_mm=args.sea_depth,
        wave_mm=args.wave_height,
        shallow_color=args.shallow_color,
        base_mm=args.base,
        padding=args.padding,
        shape=shape,
        route_style=args.route,
        route_width_mm=args.route_width,
        route_height_mm=args.route_height,
        inlay_clearance_mm=args.inlay_clearance,
        separate_route=args.separate_route,
        printer=args.printer,
        layer_mm=args.layer,
        label=args.label,
        label_lines=[args.label_text, *plate[1:]] if args.label_text and plate else (plate or []),
        colors=args.colors,
        base_color=args.base_color,
        single_color=args.single_color,
    )


def _build_and_save(points, name: str, args, plate) -> None:
    print(f"Building model from {len(points)} GPS points (downloading elevation tiles)...")
    params = _params(args, plate)
    result = build_and_export(points, name, params, Path(args.out))
    info = result["info"]
    w, h, t = info["size_mm"]
    print(f"Printer: {info['printer']['name']}, {info['layer_mm']} mm layers")
    print(f"Model size: {w} x {h} mm, max height {t} mm  (horizontal scale {info['scale']})")
    lo, hi = info["elevation_range_m"]
    sea = f", sea {info['sea_fraction']:.0%} (flattened)" if info["water"] else ""
    print(f"Elevation: {lo} m - {hi} m, vertical exaggeration x{info['exaggeration']}{sea}")
    print(f"Triangles: {info['triangles']:,}   Filament: ~{info['filament_g']} g PLA (15% infill)")
    for w_ in info["warnings"]:
        print(f"  ! {w_}")
    for path in result["files"].values():
        print(f"  wrote {path}")
    print("\nHow to print:")
    for step in print_guide(info):
        print(f"  - {step}")


def cmd_build(args) -> None:
    client = _client()
    activity = client.activity(args.activity_id)
    points = client.activity_latlng(args.activity_id)
    date = activity.get("start_date_local", "")[:10]
    title = activity.get("name", str(args.activity_id))
    name = args.name or f"{date}-{slug(title)}"
    stats = strava_stats(activity)
    print(f"Activity: {title} ({date})")
    print("  " + format_stats(stats))
    _build_and_save(points, name, args, label_lines(title, stats, args.label_stats.split(",")))


def cmd_latest(args) -> None:
    client = _client()
    for act in client.activities(per_page=30):
        if (act.get("map") or {}).get("summary_polyline"):
            args.activity_id = act["id"]
            return cmd_build(args)
    raise SystemExit("No recent activity with GPS data found.")


def cmd_gpx(args) -> None:
    track = read_gpx_track(args.file)
    title = track["name"] or Path(args.file).stem
    name = args.name or slug(Path(args.file).stem)
    stats = gpx_stats(track)
    print(f"Track: {title}")
    print("  " + format_stats(stats))
    _build_and_save(track["points"], name, args, label_lines(title, stats, args.label_stats.split(",")))


def cmd_ui(args) -> None:
    from .web import serve

    serve(port=args.port, out_dir=args.out, open_browser=not args.no_browser)


def cmd_stats(args) -> None:
    s = _client().athlete_stats()
    kind = {"ride": "ride", "run": "run", "swim": "swim"}[args.sport]
    print(f"{'':14} {'Count':>6} {'Distance':>11} {'Elevation':>10} {'Moving time':>12}")
    for label, key in (("Last 4 weeks", "recent"), ("This year", "ytd"), ("All time", "all")):
        t = s.get(f"{key}_{kind}_totals") or {}
        print(f"{label:14} {t.get('count', 0):6} {t.get('distance', 0) / 1000:9,.0f} km "
              f"{t.get('elevation_gain', 0):8,.0f} m {fmt_duration(t.get('moving_time')):>12}")
    if kind == "ride":
        print(f"Longest ride: {(s.get('biggest_ride_distance') or 0) / 1000:,.1f} km, "
              f"biggest climb: {s.get('biggest_climb_elevation_gain') or 0:,.0f} m")


def cmd_printers(args) -> None:
    for p in PRINTERS.values():
        default = "  (default)" if p.key == DEFAULT_PRINTER else ""
        ams = f", {p.ams}" if p.ams else ""
        print(f"{p.key:9} {p.name:28} {p.bed_x:.0f} x {p.bed_y:.0f} x {p.bed_z:.0f} mm{ams}{default}")


def _add_model_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("printer")
    g.add_argument("--printer", choices=list(PRINTERS), default=DEFAULT_PRINTER,
                   help="printer profile (default: Bambu Lab A1 mini); see `strava3d printers`")
    g.add_argument("--layer", type=float, help="layer height in mm (default: printer profile, 0.2)")
    g = p.add_argument_group("model options")
    g.add_argument("--size", type=float, help="longest side in mm (default: largest that fits the printer, max 200)")
    g.add_argument("--shape", choices=SHAPES, default="rectangle", help="outline of the model")
    g.add_argument("--resolution", type=float, default=0.5, help="mesh cell size in mm (default 0.5)")
    g.add_argument("--exaggeration", default="auto",
                   help="vertical exaggeration, or 'auto' to size the relief to the model (default auto)")
    g.add_argument("--water", choices=["auto", "on", "off"], default="auto",
                   help="flatten the sea and lift the land; auto = when the area has sea (default)")
    g.add_argument("--sea-color", choices=["auto", "on", "off"], default="auto",
                   help="print the sea in its own colour (one filament change at the coast); default auto = when there is sea")
    g.add_argument("--coast-step", type=float, default=0.4,
                   help="height of the land edge above the sea in mm (default 0.4)")
    g.add_argument("--sea-style", choices=SEA_STYLES, default="flat",
                   help="flat sea, waves (ripple texture) or depth (reefs high, deep water low)")
    g.add_argument("--sea-depth", type=float, default=1.6,
                   help="depth style: how far the deepest water sits below the sea surface, in mm (default 1.6)")
    g.add_argument("--wave-height", type=float, default=0.4, help="waves style: texture height in mm (default 0.4)")
    g.add_argument("--shallow-color", action="store_true",
                   help="depth style: shallow water (reefs, lagoons) in its own colour")
    g.add_argument("--base", type=float, default=3, help="base thickness in mm (default 3)")
    g.add_argument("--padding", type=float, default=0.1, help="terrain margin around route (fraction, default 0.1)")
    g.add_argument("--square", action="store_true", help="same as --shape square")
    g.add_argument("--route", choices=ROUTE_STYLES, default="raised", help="how to show the route")
    g.add_argument("--route-width", type=float, default=1.6, help="route width in mm (default 1.6)")
    g.add_argument("--route-height", type=float, default=1.0, help="route height/depth in mm (default 1.0)")
    g.add_argument("--inlay-clearance", type=float, default=0.15,
                   help="--route inlay: gap per side between route piece and slot in mm (default 0.15)")
    g.add_argument(
        "--separate-route",
        action="store_true",
        help="route as its own part (next free filament in the 3MF) for AMS / multi-colour printing",
    )
    g.add_argument("--label", action="store_true", help="add a name plate with the activity name and stats")
    g.add_argument("--label-text", help="custom name plate title (default: activity name)")
    g.add_argument("--label-stats", default=",".join(DEFAULT_LABEL_FIELDS),
                   help=f"stats on the name plate, comma separated from: {','.join(LABEL_FIELDS)}")
    g.add_argument("--colors", type=int, choices=[1, 2, 3, 4], default=1,
                   help="colour bands by height, using filament changes (default 1)")
    g.add_argument("--single-color", action="store_true",
                   help="print in one colour: no colour bands, sea/base colour or separate route")
    g.add_argument("--base-color", action="store_true",
                   help="give the base its own colour (one more filament change)")
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
    p.add_argument("--rides", action="store_true", help="only show rides")
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

    p = sub.add_parser("stats", help="your Strava totals (rides by default)")
    p.add_argument("--sport", choices=["ride", "run", "swim"], default="ride")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("printers", help="list printer profiles")
    p.set_defaults(func=cmd_printers)

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
