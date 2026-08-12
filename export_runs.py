"""Export all Strava runs with full detail + streams to per-run JSON files.

Usage:
    python export_runs.py                 # export runs (default)
    python export_runs.py --type all      # export every activity type
    python export_runs.py --no-streams    # skip the per-second time series

Behavior:
  * Resumable -- a run whose JSON already exists in data/runs/ is skipped,
    so you can stop/restart across days without losing progress.
  * Rate-limit aware -- reads Strava's X-RateLimit headers and sleeps when
    the 15-minute window is nearly exhausted; aborts cleanly if the daily
    limit is hit so you can resume tomorrow.

Each output file data/runs/<id>.json contains:
    { "summary": {...}, "detail": {...}, "streams": {...} }
"""

import argparse
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

from paths import runs_dir

API = "https://www.strava.com/api/v3"
OUT_DIR = runs_dir()

# Stream types worth pulling for a run. Strava returns only those that exist.
STREAM_KEYS = [
    "time",
    "latlng",
    "distance",
    "altitude",
    "velocity_smooth",
    "heartrate",
    "cadence",
    "watts",
    "temp",
    "moving",
    "grade_smooth",
]


class RateLimiter:
    """Tracks Strava's rate-limit headers and pauses when needed.

    Strava returns, on every response:
        X-RateLimit-Limit:  "200,2000"   (15-min, daily)
        X-RateLimit-Usage:  "5,123"       (15-min, daily) after this request
    """

    def __init__(self, short_margin=5, daily_margin=10):
        self.short_margin = short_margin
        self.daily_margin = daily_margin

    def update_and_wait(self, resp):
        limit = resp.headers.get("X-RateLimit-Limit")
        usage = resp.headers.get("X-RateLimit-Usage")
        if not limit or not usage:
            return
        short_limit, daily_limit = (int(x) for x in limit.split(","))
        short_used, daily_used = (int(x) for x in usage.split(","))

        if daily_used >= daily_limit - self.daily_margin:
            sys.exit(
                f"\nDaily rate limit nearly reached ({daily_used}/{daily_limit}). "
                "Re-run tomorrow -- the export will resume where it left off."
            )

        if short_used >= short_limit - self.short_margin:
            # Sleep to the next 15-minute boundary of the clock.
            now = time.time()
            wait = 15 * 60 - (int(now) % (15 * 60)) + 5
            mins = wait / 60
            print(f"  15-min limit near ({short_used}/{short_limit}); sleeping {mins:.1f} min...")
            time.sleep(wait)


def get_access_token():
    load_dotenv()
    cid = os.environ.get("STRAVA_CLIENT_ID")
    secret = os.environ.get("STRAVA_CLIENT_SECRET")
    refresh = os.environ.get("STRAVA_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        sys.exit("Missing credentials. Run strava_auth.py first.")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": cid,
            "client_secret": secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# A 429 costs a 15-minute sleep, so a handful of retries is already an hour of
# waiting; past that, something is wrong that sleeping will not fix.
MAX_429_RETRIES = 4


def api_get(path, token, limiter, params=None):
    """GET an API path, waiting out rate limits. Raises after MAX_429_RETRIES 429s."""
    for attempt in range(MAX_429_RETRIES + 1):
        resp = requests.get(
            f"{API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        limiter.update_and_wait(resp)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()
        # Belt-and-suspenders: honor a hard 429 even if the headers looked fine.
        if attempt < MAX_429_RETRIES:
            print(f"  Got 429 ({attempt + 1}/{MAX_429_RETRIES}); sleeping 15 min then retrying...")
            time.sleep(15 * 60 + 5)
    sys.exit(
        f"Still rate-limited after {MAX_429_RETRIES} retries on {path}. "
        "Re-run later -- the export resumes where it left off."
    )


def list_activities(token, limiter, activity_type):
    """Yield summary activity dicts, newest first, across all pages."""
    page = 1
    while True:
        batch = api_get(
            "/athlete/activities", token, limiter, params={"per_page": 200, "page": page}
        )
        if not batch:
            return
        for act in batch:
            if activity_type == "all" or act.get("type") == activity_type:
                yield act
        page += 1


def export_one(act, token, limiter, want_streams):
    act_id = act["id"]
    out_path = os.path.join(OUT_DIR, f"{act_id}.json")
    if os.path.exists(out_path):
        return False  # already done -> resumable skip

    detail = api_get(
        f"/activities/{act_id}", token, limiter, params={"include_all_efforts": "true"}
    )

    streams = {}
    if want_streams:
        streams = api_get(
            f"/activities/{act_id}/streams",
            token,
            limiter,
            params={"keys": ",".join(STREAM_KEYS), "key_by_type": "true"},
        )

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"summary": act, "detail": detail, "streams": streams}, f, ensure_ascii=False, indent=2
        )
    return True


def main():
    parser = argparse.ArgumentParser(description="Export Strava activities to JSON.")
    parser.add_argument(
        "--type",
        default="Run",
        help='Activity type to export (e.g. Run, Ride) or "all". Default: Run',
    )
    parser.add_argument(
        "--no-streams", action="store_true", help="Skip per-second GPS/HR/etc. streams."
    )
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    limiter = RateLimiter()
    token = get_access_token()

    print(f"Listing activities (type={args.type})...")
    activities = list(list_activities(token, limiter, args.type))
    print(f"Found {len(activities)} matching activities.\n")

    exported = skipped = 0
    for i, act in enumerate(activities, 1):
        label = act.get("name", "?")
        did = export_one(act, token, limiter, want_streams=not args.no_streams)
        if did:
            exported += 1
            print(f"[{i}/{len(activities)}] saved {act['id']}  {label}")
        else:
            skipped += 1

    print(f"\nDone. {exported} exported, {skipped} already present. Files in {OUT_DIR}")


if __name__ == "__main__":
    main()
