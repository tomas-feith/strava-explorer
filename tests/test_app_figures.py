"""Smoke tests for the Dash figure builders in app.py.

app.py reads its DataFrames at import time, so the fixture writes a dataset and
points STRAVA_RUNS_DIR at it *before* importing the module -- which also proves
the reader and the writers agree on that directory (they used to not: only
data_loader honoured the variable, so the importers wrote where nothing looked).

The year is deliberately 2024. It starts on a Monday, so strftime("%W") reaches
53 in late December; fig_calendar sized its grid to 53 columns and raised
IndexError for exactly those years (2018, 2024, 2029, 2035). Because the tabs
were built at import time, that took the entire app down rather than one chart.
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_doc(run_id: int, day: str, n: int = 40) -> dict:
    """A minimal run whose streams exercise every metric the figures use."""
    return {
        "summary": {
            "id": run_id,
            "name": f"Run {run_id}",
            "type": "Run",
            "start_date": f"{day}T07:30:00Z",
            "start_date_local": f"{day}T07:30:00",
            "distance": 10.0 * n,
            "moving_time": n,
            "elapsed_time": n,
            "total_elevation_gain": 12.0,
            "average_speed": 3.0,
            "average_heartrate": 150.0,
            "max_heartrate": 168,
            "average_cadence": 85.0,
            "kudos_count": 3,
            "start_latlng": [38.72, -9.14],
        },
        "detail": {
            "id": run_id,
            "gear": {"name": "Pegasus"},
            "best_efforts": [
                {
                    "name": "1k",
                    "distance": 1000,
                    "elapsed_time": 300,
                    "start_date_local": f"{day}T07:30:00",
                }
            ],
            "splits_metric": [
                {
                    "split": 1,
                    "distance": 1000.0,
                    "moving_time": 300,
                    "average_speed": 3.3,
                    "elevation_difference": 2.0,
                    "average_heartrate": 150.0,
                }
            ],
        },
        "streams": {
            "time": {"data": list(range(n))},
            "latlng": {"data": [[38.72 + i * 1e-4, -9.14] for i in range(n)]},
            "distance": {"data": [10.0 * i for i in range(n)]},
            "altitude": {"data": [30.0 + (i % 7) for i in range(n)]},
            "heartrate": {"data": [148 + (i % 10) for i in range(n)]},
            "velocity_smooth": {"data": [3.0] * n},
            "cadence": {"data": [85] * n},
        },
    }


@pytest.fixture(scope="module")
def app_module(tmp_path_factory: pytest.TempPathFactory):
    """Import app.py against a generated 2024 dataset in a throwaway directory."""
    runs = tmp_path_factory.mktemp("runs")
    # Late December is the part that lands in ISO-ish week 53 for a Monday year.
    days = ["2024-01-01", "2024-03-15", "2024-07-04", "2024-12-25", "2024-12-30", "2024-12-31"]
    for i, day in enumerate(days):
        (runs / f"{9_000_000 + i}.json").write_text(
            json.dumps(_run_doc(9_000_000 + i, day)), encoding="utf-8"
        )

    os.environ["STRAVA_RUNS_DIR"] = str(runs)
    sys.path.insert(0, str(REPO_ROOT))
    for name in ("app", "data_loader", "paths"):
        sys.modules.pop(name, None)
    import app

    yield app

    for name in ("app", "data_loader", "paths"):
        sys.modules.pop(name, None)
    os.environ.pop("STRAVA_RUNS_DIR", None)


def test_reader_picks_up_the_generated_runs(app_module) -> None:
    """Guards the reader/writer split: data_loader must honour STRAVA_RUNS_DIR."""
    assert len(app_module.RUNS) == 6
    assert set(app_module.RUNS["year"]) == {2024}


@pytest.mark.parametrize(
    "builder",
    [
        "fig_weekly_mileage",
        "fig_cumulative_ytd",
        "fig_calendar",
        "fig_dow_hour",
        "fig_pace_over_time",
        "fig_pace_vs_distance",
        "fig_best_efforts",
        "fig_heatmap_map",
        "fig_hr_zones",
        "fig_cadence_vs_pace",
        "fig_gap_vs_actual",
        "fig_decoupling",
        "fig_pace_at_hr",
    ],
)
def test_figure_builds(app_module, builder: str) -> None:
    fig = getattr(app_module, builder)()
    assert fig is not None


def test_calendar_grid_has_room_for_week_53(app_module) -> None:
    """The regression this file exists for: a Monday-start year reaches week 53."""
    import pandas as pd

    weeks = {
        int(d.strftime("%W"))
        for d in pd.date_range(pd.Timestamp(2024, 1, 1), pd.Timestamp(2024, 12, 31))
    }
    assert max(weeks) == 53
    assert app_module.CALENDAR_WEEKS > max(weeks)
    # And the figure itself renders that year without an IndexError.
    fig = app_module.fig_calendar()
    assert fig.data[0].z.shape == (7, app_module.CALENDAR_WEEKS)


def test_every_tab_renders(app_module) -> None:
    """Tabs build lazily now; make sure each one still produces a component."""
    for name in app_module._TABS:
        assert app_module.render_tab(name) is not None


def test_run_detail_renders_for_a_real_run(app_module) -> None:
    run_id = int(app_module.RUNS["id"].iloc[-1])
    assert app_module.render_run_detail(run_id) is not None


def test_run_detail_handles_no_selection(app_module) -> None:
    assert app_module.render_run_detail(None) is not None
