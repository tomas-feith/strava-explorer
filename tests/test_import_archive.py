"""Unit tests for import_archive parsing and derived-metric helpers."""


import pytest

import import_archive as ia

GPX_SAMPLE = """<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
<trk><trkseg>
<trkpt lat="38.720000" lon="-9.140000"><ele>30.0</ele>
  <time>2025-03-01T07:30:00Z</time>
  <extensions><gpxtpx:TrackPointExtension>
    <gpxtpx:hr>150</gpxtpx:hr><gpxtpx:cad>88</gpxtpx:cad>
  </gpxtpx:TrackPointExtension></extensions></trkpt>
<trkpt lat="38.720100" lon="-9.140000"><ele>31.0</ele>
  <time>2025-03-01T07:30:03Z</time>
  <extensions><gpxtpx:TrackPointExtension>
    <gpxtpx:hr>152</gpxtpx:hr><gpxtpx:cad>89</gpxtpx:cad>
  </gpxtpx:TrackPointExtension></extensions></trkpt>
</trkseg></trk></gpx>"""


def test_haversine_known_distance():
    # 0.001 deg of latitude ~= 111.19 m near the equator.
    d = ia._haversine(0.0, 0.0, 0.001, 0.0)
    assert d == pytest.approx(111.19, abs=1.0)


def test_haversine_zero():
    assert ia._haversine(38.7, -9.1, 38.7, -9.1) == pytest.approx(0.0, abs=1e-6)


def test_parse_gpx_extracts_points_and_extensions():
    pts = ia.parse_gpx(GPX_SAMPLE.encode())
    assert pts["lat"] == [38.72, 38.7201]
    assert pts["lon"] == [-9.14, -9.14]
    assert pts["ele"] == [30.0, 31.0]
    assert pts["hr"] == [150, 152]
    assert pts["cad"] == [88, 89]
    # Times parse to epoch seconds, 3 s apart.
    assert pts["t"][1] - pts["t"][0] == pytest.approx(3.0)


def test_interp_time_linear():
    dist = [0, 500, 1000, 1500, 2000]
    t = [0, 150, 300, 450, 600]
    assert ia._interp_time(dist, t, 1000) == pytest.approx(300)
    assert ia._interp_time(dist, t, 750) == pytest.approx(225)  # halfway 500->1000
    assert ia._interp_time(dist, t, 5000) is None  # never reached


def test_best_efforts_constant_speed():
    # 2000 m at a steady 10/3 m/s (10 m every 3 s).
    dist = [i * 10 for i in range(201)]
    t = [i * 3 for i in range(201)]
    efforts = {e["name"]: e for e in ia._best_efforts(dist, t, "2025-03-01T00:00:00")}
    assert "1k" in efforts
    assert efforts["1k"]["elapsed_time"] == pytest.approx(300, abs=1)
    assert efforts["400m"]["elapsed_time"] == pytest.approx(120, abs=1)
    # The run is too short for a half-marathon.
    assert "Half-Marathon" not in efforts


def test_splits_constant_speed():
    dist = [i * 10 for i in range(201)]     # 0..2000 m
    t = [i * 3 for i in range(201)]         # 3 s per 10 m
    hr = [150] * 201
    alt = [20.0 + (i * 10) / 1000.0 for i in range(201)]  # +1 m per km
    splits = ia._splits(dist, t, hr, alt)
    assert len(splits) == 2
    assert splits[0]["distance"] == 1000.0
    assert splits[0]["moving_time"] == pytest.approx(300, abs=1)
    assert splits[0]["average_heartrate"] == pytest.approx(150.0)


def test_build_activity_end_to_end():
    pts = ia.parse_gpx(GPX_SAMPLE.encode())
    meta = {"id": 42, "name": "Test Run", "type": "Run"}
    doc = ia.build_activity(pts, meta)
    assert doc is not None
    s = doc["summary"]
    assert s["id"] == 42
    assert s["distance"] > 0
    assert s["elapsed_time"] == 3
    # GPS + HR + cadence streams should all be present.
    for key in ("time", "distance", "latlng", "heartrate", "cadence"):
        assert key in doc["streams"]
    assert s["average_heartrate"] == pytest.approx(151.0)


def test_build_activity_rejects_single_point():
    pts = {"lat": [1.0], "lon": [2.0], "ele": [10.0], "t": [1000.0],
           "hr": [150], "cad": [80]}
    assert ia.build_activity(pts, {"id": 1}) is None


def test_maybe_gunzip_passthrough():
    assert ia.maybe_gunzip("x.gpx", b"plain") == b"plain"


def test_parse_csv_date_formats():
    assert ia._parse_csv_date("Mar 1, 2025, 7:30:00 AM") is not None
    assert ia._parse_csv_date("garbage") is None
    assert ia._parse_csv_date(None) is None


def test_archive_reads_zip_and_gunzips(tmp_path):
    import gzip
    import zipfile

    zpath = tmp_path / "export.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("activities.csv", "Activity ID,Filename\n1,activities/1.gpx.gz\n")
        z.writestr("activities/1.gpx.gz", gzip.compress(GPX_SAMPLE.encode()))

    arc = ia.Archive(str(zpath))
    assert arc.read_csv_text("activities.csv").startswith("Activity ID")
    raw = arc.read("activities/1.gpx.gz")
    pts = ia.parse_track("activities/1.gpx.gz", raw)
    assert pts["lat"] == [38.72, 38.7201]
