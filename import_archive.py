"""Import a Strava bulk-export archive into data/runs/*.json.

This is the FREE alternative to the API (which now needs a Strava subscription):
request your archive at Settings -> My Account -> "Download or Delete Your
Account" -> Request your archive. You'll get a ZIP by email.

Usage:
    python import_archive.py path/to/export_12345.zip
    python import_archive.py path/to/unzipped_folder/
    python import_archive.py export.zip --type all      # not just runs

It parses activities.csv + the per-activity track files (GPX / TCX / FIT, incl.
.gz) and writes the SAME {summary, detail, streams} schema that export_runs.py
produces, so app.py / data_loader.py work unchanged. Distance, pace, per-km
splits and best-efforts are computed from each track.

FIT files require the optional `fitparse` package:  pip install fitparse
"""

import argparse
import csv
import gzip
import io
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runs")

# Standard distances (metres) for computed best-efforts, matching the API names.
BEST_EFFORT_DISTANCES = [
    ("400m", 400),
    ("1k", 1000),
    ("1 mile", 1609),
    ("5k", 5000),
    ("10k", 10000),
    ("Half-Marathon", 21097),
    ("Marathon", 42195),
]


# ---------------------------------------------------------------------------
# Archive access -- works on either a .zip or an already-extracted folder.
# ---------------------------------------------------------------------------
class Archive:
    def __init__(self, path):
        self.path = path
        self.zip = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else None
        if self.zip is None and not os.path.isdir(path):
            sys.exit(f"Not a zip or directory: {path}")

    def read(self, rel):
        rel = rel.replace("\\", "/").lstrip("/")
        if self.zip is not None:
            try:
                return self.zip.read(rel)
            except KeyError:
                # Some archives nest under a top folder; try a suffix match.
                for name in self.zip.namelist():
                    if name.replace("\\", "/").endswith(rel):
                        return self.zip.read(name)
                return None
        full = os.path.join(self.path, rel)
        if os.path.exists(full):
            with open(full, "rb") as f:
                return f.read()
        return None

    def read_csv_text(self, rel):
        raw = self.read(rel)
        return raw.decode("utf-8-sig") if raw else None


def maybe_gunzip(name, data):
    if name.lower().endswith(".gz") and data:
        return gzip.decompress(data)
    return data


# ---------------------------------------------------------------------------
# Track parsers -> a common dict of parallel lists.
# Keys: lat, lon, ele, t (epoch seconds), hr, cad  (missing series -> None list)
# ---------------------------------------------------------------------------
def _iso_to_epoch(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _lstrip_xml(data):
    """Drop leading whitespace/BOM before the XML declaration. Some exports
    prefix TCX/GPX with spaces, which ElementTree rejects ("XML declaration
    not at start of entity")."""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).lstrip(b"\xef\xbb\xbf \t\r\n")
    return data.lstrip("﻿ \t\r\n")


def parse_gpx(data):
    root = ET.fromstring(_lstrip_xml(data))
    ns = {
        "g": "http://www.topografix.com/GPX/1/1",
        "tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    }
    pts = {"lat": [], "lon": [], "ele": [], "t": [], "hr": [], "cad": []}
    for trkpt in root.iter("{http://www.topografix.com/GPX/1/1}trkpt"):
        # HR-only devices (e.g. wrist bands on a treadmill) emit trkpts with
        # no lat/lon -- keep the point (for time/HR), just without a position.
        lat_a, lon_a = trkpt.get("lat"), trkpt.get("lon")
        pts["lat"].append(float(lat_a) if lat_a is not None else None)
        pts["lon"].append(float(lon_a) if lon_a is not None else None)
        ele = trkpt.find("g:ele", ns)
        pts["ele"].append(float(ele.text) if ele is not None else None)
        t = trkpt.find("g:time", ns)
        pts["t"].append(_iso_to_epoch(t.text) if t is not None else None)
        hr = trkpt.find(".//tpx:hr", ns)
        pts["hr"].append(int(hr.text) if hr is not None else None)
        cad = trkpt.find(".//tpx:cad", ns)
        pts["cad"].append(int(cad.text) if cad is not None else None)
    return pts


def parse_tcx(data):
    root = ET.fromstring(_lstrip_xml(data))
    tc = "{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}"
    ax = "{http://www.garmin.com/xmlschemas/ActivityExtension/v2}"
    pts = {"lat": [], "lon": [], "ele": [], "t": [], "hr": [], "cad": [], "dist": []}
    for tp in root.iter(tc + "Trackpoint"):
        pos = tp.find(tc + "Position")
        if pos is not None:
            pts["lat"].append(float(pos.findtext(tc + "LatitudeDegrees")))
            pts["lon"].append(float(pos.findtext(tc + "LongitudeDegrees")))
        else:
            pts["lat"].append(None)
            pts["lon"].append(None)
        alt = tp.findtext(tc + "AltitudeMeters")
        pts["ele"].append(float(alt) if alt else None)
        t = tp.findtext(tc + "Time")
        pts["t"].append(_iso_to_epoch(t))
        hr = tp.find(tc + "HeartRateBpm")
        pts["hr"].append(int(hr.findtext(tc + "Value")) if hr is not None else None)
        d = tp.findtext(tc + "DistanceMeters")
        pts["dist"].append(float(d) if d else None)
        cad = tp.findtext(tc + "Cadence")
        if cad is None:
            cad = tp.findtext(".//" + ax + "RunCadence")
        pts["cad"].append(int(cad) if cad else None)
    return pts


def parse_fit(data):
    try:
        from fitparse import FitFile
    except ImportError as err:
        raise RuntimeError(
            "FIT files found but 'fitparse' is not installed. Run:  pip install fitparse"
        ) from err
    fit = FitFile(io.BytesIO(data))
    pts = {"lat": [], "lon": [], "ele": [], "t": [], "hr": [], "cad": [], "dist": []}
    sc = 180.0 / 2**31  # semicircles -> degrees
    for rec in fit.get_messages("record"):
        v = {d.name: d.value for d in rec}
        lat = v.get("position_lat")
        lon = v.get("position_long")
        pts["lat"].append(lat * sc if lat is not None else None)
        pts["lon"].append(lon * sc if lon is not None else None)
        pts["ele"].append(v.get("enhanced_altitude", v.get("altitude")))
        ts = v.get("timestamp")
        pts["t"].append(ts.replace(tzinfo=UTC).timestamp() if ts else None)
        pts["hr"].append(v.get("heart_rate"))
        pts["cad"].append(v.get("cadence"))
        pts["dist"].append(v.get("distance"))
    return pts


def parse_track(filename, data):
    data = maybe_gunzip(filename, data)
    low = filename.lower()
    if low.endswith(".gz"):
        low = low[:-3]
    if low.endswith(".gpx"):
        return parse_gpx(data)
    if low.endswith(".tcx"):
        return parse_tcx(data)
    if low.endswith(".fit"):
        return parse_fit(data)
    return None


# ---------------------------------------------------------------------------
# Derive streams + summary metrics from parsed points.
# ---------------------------------------------------------------------------
def _haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_activity(pts, meta):
    n = len(pts["t"])
    if n < 2:
        return None
    t0 = next((x for x in pts["t"] if x is not None), None)
    if t0 is None:
        return None

    time_s, latlng, dist_cum, alt, hr, cad = [], [], [], [], [], []
    have_gps = any(x is not None for x in pts["lat"])
    dev_dist = pts.get("dist")

    cum = 0.0
    prev_ll = None
    for i in range(n):
        t = pts["t"][i]
        time_s.append(int(t - t0) if t is not None else (time_s[-1] if time_s else 0))
        lat, lon = pts["lat"][i], pts["lon"][i]
        # Keep latlng aligned 1:1 with the other streams (None where a point
        # has no GPS fix); consumers filter the gaps.
        if lat is not None and lon is not None:
            latlng.append([round(lat, 6), round(lon, 6)])
        else:
            latlng.append(None)
        # Distance: prefer device distance, else integrate GPS.
        if dev_dist and dev_dist[i] is not None:
            cum = float(dev_dist[i])
        elif lat is not None and prev_ll is not None:
            cum += _haversine(prev_ll[0], prev_ll[1], lat, lon)
        if lat is not None:
            prev_ll = (lat, lon)
        dist_cum.append(round(cum, 1))
        alt.append(pts["ele"][i])
        hr.append(pts["hr"][i])
        cad.append(pts["cad"][i])

    # Velocity (m/s) from distance/time diffs, lightly smoothed.
    vel = [0.0] * n
    for i in range(1, n):
        dt = time_s[i] - time_s[i - 1]
        vel[i] = (dist_cum[i] - dist_cum[i - 1]) / dt if dt > 0 else vel[i - 1]
    vel = _smooth(vel, 5)

    elapsed = time_s[-1]
    # Sum the actual time span of moving samples, not the point count. Many
    # devices record every few seconds, so counting points underestimates
    # moving time (and inflates pace) by the sampling interval.
    moving = sum(time_s[i] - time_s[i - 1] for i in range(1, n) if vel[i] > 0.5)
    distance = dist_cum[-1]
    hr_vals = [h for h in hr if h]
    cad_vals = [c for c in cad if c]
    ele_gain = sum(
        max(0, (alt[i] or 0) - (alt[i - 1] or 0))
        for i in range(1, n)
        if alt[i] is not None and alt[i - 1] is not None
    )

    streams = {
        "time": {"data": time_s},
        "distance": {"data": dist_cum},
        "velocity_smooth": {"data": [round(v, 2) for v in vel]},
    }
    if have_gps:
        streams["latlng"] = {"data": latlng}
    if any(a is not None for a in alt):
        streams["altitude"] = {"data": alt}
    if hr_vals:
        streams["heartrate"] = {"data": hr}
    if cad_vals:
        streams["cadence"] = {"data": cad}

    start_local = meta.get("start_local") or datetime.fromtimestamp(t0, UTC).isoformat()
    summary = {
        "id": meta["id"],
        "name": meta.get("name") or "Run",
        "type": meta.get("type") or "Run",
        "start_date": datetime.fromtimestamp(t0, UTC).isoformat(),
        "start_date_local": start_local,
        "distance": distance,
        "moving_time": moving,
        "elapsed_time": elapsed,
        "total_elevation_gain": round(ele_gain, 1),
        "average_speed": distance / moving if moving else 0.0,
        "max_speed": max(vel) if vel else 0.0,
        "average_heartrate": sum(hr_vals) / len(hr_vals) if hr_vals else None,
        "max_heartrate": max(hr_vals) if hr_vals else None,
        # data_loader multiplies by 2; device cadence is per-leg, so store raw mean.
        "average_cadence": (sum(cad_vals) / len(cad_vals)) if cad_vals else None,
        "kudos_count": None,
        "start_latlng": next((p for p in latlng if p is not None), [None, None]),
    }
    detail = {
        "id": meta["id"],
        "gear": {"name": meta.get("gear")} if meta.get("gear") else None,
        "description": meta.get("description", ""),
        "splits_metric": _splits(dist_cum, time_s, hr, alt),
        "best_efforts": _best_efforts(dist_cum, time_s, start_local),
    }
    return {"summary": summary, "detail": detail, "streams": streams}


def _smooth(xs, w):
    if w <= 1:
        return xs
    out, half = [], w // 2
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        window = xs[lo:hi]
        out.append(sum(window) / len(window))
    return out


def _interp_time(dist_cum, time_s, target):
    """Time (s) at which cumulative distance first reaches `target`."""
    for i in range(1, len(dist_cum)):
        if dist_cum[i] >= target:
            d0, d1 = dist_cum[i - 1], dist_cum[i]
            if d1 == d0:
                return time_s[i]
            frac = (target - d0) / (d1 - d0)
            return time_s[i - 1] + frac * (time_s[i] - time_s[i - 1])
    return None


def _splits(dist_cum, time_s, hr, alt):
    total = dist_cum[-1]
    splits = []
    k = 1
    while k * 1000 <= total + 1:
        t_start = _interp_time(dist_cum, time_s, (k - 1) * 1000) or 0
        t_end = _interp_time(dist_cum, time_s, k * 1000)
        if t_end is None:
            break
        seg_hr = [
            hr[i] for i in range(len(hr)) if hr[i] and (k - 1) * 1000 <= dist_cum[i] < k * 1000
        ]
        a_start = _val_at(dist_cum, alt, (k - 1) * 1000)
        a_end = _val_at(dist_cum, alt, k * 1000)
        splits.append(
            {
                "split": k,
                "distance": 1000.0,
                "moving_time": int(t_end - t_start),
                "elapsed_time": int(t_end - t_start),
                "average_speed": 1000.0 / (t_end - t_start) if t_end > t_start else 0,
                "elevation_difference": (
                    round(a_end - a_start, 1) if a_start is not None and a_end is not None else None
                ),
                "average_heartrate": sum(seg_hr) / len(seg_hr) if seg_hr else None,
            }
        )
        k += 1
    return splits


def _val_at(dist_cum, series, target):
    for i in range(len(dist_cum)):
        if dist_cum[i] >= target and series[i] is not None:
            return series[i]
    return None


def _best_efforts(dist_cum, time_s, start_local):
    """Fastest time to cover each standard distance (sliding window)."""
    efforts = []
    total = dist_cum[-1]
    n = len(dist_cum)
    for name, dm in BEST_EFFORT_DISTANCES:
        if total < dm:
            continue
        best = None
        j = 0  # left edge; advance to the tightest window still covering dm
        for i in range(n):
            while j + 1 <= i and dist_cum[i] - dist_cum[j + 1] >= dm:
                j += 1
            if dist_cum[i] - dist_cum[j] >= dm:
                dt = time_s[i] - time_s[j]
                if dt > 0 and (best is None or dt < best):
                    best = dt
        if best is not None:
            efforts.append(
                {
                    "name": name,
                    "distance": dm,
                    "elapsed_time": int(best),
                    "start_date_local": start_local,
                }
            )
    return efforts


# ---------------------------------------------------------------------------
# Driver: read activities.csv, resolve track files, write JSON.
# ---------------------------------------------------------------------------
def _pick(row, *names):
    for nkey in names:
        for key in row:
            if key.strip().lower() == nkey.lower():
                return row[key]
    return None


def _parse_csv_date(s):
    if not s:
        return None
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%b %d, %Y, %I:%M:%S %p UTC"):
        try:
            return datetime.strptime(s.strip(), fmt).isoformat()
        except ValueError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive", help="Path to the export .zip or extracted folder")
    ap.add_argument("--type", default="Run", help='Activity type to import, or "all". Default: Run')
    args = ap.parse_args()

    arc = Archive(args.archive)
    csv_text = arc.read_csv_text("activities.csv")
    if not csv_text:
        sys.exit("Could not find activities.csv in the archive.")

    os.makedirs(OUT_DIR, exist_ok=True)
    reader = csv.DictReader(io.StringIO(csv_text))

    imported = skipped = no_track = errors = 0
    for row in reader:
        act_type = (_pick(row, "Activity Type") or "").strip()
        if args.type != "all" and act_type != args.type:
            skipped += 1
            continue
        act_id = (_pick(row, "Activity ID") or "").strip()
        filename = (_pick(row, "Filename") or "").strip()
        if not act_id:
            continue
        out_path = os.path.join(OUT_DIR, f"{act_id}.json")
        if os.path.exists(out_path):
            imported += 1
            continue
        if not filename:
            no_track += 1
            continue
        raw = arc.read(filename)
        if raw is None:
            no_track += 1
            continue
        meta = {
            "id": int(act_id) if act_id.isdigit() else act_id,
            "name": _pick(row, "Activity Name"),
            "type": act_type or "Run",
            "gear": _pick(row, "Activity Gear"),
            "description": _pick(row, "Activity Description") or "",
            "start_local": _parse_csv_date(_pick(row, "Activity Date")),
        }
        try:
            pts = parse_track(filename, raw)
            if pts is None:
                no_track += 1
                continue
            doc = build_activity(pts, meta)
            if doc is None:
                no_track += 1
                continue
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            imported += 1
            print(f"imported {act_id}  {meta['name']}")
        except RuntimeError as e:  # e.g. missing fitparse
            sys.exit(str(e))
        except Exception as e:
            errors += 1
            print(f"  ! failed {act_id} ({filename}): {e}")

    print(
        f"\nDone. {imported} runs in {OUT_DIR} | "
        f"{skipped} non-matching type | {no_track} without a usable track | "
        f"{errors} parse errors"
    )
    print("Now run:  python app.py")


if __name__ == "__main__":
    main()
