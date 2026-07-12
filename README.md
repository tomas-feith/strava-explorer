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

This project uses [uv](https://docs.astral.sh/uv/):

```
uv sync                 # runtime deps into .venv
uv sync --extra fit     # also install fitparse (only if your archive has .fit files)
```

Then run commands with `uv run …` (e.g. `uv run python app.py`), or activate
`.venv`. Prefer plain pip? `pip install -r requirements.txt` still works.

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
- **Advanced** — grade-adjusted vs actual pace (Minetti cost model), aerobic
  decoupling (pace:HR drift, 1st→2nd half), and pace at a fixed HR over time
  (an aerobic-fitness trend)
- **Run detail** — route colored by pace, elevation profile, per-km splits

---

## Development

```
uv sync --extra fit            # install runtime + dev tools
uv run pre-commit install      # enable ruff + mypy git hooks

uv run pytest                  # tests
uv run ruff check .            # lint
uv run mypy app.py data_loader.py import_archive.py export_runs.py strava_auth.py make_sample_data.py tests
```

Ruff + mypy run automatically on every commit via pre-commit, and the same
three checks run in CI (`.github/workflows/ci.yml`) on every push and PR.
Config lives in `pyproject.toml`.

Tuning knobs (env vars): `STRAVA_HR_MAX` (HR zones, default 190),
`STRAVA_FITNESS_HR` (the fixed HR for the fitness trend, default 150),
`STRAVA_RUNS_DIR` (where the dashboard reads run JSON).

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

## Try it without your data
```
uv run python make_sample_data.py --n 60   # writes synthetic runs to data/runs/
uv run python app.py
```
Delete the samples before importing real data: `rm -rf data/runs`

## Files
- `import_archive.py` — bulk-archive → JSON (free path)
- `export_runs.py` / `strava_auth.py` — Strava API → JSON (subscription path)
- `data_loader.py` — JSON → tidy DataFrames + derived metrics (incl. analytics)
- `app.py` — the Dash dashboard
- `make_sample_data.py` — synthetic data for demos/tests
- `tests/` — pytest unit tests for the analytics + import helpers
