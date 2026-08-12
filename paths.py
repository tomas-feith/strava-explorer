"""The one place that decides where run JSON lives.

Three modules have to agree on this directory: ``data_loader.py`` reads it, and
``export_runs.py`` and ``import_archive.py`` both write into it. They used to
define it separately, and only the reader honoured ``STRAVA_RUNS_DIR`` -- so
setting that variable pointed the dashboard at one directory while the two
importers kept writing to the default, and the runs never showed up.

Stdlib only, so the importers can use it without pulling in pandas.
"""

from __future__ import annotations

import os

DEFAULT_RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runs")


def runs_dir() -> str:
    """Directory holding ``<activity id>.json``, overridable via ``STRAVA_RUNS_DIR``.

    Read at import time by each caller, so set the variable before starting the
    app or an importer rather than expecting it to change mid-run.
    """
    return os.environ.get("STRAVA_RUNS_DIR") or DEFAULT_RUNS_DIR
