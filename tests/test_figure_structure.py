"""Structural snapshots of every figure.

`test_app_figures.py` asserts each builder returns *something*. That catches a
crash, which is how the week-53 IndexError was found -- but not a chart that
still renders while being wrong: a reversed pace axis that stops being
reversed, a trace that silently disappears, a heatmap that loses its year.

These pin the structure that carries meaning. Deliberately not a pixel or JSON
snapshot: those break on every plotly upgrade and get regenerated without being
read, which is worse than no test. Each assertion below names a property
someone would notice was wrong.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_doc(run_id: int, day: str, n: int = 40) -> dict:
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
def app(tmp_path_factory: pytest.TempPathFactory):
    runs = tmp_path_factory.mktemp("runs")
    for i, day in enumerate(["2024-01-01", "2024-03-15", "2024-07-04", "2024-12-30"]):
        (runs / f"{9_000_000 + i}.json").write_text(
            json.dumps(_run_doc(9_000_000 + i, day)), encoding="utf-8"
        )
    os.environ["STRAVA_RUNS_DIR"] = str(runs)
    sys.path.insert(0, str(REPO_ROOT))
    for name in ("app", "data_loader", "paths"):
        sys.modules.pop(name, None)
    import app as module

    yield module

    for name in ("app", "data_loader", "paths"):
        sys.modules.pop(name, None)
    os.environ.pop("STRAVA_RUNS_DIR", None)


def trace_types(fig) -> list[str]:
    return [t.type for t in fig.data]


# --- pace axes must stay inverted ------------------------------------------
# Every pace chart plots min/km, where SMALLER is better, so the y axis is
# reversed and the label says "faster up". Lose the reversal and the chart
# reads exactly backwards while looking entirely normal.


@pytest.mark.parametrize(
    "builder",
    [
        "fig_pace_over_time",
        "fig_pace_vs_distance",
        "fig_best_efforts",
        "fig_pace_at_hr",
        "fig_gap_vs_actual",
    ],
)
def test_pace_axes_are_reversed(app, builder: str) -> None:
    fig = getattr(app, builder)()
    assert fig.layout.yaxis.autorange == "reversed", f"{builder} lost its reversed pace axis"


def test_cadence_chart_reverses_the_x_axis_instead(app) -> None:
    """Pace is on x here, so the reversal moves with it."""
    fig = app.fig_cadence_vs_pace()
    assert fig.layout.xaxis.autorange == "reversed"
    assert fig.layout.yaxis.autorange != "reversed"


# --- composition -----------------------------------------------------------


def test_weekly_mileage_has_bars_and_a_rolling_line(app) -> None:
    fig = app.fig_weekly_mileage()
    assert trace_types(fig) == ["bar", "scatter"]
    assert [t.name for t in fig.data] == ["Weekly km", "4-week avg"]


def test_pace_over_time_carries_a_trendline(app) -> None:
    fig = app.fig_pace_over_time()
    assert "trend" in [t.name for t in fig.data]


def test_gap_chart_shows_actual_and_grade_adjusted(app) -> None:
    names = [t.name for t in app.fig_gap_vs_actual().data]
    assert "actual" in names and "grade-adjusted" in names


def test_calendar_is_a_full_year_grid_titled_with_the_year(app) -> None:
    fig = app.fig_calendar()
    assert trace_types(fig) == ["heatmap"]
    assert fig.data[0].z.shape == (7, app.CALENDAR_WEEKS)
    assert "2024" in fig.layout.title.text
    # Weekday labels, Monday first, top to bottom.
    assert list(fig.layout.yaxis.ticktext) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert fig.layout.yaxis.autorange == "reversed"


def test_hr_zone_chart_covers_all_five_zones(app) -> None:
    fig = app.fig_hr_zones()
    assert len(fig.data) == 5  # one trace per zone (coloured discretely)
    assert f"HR max={app.dl.HR_MAX}" in fig.layout.title.text


def test_decoupling_chart_marks_the_five_percent_threshold(app) -> None:
    """The horizontal reference line is the whole point of the chart."""
    fig = app.fig_decoupling()
    hlines = [s for s in fig.layout.shapes if s.type == "line"]
    assert hlines, "the 5% aerobic-durability threshold line is missing"
    assert any(s.y0 == 5 for s in hlines)


# --- the empty state -------------------------------------------------------


def test_every_builder_produces_a_figure_with_no_data(app, monkeypatch) -> None:
    """With an empty catalog each chart must return the placeholder, not raise."""
    import pandas as pd

    monkeypatch.setattr(app, "RUNS", pd.DataFrame())
    monkeypatch.setattr(app, "BEST", pd.DataFrame())
    monkeypatch.setattr(app, "ADV", pd.DataFrame())

    for name in [n for n in dir(app) if n.startswith("fig_")]:
        fig = getattr(app, name)()
        assert fig is not None, name
