"""Load exported Strava run JSON into tidy structures for the dashboard.

Reads every data/runs/<id>.json produced by export_runs.py and provides:
  * load_runs_df()   -> one row per run with derived metrics (pace, HR, etc.)
  * load_best_efforts_df() -> one row per (run, best-effort distance)
  * load_streams(id) -> the per-second stream dict for a single run
  * heatmap_points() -> sampled (lat, lon) across all runs for a density map

Distances are metres and times seconds in the raw data; we convert to km /
minutes and compute pace (min per km) for display.
"""

import glob
import json
import os
from functools import lru_cache

import numpy as np
import pandas as pd

RUNS_DIR = os.environ.get(
    "STRAVA_RUNS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runs"),
)

# Max HR used to derive %-of-max HR zones. Override via STRAVA_HR_MAX env var.
HR_MAX = int(os.environ.get("STRAVA_HR_MAX", "190"))
# Reference HR for the "pace at a fixed HR" fitness trend. Override via env.
FITNESS_HR = int(os.environ.get("STRAVA_FITNESS_HR", "150"))
# Zone lower bounds as fraction of HR_MAX (Z1..Z5). Standard 5-zone model.
HR_ZONE_BOUNDS = [0.0, 0.60, 0.70, 0.80, 0.90, 1.01]
HR_ZONE_NAMES = ["Z1 Recovery", "Z2 Endurance", "Z3 Tempo", "Z4 Threshold", "Z5 VO2max"]


def _run_files():
    return sorted(glob.glob(os.path.join(RUNS_DIR, "*.json")))


@lru_cache(maxsize=1)
def _load_all():
    """Read and cache the raw JSON docs (list of {summary, detail, streams})."""
    docs = []
    for path in _run_files():
        try:
            with open(path, encoding="utf-8") as f:
                docs.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return docs


def _pace_min_per_km(distance_m, moving_s):
    if not distance_m or not moving_s:
        return np.nan
    return (moving_s / 60.0) / (distance_m / 1000.0)


def load_runs_df():
    """One row per run with the metrics the dashboard charts against."""
    rows = []
    for doc in _load_all():
        s = doc.get("summary", {})
        d = doc.get("detail", {})
        dist_m = s.get("distance") or 0.0
        moving_s = s.get("moving_time") or 0.0
        start = pd.to_datetime(s.get("start_date_local") or s.get("start_date"),
                               errors="coerce")
        latlng = s.get("start_latlng") or [None, None]
        rows.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "start": start,
            "date": start.date() if pd.notna(start) else None,
            "type": s.get("type"),
            "distance_km": dist_m / 1000.0,
            "moving_min": moving_s / 60.0,
            "elapsed_min": (s.get("elapsed_time") or 0) / 60.0,
            "pace_min_km": _pace_min_per_km(dist_m, moving_s),
            "avg_speed_kmh": (s.get("average_speed") or 0) * 3.6,
            "elev_gain_m": s.get("total_elevation_gain"),
            "avg_hr": s.get("average_heartrate"),
            "max_hr": s.get("max_heartrate"),
            "avg_cadence": (s.get("average_cadence") or np.nan) * 2,  # spm (both feet)
            "kudos": s.get("kudos_count"),
            "gear": (d.get("gear") or {}).get("name"),
            "start_lat": latlng[0],
            "start_lng": latlng[1],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("start").reset_index(drop=True)
    df["year"] = df["start"].dt.year
    df["week"] = df["start"].dt.to_period("W").dt.start_time
    df["month"] = df["start"].dt.to_period("M").dt.start_time
    df["dow"] = df["start"].dt.day_name()
    df["hour"] = df["start"].dt.hour
    return df


def load_best_efforts_df():
    """One row per (run, best-effort) for PR / progression charts.

    Strava's detail.best_efforts lists standard distances (400m, 1k, 1 mile,
    5k, 10k, ...) with the fastest elapsed_time achieved during that run.
    """
    rows = []
    for doc in _load_all():
        s = doc.get("summary", {})
        start = pd.to_datetime(s.get("start_date_local") or s.get("start_date"),
                               errors="coerce")
        for be in (doc.get("detail", {}).get("best_efforts") or []):
            elapsed = be.get("elapsed_time")
            dist = be.get("distance")
            if not elapsed or not dist:
                continue
            rows.append({
                "run_id": s.get("id"),
                "start": start,
                "effort": be.get("name"),
                "distance_m": dist,
                "elapsed_s": elapsed,
                "pace_min_km": _pace_min_per_km(dist, elapsed),
            })
    return pd.DataFrame(rows)


def load_splits_df(run_id):
    """Per-km splits for one run (from detail.splits_metric)."""
    for doc in _load_all():
        if doc.get("summary", {}).get("id") == run_id:
            rows = []
            for sp in (doc.get("detail", {}).get("splits_metric") or []):
                dist = sp.get("distance")
                moving = sp.get("moving_time")
                rows.append({
                    "split": sp.get("split"),
                    "distance_km": (dist or 0) / 1000.0,
                    "pace_min_km": _pace_min_per_km(dist, moving),
                    "elev_diff_m": sp.get("elevation_difference"),
                    "avg_hr": sp.get("average_heartrate"),
                })
            return pd.DataFrame(rows)
    return pd.DataFrame()


def load_streams(run_id):
    """Return the key_by_type streams dict for one run, or {}."""
    for doc in _load_all():
        if doc.get("summary", {}).get("id") == run_id:
            return doc.get("streams") or {}
    return {}


def stream_series(streams, key):
    """Extract a stream's data list, or None if absent."""
    node = streams.get(key)
    if isinstance(node, dict):
        return node.get("data")
    return None


def hr_zone_distribution():
    """Aggregate seconds spent in each HR zone across all runs with HR data.

    Uses the per-second heartrate + time streams so it reflects real time in
    zone, not just average HR.
    """
    seconds = np.zeros(len(HR_ZONE_NAMES))
    bounds = [b * HR_MAX for b in HR_ZONE_BOUNDS]
    for doc in _load_all():
        streams = doc.get("streams") or {}
        hr = stream_series(streams, "heartrate")
        t = stream_series(streams, "time")
        if not hr:
            continue
        hr = np.asarray(hr, dtype=float)
        # dt between samples; assume 1s if no time stream.
        if t and len(t) == len(hr):
            dt = np.diff(np.asarray(t, dtype=float), prepend=t[0])
            dt[dt <= 0] = 1.0
        else:
            dt = np.ones_like(hr)
        idx = np.clip(np.digitize(hr, bounds[1:-1]), 0, len(HR_ZONE_NAMES) - 1)
        for z in range(len(HR_ZONE_NAMES)):
            seconds[z] += dt[idx == z].sum()
    return pd.DataFrame({
        "zone": HR_ZONE_NAMES,
        "minutes": seconds / 60.0,
    })


def heatmap_points(max_points=40000, stride=3):
    """Sampled (lat, lon) points across all runs for a density heatmap.

    Takes every `stride`-th GPS point per run and caps the total at
    `max_points` to keep the figure responsive.
    """
    lats, lons = [], []
    for doc in _load_all():
        latlng = stream_series(doc.get("streams") or {}, "latlng")
        if not latlng:
            continue
        for p in latlng[::stride]:
            if p and p[0] is not None:
                lats.append(p[0])
                lons.append(p[1])
    if len(lats) > max_points:
        keep = np.linspace(0, len(lats) - 1, max_points).astype(int)
        lats = [lats[i] for i in keep]
        lons = [lons[i] for i in keep]
    return pd.DataFrame({"lat": lats, "lon": lons})


def run_track(run_id):
    """Return a DataFrame of the single-run track: lat, lon, distance, pace, hr."""
    streams = load_streams(run_id)
    latlng = stream_series(streams, "latlng")
    if not latlng:
        return pd.DataFrame()
    dist = stream_series(streams, "distance") or [np.nan] * len(latlng)
    vel = stream_series(streams, "velocity_smooth")
    alt = stream_series(streams, "altitude") or [np.nan] * len(latlng)
    hr = stream_series(streams, "heartrate") or [np.nan] * len(latlng)
    df = pd.DataFrame({
        "lat": [p[0] for p in latlng],
        "lon": [p[1] for p in latlng],
        "distance_km": np.asarray(dist, dtype=float) / 1000.0,
        "altitude_m": np.asarray(alt, dtype=float),
        "hr": np.asarray(hr, dtype=float),
    })
    if vel:
        v = np.asarray(vel, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            df["pace_min_km"] = np.where(v > 0.1, (1000.0 / v) / 60.0, np.nan)
    else:
        df["pace_min_km"] = np.nan
    return df


# ===========================================================================
# Advanced analytics (pure functions over numpy arrays -> unit-testable).
# ===========================================================================
def minetti_cost_factor(grade):
    """Metabolic cost of running at `grade` relative to flat ground.

    `grade` is rise/run as a fraction (0.10 == 10% uphill, negative == down).
    Uses Minetti et al. (2002) energy-cost polynomial, normalised so flat == 1.
    Returns a multiplier >= ~0.5; running uphill costs more than 1, gentle
    downhills less, steep downhills more again (braking).
    """
    g = np.clip(np.asarray(grade, dtype=float), -0.45, 0.45)
    c = (155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3
         + 46.3 * g**2 + 19.5 * g + 3.6)
    return c / 3.6  # 3.6 J/kg/m is the flat-ground cost


def grade_adjusted_pace(distance_m, altitude_m, moving_time_s) -> float:
    """Grade-adjusted pace (min/km): the flat-equivalent pace for the effort.

    Given per-point cumulative distance, altitude and (optionally) elapsed
    time, weights each segment by its Minetti cost so that climbing makes the
    equivalent flat pace *faster* than the raw pace. Returns np.nan if inputs
    are too sparse.
    """
    d = np.asarray(distance_m, dtype=float)
    a = np.asarray(altitude_m, dtype=float)
    if d.size < 2 or a.size != d.size:
        return np.nan
    dd = np.diff(d)
    dz = np.diff(a)
    valid = dd > 0.1
    if not valid.any():
        return np.nan
    grade = np.zeros_like(dd)
    grade[valid] = dz[valid] / dd[valid]
    # Equivalent flat distance = actual distance * cost factor.
    equiv_flat_m = np.sum(dd[valid] * minetti_cost_factor(grade[valid]))
    total_min = moving_time_s / 60.0
    if equiv_flat_m <= 0 or total_min <= 0:
        return np.nan
    return float(total_min / (equiv_flat_m / 1000.0))


def aerobic_decoupling(velocity, heartrate, time=None) -> float:
    """Aerobic decoupling (%) between the first and second half of a run.

    Efficiency = mean(speed) / mean(HR). Decoupling = how much efficiency
    fell from the first half to the second (positive = cardiac drift /
    fatigue; near 0 = well-paced aerobic effort). Splits by time if a time
    stream is given, else by sample count.
    """
    v = np.asarray(velocity, dtype=float)
    hr = np.asarray(heartrate, dtype=float)
    n = min(v.size, hr.size)
    if n < 10:
        return np.nan
    v, hr = v[:n], hr[:n]
    if time is not None and len(time) >= n:
        t = np.asarray(time[:n], dtype=float)
        mid = t[0] + (t[-1] - t[0]) / 2.0
        first = t <= mid
    else:
        first = np.arange(n) < n // 2
    second = ~first

    def eff(mask):
        vm = np.nanmean(v[mask])
        hm = np.nanmean(hr[mask])
        return vm / hm if hm > 0 else np.nan

    e1, e2 = eff(first), eff(second)
    if not np.isfinite(e1) or not np.isfinite(e2) or e1 == 0:
        return np.nan
    return float((e1 - e2) / e1 * 100.0)


def pace_at_hr(velocity, heartrate, target_hr, tol=4) -> float:
    """Median pace (min/km) at a target heart rate +/- tol bpm within a run.

    Isolates the samples where HR sat near `target_hr` and reports the median
    pace there. Tracking this across runs over time surfaces aerobic fitness:
    a faster pace at the same HR means you're getting fitter.
    """
    v = np.asarray(velocity, dtype=float)
    hr = np.asarray(heartrate, dtype=float)
    n = min(v.size, hr.size)
    if n == 0:
        return np.nan
    v, hr = v[:n], hr[:n]
    mask = (np.abs(hr - target_hr) <= tol) & (v > 0.5)
    if mask.sum() < 5:
        return np.nan
    median_speed = np.nanmedian(v[mask])
    if median_speed <= 0:
        return np.nan
    return float((1000.0 / median_speed) / 60.0)


def advanced_metrics_df(target_hr=None):
    """One row per run with grade-adjusted pace, decoupling and pace-at-HR.

    Computed from each run's streams in a single pass. Rows with insufficient
    stream data get NaN for the affected metric.
    """
    if target_hr is None:
        target_hr = FITNESS_HR
    rows = []
    for doc in _load_all():
        s = doc.get("summary", {})
        streams = doc.get("streams") or {}
        start = pd.to_datetime(s.get("start_date_local") or s.get("start_date"),
                               errors="coerce")
        dist = stream_series(streams, "distance")
        alt = stream_series(streams, "altitude")
        vel = stream_series(streams, "velocity_smooth")
        hr = stream_series(streams, "heartrate")
        t = stream_series(streams, "time")
        moving_s = s.get("moving_time") or 0
        dist_m = s.get("distance") or 0.0

        gap = (grade_adjusted_pace(dist, alt, moving_s)
               if dist and alt else np.nan)
        decouple = (aerobic_decoupling(vel, hr, t)
                    if vel and hr else np.nan)
        at_hr = pace_at_hr(vel, hr, target_hr) if vel and hr else np.nan

        rows.append({
            "id": s.get("id"),
            "start": start,
            "actual_pace_min_km": _pace_min_per_km(dist_m, moving_s),
            "gap_min_km": gap,
            "decoupling_pct": decouple,
            "pace_at_hr_min_km": at_hr,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("start").reset_index(drop=True)
    return df
