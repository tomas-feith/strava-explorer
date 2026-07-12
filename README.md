# strava_explorer

A local dashboard for exploring **all your runs with all their data** —
heatmaps of where you run, pace/PR progression, HR zones, per-run route +
splits, and more.

The dashboard is **data-source agnostic**: it reads `data/runs/<id>.json` and
doesn't care how those files got there. There are two ways to fill that folder,
and you can even mix them:

| Source | Command | Cost | GPS/HR streams |
|--------|---------|------|----------------|
| **Bulk archive (recommended)** | `python import_archive.py export.zip` | Free for every athlete | Yes |
| **Strava API** | `python export_runs.py` | Requires a Strava subscription (since 2026) | Yes |

> **Why two paths?** As of June 30, 2026 the Strava API's Standard tier requires
> an active Strava subscription. But **every athlete can still download all their
> own data for free** via the account archive — so that's the default path here.

## Install

```
pip install -r requirements.txt      # add: pip install fitparse  (only if your archive has .fit files)
```

---

## Option A — Bulk archive (free)

1. Request your archive: **strava.com → Settings → My Account → "Download or
   Delete Your Account" → Request your archive.** Strava emails you a ZIP.
2. Import it (auto-detects GPX / TCX / FIT, including `.gz`):
   ```
   python import_archive.py path/to/export_12345.zip
   python import_archive.py export.zip --type all     # not just runs
   ```
   Distance, pace, per-km splits and best-efforts are computed from each track.

## Option B — Strava API (subscription)

1. Create an app at https://www.strava.com/settings/api (callback domain `localhost`).
2. `copy .env.example .env` and fill in Client ID + Secret.
3. `python strava_auth.py` — one-time browser login, saves a refresh token.
4. `python export_runs.py` — pulls runs with full detail + streams. It's
   rate-limit-aware and **resumable**; if the daily cap hits, just re-run
   tomorrow. Flags: `--type all`, `--no-streams`.

## Run the dashboard

```
python app.py      →  open http://127.0.0.1:8050
```

Restart the app to pick up newly imported/exported runs.

### Tabs
- **Overview** — totals, daily-distance calendar, weekly volume + 4-wk avg,
  cumulative-by-year, day×hour heatmap
- **Performance** — pace over time (trend), pace-vs-distance, best-effort +
  PR progression
- **Geography** — GPS density heatmap of everywhere you've run
- **Physiology** — real time-in-HR-zone (from streams), cadence-vs-pace
- **Run detail** — route colored by pace, elevation profile, per-km splits

---

## Data schema

Each `data/runs/<id>.json`:
```json
{ "summary": { ... }, "detail": { ... }, "streams": { ... } }
```
- `summary` — distance, moving time, pace, elevation, avg/max HR, cadence …
- `detail` — `splits_metric`, `best_efforts`, gear, description
- `streams` — per-point series: `time`, `latlng`, `distance`, `altitude`,
  `velocity_smooth`, `heartrate`, `cadence` (only those present in your data)

## Config
- `STRAVA_HR_MAX` (env var) — sets HR-zone boundaries (default 190).

## Try it without your data
```
python make_sample_data.py --n 60     # writes synthetic runs to data/runs/
python app.py
```
Delete the samples before importing real data: `rm -rf data/runs`

## Files
- `import_archive.py` — bulk-archive → JSON (free path)
- `export_runs.py` / `strava_auth.py` — Strava API → JSON (subscription path)
- `data_loader.py` — JSON → tidy DataFrames + derived metrics
- `app.py` — the Dash dashboard
- `make_sample_data.py` — synthetic data for demos/tests
