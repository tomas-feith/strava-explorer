"""Generate synthetic run JSON matching export_runs.py's schema.

For local testing / demoing the dashboard without real Strava data:
    python make_sample_data.py --n 60
Writes fake runs to data/runs/. Delete data/runs/ to remove them.
"""

import argparse
import json
import math
import os
import random
from datetime import datetime, timedelta

from paths import runs_dir

OUT = runs_dir()
# A loop around a central point (Lisbon-ish) so the map has something to show.
CENTER = (38.7223, -9.1393)


def make_track(n_points, base_speed):
    latlng, dist, alt, hr, vel, cad, tstream = [], [], [], [], [], [], []
    lat, lon = CENTER[0] + random.uniform(-0.02, 0.02), CENTER[1] + random.uniform(-0.02, 0.02)
    d = 0.0
    for i in range(n_points):
        ang = 2 * math.pi * i / n_points
        lat += 0.00025 * math.cos(ang) + random.uniform(-3e-5, 3e-5)
        lon += 0.00025 * math.sin(ang) + random.uniform(-3e-5, 3e-5)
        v = max(1.8, base_speed + 0.6 * math.sin(ang) + random.uniform(-0.3, 0.3))
        d += v
        latlng.append([round(lat, 6), round(lon, 6)])
        dist.append(round(d, 1))
        alt.append(round(30 + 12 * math.sin(ang * 2) + random.uniform(-2, 2), 1))
        hr.append(int(150 + 18 * math.sin(ang) + random.uniform(-6, 6)))
        vel.append(round(v, 2))
        cad.append(round(85 + random.uniform(-4, 4), 1))
        tstream.append(i)
    return latlng, dist, alt, hr, vel, cad, tstream


def make_run(run_id, start):
    n = random.randint(1500, 4500)  # ~seconds ≈ points
    base_speed = random.uniform(2.6, 3.6)  # m/s
    latlng, dist, alt, hr, vel, cad, tstream = make_track(n, base_speed)
    distance = dist[-1]
    moving = n
    avg_speed = distance / moving
    summary = {
        "id": run_id,
        "name": random.choice(["Morning Run", "Lunch Run", "Evening Run", "Long Run", "Tempo"]),
        "type": "Run",
        "start_date": start.isoformat() + "Z",
        "start_date_local": start.isoformat(),
        "distance": distance,
        "moving_time": moving,
        "elapsed_time": moving + random.randint(0, 300),
        "total_elevation_gain": round(
            sum(max(0, alt[i] - alt[i - 1]) for i in range(1, len(alt))), 1
        ),
        "average_speed": avg_speed,
        "max_speed": max(vel),
        "average_heartrate": sum(hr) / len(hr),
        "max_heartrate": max(hr),
        "average_cadence": sum(cad) / len(cad) / 2,  # Strava reports one-leg
        "kudos_count": random.randint(0, 30),
        "start_latlng": latlng[0],
    }
    # Best efforts for common distances the run is long enough to contain.
    best = []
    for name, dm in [("400m", 400), ("1k", 1000), ("1 mile", 1609), ("5k", 5000), ("10k", 10000)]:
        if distance >= dm:
            pace = 1 / avg_speed * random.uniform(0.92, 1.05)
            best.append(
                {
                    "name": name,
                    "distance": dm,
                    "elapsed_time": int(dm * pace),
                    "start_date_local": start.isoformat(),
                }
            )
    # Per-km splits.
    splits = []
    km = int(distance // 1000)
    for k in range(1, km + 1):
        st = avg_speed * random.uniform(0.9, 1.1)
        splits.append(
            {
                "split": k,
                "distance": 1000.0,
                "moving_time": int(1000 / st),
                "elapsed_time": int(1000 / st),
                "average_speed": st,
                "elevation_difference": round(random.uniform(-8, 8), 1),
                "average_heartrate": 150 + random.uniform(-10, 10),
            }
        )
    detail = {
        "id": run_id,
        "best_efforts": best,
        "splits_metric": splits,
        "gear": {"name": random.choice(["Pegasus 40", "Endorphin Speed"])},
        "description": "",
    }
    streams = {
        "time": {"data": tstream},
        "latlng": {"data": latlng},
        "distance": {"data": dist},
        "altitude": {"data": alt},
        "heartrate": {"data": hr},
        "velocity_smooth": {"data": vel},
        "cadence": {"data": cad},
    }
    return {"summary": summary, "detail": detail, "streams": streams}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=60, help="How many runs to generate. Default: 60")
    ap.add_argument(
        "--start",
        default="2025-01-04",
        help="Date of the first run, YYYY-MM-DD. Default: 2025-01-04",
    )
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    day = datetime.strptime(args.start, "%Y-%m-%d").replace(hour=7, minute=30)
    for i in range(args.n):
        day += timedelta(days=random.choice([1, 2, 2, 3, 4]))
        run_id = 9_000_000 + i
        with open(os.path.join(OUT, f"{run_id}.json"), "w", encoding="utf-8") as f:
            json.dump(make_run(run_id, day + timedelta(hours=random.randint(-2, 11))), f)
    print(f"Wrote {args.n} sample runs to {OUT}")


if __name__ == "__main__":
    main()
