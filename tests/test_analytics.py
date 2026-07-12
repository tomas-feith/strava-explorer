"""Unit tests for the pure analytics functions in data_loader."""

import math

import numpy as np
import pytest

import data_loader as dl


def test_minetti_flat_is_unity():
    assert dl.minetti_cost_factor(0.0) == pytest.approx(1.0)


def test_minetti_uphill_costs_more_than_flat():
    assert dl.minetti_cost_factor(0.10) > 1.0
    # A gentle downhill is cheaper than flat...
    assert dl.minetti_cost_factor(-0.10) < 1.0
    # ...but a steep downhill costs more again (braking).
    assert dl.minetti_cost_factor(-0.40) > dl.minetti_cost_factor(-0.10)


def test_grade_adjusted_pace_flat_equals_raw():
    # 1000 m on the flat in 300 s -> 5:00/km, GAP should match.
    dist = np.linspace(0, 1000, 101)
    alt = np.full(101, 42.0)
    gap = dl.grade_adjusted_pace(dist, alt, moving_time_s=300)
    assert gap == pytest.approx(5.0, abs=0.05)


def test_grade_adjusted_pace_uphill_is_faster_equivalent():
    # Steady 10% climb: equivalent flat pace must be faster (smaller number).
    dist = np.linspace(0, 1000, 101)
    alt = np.linspace(0, 100, 101)  # +100 m over 1000 m == 10%
    raw = 6.0  # 360 s / 1 km
    gap = dl.grade_adjusted_pace(dist, alt, moving_time_s=360)
    assert gap < raw
    assert 3.0 < gap < 4.0  # ~3:37/km from the Minetti factor


def test_grade_adjusted_pace_insufficient_data():
    assert math.isnan(dl.grade_adjusted_pace([0.0], [0.0], 10))
    assert math.isnan(dl.grade_adjusted_pace([], [], 0))


def test_aerobic_decoupling_zero_when_steady():
    v = np.full(100, 3.0)
    hr = np.full(100, 150.0)
    assert dl.aerobic_decoupling(v, hr) == pytest.approx(0.0, abs=1e-9)


def test_aerobic_decoupling_positive_on_drift():
    # Same speed but HR climbs in the second half -> efficiency falls.
    v = np.full(100, 3.0)
    hr = np.concatenate([np.full(50, 150.0), np.full(50, 165.0)])
    d = dl.aerobic_decoupling(v, hr)
    assert d == pytest.approx((1 / 150 - 1 / 165) / (1 / 150) * 100, abs=0.01)
    assert d > 0


def test_aerobic_decoupling_too_short():
    assert math.isnan(dl.aerobic_decoupling([1, 2], [100, 100]))


def test_pace_at_hr_isolates_target_band():
    # Half the samples sit at HR 150 running 3.0 m/s -> 5:33/km there.
    v = np.full(60, 3.0)
    hr = np.concatenate([np.full(30, 150.0), np.full(30, 175.0)])
    p = dl.pace_at_hr(v, hr, target_hr=150, tol=4)
    assert p == pytest.approx((1000 / 3.0) / 60.0, abs=0.01)


def test_pace_at_hr_no_samples_in_band():
    v = np.full(30, 3.0)
    hr = np.full(30, 180.0)
    assert math.isnan(dl.pace_at_hr(v, hr, target_hr=150, tol=4))


def test_advanced_metrics_df_from_synthetic_docs(monkeypatch):
    """advanced_metrics_df should compute per-run rows from streams."""
    n = 120
    doc = {
        "summary": {
            "id": 7, "start_date_local": "2025-05-01T07:00:00",
            "distance": 600.0, "moving_time": 200,
        },
        "streams": {
            "time": {"data": list(range(n))},
            "distance": {"data": list(np.linspace(0, 600, n))},
            "altitude": {"data": list(np.linspace(0, 30, n))},
            "velocity_smooth": {"data": [3.0] * n},
            "heartrate": {"data": [150] * (n // 2) + [162] * (n - n // 2)},
        },
    }
    monkeypatch.setattr(dl, "_load_all", lambda: [doc])
    df = dl.advanced_metrics_df(target_hr=150)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["id"] == 7
    assert np.isfinite(row["gap_min_km"])
    assert np.isfinite(row["decoupling_pct"]) and row["decoupling_pct"] > 0
    assert np.isfinite(row["pace_at_hr_min_km"])


def test_advanced_metrics_df_empty(monkeypatch):
    monkeypatch.setattr(dl, "_load_all", lambda: [])
    assert dl.advanced_metrics_df().empty
