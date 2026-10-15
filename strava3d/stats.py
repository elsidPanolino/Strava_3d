"""Activity statistics (from Strava or a GPX file) and their formatting."""

from __future__ import annotations

from datetime import datetime

import numpy as np

from .geo import EARTH_RADIUS_M, track_length_km

RIDE_TYPES = {
    "Ride", "MountainBikeRide", "GravelRide", "EBikeRide", "EMountainBikeRide", "Velomobile", "Handcycle",
}
# Name-plate fields in display order.
LABEL_FIELDS = ("distance", "elevation", "time", "speed", "date")
DEFAULT_LABEL_FIELDS = ("distance", "elevation", "time", "date")


def fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    h, m = divmod(int(round(seconds / 60)), 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso[:10])
        return f"{d.day} {d:%b %Y}"
    except ValueError:
        return iso[:10]


def strava_stats(a: dict) -> dict:
    """Normalise a Strava activity (summary or detailed) into our stats dict."""
    moving = a.get("moving_time") or 0
    dist_km = (a.get("distance") or 0) / 1000
    speed = (a.get("average_speed") or 0) * 3.6 or (dist_km / (moving / 3600) if moving else 0)
    out = {
        "sport": a.get("sport_type") or a.get("type") or "",
        "distance_km": round(dist_km, 2),
        "elevation_gain_m": round(a.get("total_elevation_gain") or 0),
        "moving_time_s": moving,
        "elapsed_time_s": a.get("elapsed_time") or 0,
        "avg_speed_kmh": round(speed, 1),
        "max_speed_kmh": round((a.get("max_speed") or 0) * 3.6, 1),
        "elev_high_m": a.get("elev_high"),
        "elev_low_m": a.get("elev_low"),
        "avg_watts": a.get("average_watts"),
        "weighted_watts": a.get("weighted_average_watts"),
        "kilojoules": a.get("kilojoules"),
        "avg_hr": a.get("average_heartrate"),
        "max_hr": a.get("max_heartrate"),
        "avg_cadence": a.get("average_cadence"),
        "calories": a.get("calories"),
        "kudos": a.get("kudos_count"),
        "achievements": a.get("achievement_count"),
        "prs": a.get("pr_count"),
        "date": (a.get("start_date_local") or "")[:10],
        "device": a.get("device_name"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", 0, 0.0)}


def gpx_stats(track: dict) -> dict:
    """Stats computed from GPX points, <ele> and <time> (as exported by Strava)."""
    pts = track["points"]
    out = {"distance_km": round(track_length_km(pts), 2)}
    ele = np.array([e for e in track.get("ele", []) if e is not None], dtype=float)
    if len(ele) > 2:
        gain, ref = 0.0, ele[0]
        for e in ele[1:]:  # 3 m hysteresis filters GPS/barometer noise
            if e > ref + 3:
                gain += e - ref
                ref = e
            elif e < ref - 3:
                ref = e
        out.update(elevation_gain_m=round(gain), elev_high_m=round(float(ele.max())), elev_low_m=round(float(ele.min())))
    times = track.get("times") or []
    if len(times) == len(pts) and all(times):
        moving = _moving_seconds(pts, times)
        out["moving_time_s"] = moving
        out["elapsed_time_s"] = int((times[-1] - times[0]).total_seconds())
        if moving:
            out["avg_speed_kmh"] = round(out["distance_km"] / (moving / 3600), 1)
        out["date"] = times[0].date().isoformat()
    return out


def _moving_seconds(points, times, min_speed_ms: float = 1.0) -> int:
    """Sum of time between points while moving faster than ~3.6 km/h (Strava-like)."""
    p = np.radians(np.asarray(points, dtype=float))
    a = (np.sin(np.diff(p[:, 0]) / 2) ** 2
         + np.cos(p[:-1, 0]) * np.cos(p[1:, 0]) * np.sin(np.diff(p[:, 1]) / 2) ** 2)
    dist = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))
    dt = np.array([(b - a_).total_seconds() for a_, b in zip(times[:-1], times[1:])])
    ok = (dt > 0) & (dist / np.maximum(dt, 1e-9) >= min_speed_ms)
    return int(dt[ok].sum())


def label_lines(title: str, stats: dict, fields=DEFAULT_LABEL_FIELDS) -> list[str]:
    """Name plate lines: title, then the chosen stats (split over two lines if long)."""
    parts = []
    for f in LABEL_FIELDS:
        if f not in fields:
            continue
        if f == "distance" and stats.get("distance_km"):
            parts.append(f"{stats['distance_km']:.1f} km")
        elif f == "elevation" and stats.get("elevation_gain_m"):
            parts.append(f"{stats['elevation_gain_m']:,.0f} m elevation")
        elif f == "time" and stats.get("moving_time_s"):
            parts.append(fmt_duration(stats["moving_time_s"]))
        elif f == "speed" and stats.get("avg_speed_kmh"):
            parts.append(f"{stats['avg_speed_kmh']:.1f} km/h")
        elif f == "date" and stats.get("date"):
            parts.append(fmt_date(stats["date"]))
    sep = "  ·  "
    if len(parts) > 3:
        return [title, sep.join(parts[:3]), sep.join(parts[3:])]
    return [title, sep.join(parts)] if parts else [title]


def format_stats(stats: dict) -> str:
    """One-line summary for the terminal."""
    bits = [f"{stats.get('distance_km', 0):.1f} km"]
    if stats.get("elevation_gain_m"):
        bits.append(f"{stats['elevation_gain_m']:,} m elevation")
    if stats.get("moving_time_s"):
        bits.append(fmt_duration(stats["moving_time_s"]))
    if stats.get("avg_speed_kmh"):
        bits.append(f"{stats['avg_speed_kmh']} km/h avg")
    if stats.get("avg_watts"):
        bits.append(f"{stats['avg_watts']:.0f} W")
    if stats.get("avg_hr"):
        bits.append(f"{stats['avg_hr']:.0f} bpm")
    return " · ".join(bits)
