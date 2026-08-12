"""The dashboard must come up and explain itself when data is unusable.

Two distinct failures, two boundaries:

* the startup load raising -- the server has to bind anyway, or there is no page
  on which to report anything;
* a single tab's figure raising -- tabs build lazily, so the cost should be that
  one tab rather than Dash's error overlay over the whole app.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _reimport_app(runs_dir: Path):
    """Import app.py fresh against a given runs directory."""
    os.environ["STRAVA_RUNS_DIR"] = str(runs_dir)
    sys.path.insert(0, str(REPO_ROOT))
    for name in ("app", "data_loader", "paths"):
        sys.modules.pop(name, None)
    return importlib.import_module("app")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for name in ("app", "data_loader", "paths"):
        sys.modules.pop(name, None)
    os.environ.pop("STRAVA_RUNS_DIR", None)


def test_a_malformed_run_does_not_stop_the_app_importing(tmp_path: Path) -> None:
    """A doc that is valid JSON but the wrong shape used to raise at import.

    data_loader skips files that will not parse as JSON at all, so anything that
    reaches load_runs_df is structurally surprising rather than corrupt -- here
    a summary whose start_latlng is a bare number instead of a [lat, lon] pair.
    """
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "1.json").write_text(
        json.dumps({"summary": {"id": 1, "start_latlng": 5}, "detail": {}, "streams": {}}),
        encoding="utf-8",
    )

    app = _reimport_app(runs)

    # Imported successfully -- that is the point of the boundary.
    assert app.LOAD_ERROR is not None
    assert app.RUNS.empty
    # And the empty-state panel names the failure rather than showing "no runs".
    panel = app.kpi_cards()
    assert "Could not read your runs" in str(panel)
    assert app.LOAD_ERROR in str(panel)


def test_clean_data_leaves_no_load_error(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "1.json").write_text(
        json.dumps(
            {
                "summary": {
                    "id": 1,
                    "name": "Run",
                    "type": "Run",
                    "start_date_local": "2025-03-01T07:30:00",
                    "distance": 5000.0,
                    "moving_time": 1500,
                    "elapsed_time": 1500,
                    "start_latlng": [38.72, -9.14],
                },
                "detail": {},
                "streams": {},
            }
        ),
        encoding="utf-8",
    )

    app = _reimport_app(runs)
    assert app.LOAD_ERROR is None
    assert len(app.RUNS) == 1


def test_a_failing_tab_reports_itself_and_leaves_the_others(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "1.json").write_text(
        json.dumps(
            {
                "summary": {
                    "id": 1,
                    "name": "Run",
                    "type": "Run",
                    "start_date_local": "2025-03-01T07:30:00",
                    "distance": 5000.0,
                    "moving_time": 1500,
                    "elapsed_time": 1500,
                    "start_latlng": [38.72, -9.14],
                },
                "detail": {},
                "streams": {},
            }
        ),
        encoding="utf-8",
    )
    app = _reimport_app(runs)

    def broken():
        raise ValueError("this chart is broken")

    app._TABS["geography"] = broken

    rendered = str(app.render_tab("geography"))
    assert "could not be built" in rendered
    assert "ValueError: this chart is broken" in rendered

    # A different tab is unaffected.
    assert "could not be built" not in str(app.render_tab("overview"))
