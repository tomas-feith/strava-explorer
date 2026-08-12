"""Strava Explorer -- a Plotly Dash dashboard over your exported runs.

Run:
    python app.py
then open the URL it prints (http://127.0.0.1:8050, or the next free port if
8050 is already taken).

Reads data/runs/*.json (see export_runs.py). All figures degrade gracefully
when a metric is missing (e.g. no HR stream) so partial data still renders.
"""

import os
from functools import cache

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

import data_loader as dl
from freeport import find_free_port

# ----------------------------------------------------------------------------
# Load once at startup. Restart the app to pick up newly exported runs.
#
# A load failure must not stop the server coming up. data_loader already skips
# files that are unreadable or not JSON, so what lands here is a doc that parsed
# but is not shaped the way we expect -- and the useful response to that is a
# running page that says so, not a stack trace in the terminal and no dashboard.
# ----------------------------------------------------------------------------
LOAD_ERROR: str | None = None


def _load_frames():
    global LOAD_ERROR
    try:
        return dl.load_runs_df(), dl.load_best_efforts_df(), dl.advanced_metrics_df()
    except Exception as exc:
        LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        empty = pd.DataFrame()
        return empty, empty.copy(), empty.copy()


RUNS, BEST, ADV = _load_frames()

ACCENT = "#fc4c02"  # Strava orange
ERROR_COLOR = "#c0392b"  # failure notices; distinct from the Strava orange
TEMPLATE = "plotly_white"

# Columns in the calendar heatmap. strftime("%W") is 00-53, not 00-52: a year
# that starts on a Monday (2018, 2024, 2029...) puts its last days in week 53.
# Sizing this to 53 made fig_calendar raise IndexError for exactly those years,
# and since the tab is built at import time that took the whole app down.
CALENDAR_WEEKS = 54


def _empty_fig(msg="No data"):
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=16, color="#888"))
    fig.update_layout(
        template=TEMPLATE, height=300, xaxis=dict(visible=False), yaxis=dict(visible=False)
    )
    return fig


def _trendline(x_num, y):
    """Return (x, yhat) for a simple OLS line, ignoring NaNs."""
    mask = np.isfinite(x_num) & np.isfinite(y)
    if mask.sum() < 2:
        return None, None
    coef = np.polyfit(x_num[mask], y[mask], 1)
    xs = np.array([x_num[mask].min(), x_num[mask].max()])
    return xs, np.polyval(coef, xs)


def fmt_pace(p):
    if not np.isfinite(p):
        return "-"
    m = int(p)
    s = int(round((p - m) * 60))
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}/km"


# ----------------------------------------------------------------------------
# Figure builders
# ----------------------------------------------------------------------------
def fig_weekly_mileage():
    if RUNS.empty:
        return _empty_fig()
    wk = RUNS.groupby("week", as_index=False)["distance_km"].sum()
    wk["roll4"] = wk["distance_km"].rolling(4, min_periods=1).mean()
    fig = go.Figure()
    fig.add_bar(
        x=wk["week"], y=wk["distance_km"], name="Weekly km", marker_color=ACCENT, opacity=0.75
    )
    fig.add_scatter(
        x=wk["week"],
        y=wk["roll4"],
        name="4-week avg",
        mode="lines",
        line=dict(color="#333", width=2),
    )
    fig.update_layout(
        template=TEMPLATE,
        height=340,
        bargap=0.1,
        title="Weekly volume",
        yaxis_title="km",
        legend=dict(orientation="h", y=1.12),
    )
    return fig


def fig_cumulative_ytd():
    if RUNS.empty:
        return _empty_fig()
    fig = go.Figure()
    for yr, grp in RUNS.groupby("year"):
        grp = grp.sort_values("start")
        doy = grp["start"].dt.dayofyear
        fig.add_scatter(x=doy, y=grp["distance_km"].cumsum(), mode="lines", name=str(int(yr)))
    fig.update_layout(
        template=TEMPLATE,
        height=340,
        title="Cumulative distance by year",
        xaxis_title="Day of year",
        yaxis_title="Cumulative km",
    )
    return fig


def fig_calendar():
    """GitHub-style daily distance heatmap for the most recent year present."""
    if RUNS.empty:
        return _empty_fig()
    year = int(RUNS["year"].max())
    daily = RUNS[RUNS["year"] == year].groupby("date")["distance_km"].sum()
    days = pd.date_range(pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31))
    z = np.full((7, CALENDAR_WEEKS), np.nan)
    text = np.empty((7, CALENDAR_WEEKS), dtype=object)
    for day in days:
        wk = int(day.strftime("%W"))
        wd = day.weekday()
        km = float(daily.get(day.date(), 0.0))
        z[wd, wk] = km
        text[wd, wk] = f"{day.date()}: {km:.1f} km"
    fig = go.Figure(
        go.Heatmap(
            z=z,
            text=text,
            hoverinfo="text",
            xgap=2,
            ygap=2,
            colorscale=[[0, "#eee"], [0.01, "#ffd9c7"], [1, ACCENT]],
            showscale=True,
            colorbar=dict(title="km"),
        )
    )
    fig.update_layout(
        template=TEMPLATE,
        height=240,
        title=f"Daily distance — {year}",
        yaxis=dict(
            tickmode="array",
            tickvals=list(range(7)),
            ticktext=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            autorange="reversed",
        ),
        xaxis=dict(title="Week"),
    )
    return fig


def fig_dow_hour():
    if RUNS.empty:
        return _empty_fig()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = (
        RUNS.pivot_table(index="dow", columns="hour", values="id", aggfunc="count")
        .reindex(order)
        .fillna(0)
    )
    fig = px.imshow(
        pivot, aspect="auto", color_continuous_scale="Oranges", labels=dict(color="runs")
    )
    fig.update_layout(
        template=TEMPLATE,
        height=300,
        title="When you run (day × hour)",
        xaxis_title="Hour of day",
        yaxis_title="",
    )
    return fig


def fig_pace_over_time():
    if RUNS.empty:
        return _empty_fig()
    df = RUNS.dropna(subset=["pace_min_km"])
    fig = px.scatter(
        df,
        x="start",
        y="pace_min_km",
        size="distance_km",
        color="distance_km",
        color_continuous_scale="Oranges",
        hover_data=["name", "distance_km"],
    )
    xs, ys = _trendline(
        df["start"].map(pd.Timestamp.timestamp).to_numpy(), df["pace_min_km"].to_numpy()
    )
    if xs is not None:
        fig.add_scatter(
            x=[pd.Timestamp.fromtimestamp(t, "UTC") for t in xs],
            y=ys,
            mode="lines",
            name="trend",
            line=dict(color="#333"),
        )
    fig.update_yaxes(autorange="reversed", title="pace (min/km, faster ↑)")
    fig.update_layout(template=TEMPLATE, height=380, title="Pace over time", xaxis_title="")
    return fig


def fig_pace_vs_distance():
    if RUNS.empty:
        return _empty_fig()
    df = RUNS.dropna(subset=["pace_min_km"])
    fig = px.scatter(
        df,
        x="distance_km",
        y="pace_min_km",
        color="avg_hr",
        color_continuous_scale="Turbo",
        hover_data=["name", "start"],
    )
    fig.update_yaxes(autorange="reversed", title="pace (min/km, faster ↑)")
    fig.update_layout(
        template=TEMPLATE,
        height=380,
        title="Pace vs distance",
        xaxis_title="distance (km)",
        coloraxis_colorbar_title="avg HR",
    )
    return fig


def fig_best_efforts():
    if BEST.empty:
        return _empty_fig("No best-effort data (needs detailed activities)")
    # Focus on the common race distances if present.
    keep = ["400m", "1k", "1 mile", "5k", "10k", "Half-Marathon", "Marathon"]
    df = BEST[BEST["effort"].isin(keep)].copy()
    if df.empty:
        df = BEST.copy()
    fig = px.scatter(df, x="start", y="pace_min_km", color="effort", hover_data=["effort"])
    for eff, grp in df.groupby("effort"):
        grp = grp.sort_values("start")
        running_best = grp["pace_min_km"].cummin()
        fig.add_scatter(
            x=grp["start"],
            y=running_best,
            mode="lines",
            name=f"{eff} PR",
            line=dict(dash="dot"),
            showlegend=False,
        )
    fig.update_yaxes(autorange="reversed", title="pace (min/km, faster ↑)")
    fig.update_layout(
        template=TEMPLATE, height=400, title="Best-effort pace + PR progression", xaxis_title=""
    )
    return fig


def fig_heatmap_map():
    pts = dl.heatmap_points()
    if pts.empty:
        return _empty_fig("No GPS streams found")
    fig = px.density_map(pts, lat="lat", lon="lon", radius=4, color_continuous_scale="Hot")
    fig.update_layout(
        template=TEMPLATE,
        height=560,
        map_style="open-street-map",
        map=dict(center=dict(lat=pts["lat"].mean(), lon=pts["lon"].mean()), zoom=10),
        margin=dict(l=0, r=0, t=30, b=0),
        title="Where you run (GPS heatmap)",
    )
    return fig


def fig_hr_zones():
    z = dl.hr_zone_distribution()
    if z.empty or z["minutes"].sum() == 0:
        return _empty_fig("No heart-rate streams found")
    z["hours"] = z["minutes"] / 60.0
    fig = px.bar(
        z,
        x="zone",
        y="hours",
        color="zone",
        color_discrete_sequence=px.colors.sequential.Oranges[2:],
    )
    fig.update_layout(
        template=TEMPLATE,
        height=360,
        showlegend=False,
        title=f"Time in HR zone (HR max={dl.HR_MAX})",
        yaxis_title="hours",
        xaxis_title="",
    )
    return fig


def fig_cadence_vs_pace():
    if RUNS.empty:
        return _empty_fig()
    df = RUNS.dropna(subset=["avg_cadence", "pace_min_km"])
    if df.empty:
        return _empty_fig("No cadence data")
    fig = px.scatter(
        df,
        x="pace_min_km",
        y="avg_cadence",
        color="distance_km",
        color_continuous_scale="Oranges",
        hover_data=["name"],
    )
    fig.update_xaxes(autorange="reversed", title="pace (min/km, faster →)")
    fig.update_layout(
        template=TEMPLATE, height=360, title="Cadence vs pace", yaxis_title="cadence (spm)"
    )
    return fig


def fig_gap_vs_actual():
    """Grade-adjusted vs actual pace over time -- hilly runs normalised."""
    if ADV.empty:
        return _empty_fig()
    df = ADV.dropna(subset=["actual_pace_min_km"])
    if df.empty:
        return _empty_fig("No pace data")
    fig = go.Figure()
    fig.add_scatter(
        x=df["start"],
        y=df["actual_pace_min_km"],
        mode="markers",
        name="actual",
        marker=dict(color="#bbb", size=7),
    )
    gap = df.dropna(subset=["gap_min_km"])
    fig.add_scatter(
        x=gap["start"],
        y=gap["gap_min_km"],
        mode="markers",
        name="grade-adjusted",
        marker=dict(color=ACCENT, size=8),
    )
    xs, ys = _trendline(
        gap["start"].map(pd.Timestamp.timestamp).to_numpy(), gap["gap_min_km"].to_numpy()
    )
    if xs is not None:
        fig.add_scatter(
            x=[pd.Timestamp.fromtimestamp(t, "UTC") for t in xs],
            y=ys,
            mode="lines",
            name="GAP trend",
            line=dict(color="#333"),
        )
    fig.update_yaxes(autorange="reversed", title="pace (min/km, faster ↑)")
    fig.update_layout(
        template=TEMPLATE,
        height=400,
        xaxis_title="",
        title="Grade-adjusted vs actual pace",
        legend=dict(orientation="h", y=1.12),
    )
    return fig


def fig_decoupling():
    """Aerobic decoupling per run over time (lower = better-paced aerobically)."""
    if ADV.empty or ADV["decoupling_pct"].dropna().empty:
        return _empty_fig("No HR+pace streams for decoupling")
    df = ADV.dropna(subset=["decoupling_pct"])
    colors = [
        "#2ca02c" if d <= 5 else "#ff7f0e" if d <= 10 else "#d62728" for d in df["decoupling_pct"]
    ]
    fig = go.Figure(go.Bar(x=df["start"], y=df["decoupling_pct"], marker_color=colors))
    fig.add_hline(
        y=5,
        line_dash="dot",
        line_color="#2ca02c",
        annotation_text="5% aerobic-durability threshold",
    )
    fig.update_layout(
        template=TEMPLATE,
        height=380,
        xaxis_title="",
        title="Aerobic decoupling (pace:HR drift, 1st→2nd half)",
        yaxis_title="decoupling %",
    )
    return fig


def fig_pace_at_hr():
    """Pace at a fixed HR over time -- a proxy for aerobic fitness gains."""
    df = ADV.dropna(subset=["pace_at_hr_min_km"]) if not ADV.empty else ADV
    if df is None or df.empty:
        return _empty_fig(f"No samples near HR {dl.FITNESS_HR}")
    fig = px.scatter(df, x="start", y="pace_at_hr_min_km", color_discrete_sequence=[ACCENT])
    xs, ys = _trendline(
        df["start"].map(pd.Timestamp.timestamp).to_numpy(), df["pace_at_hr_min_km"].to_numpy()
    )
    if xs is not None:
        fig.add_scatter(
            x=[pd.Timestamp.fromtimestamp(t, "UTC") for t in xs],
            y=ys,
            mode="lines",
            name="trend",
            line=dict(color="#333"),
        )
    fig.update_yaxes(autorange="reversed", title="pace (min/km, faster ↑)")
    fig.update_layout(
        template=TEMPLATE,
        height=380,
        xaxis_title="",
        title=f"Pace at HR ≈ {dl.FITNESS_HR} bpm (fitness trend)",
    )
    return fig


# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------
app = Dash(__name__, title="Strava Explorer")
server = app.server


def kpi_cards():
    if LOAD_ERROR:
        return html.Div(
            [
                html.P(
                    "Could not read your runs.",
                    style={"fontWeight": "700", "color": ERROR_COLOR},
                ),
                html.Pre(
                    LOAD_ERROR,
                    style={"whiteSpace": "pre-wrap", "background": "#fff", "padding": "0.75rem"},
                ),
                html.P(
                    f"Files that are unreadable or not JSON are skipped silently, so this "
                    f"is a file that parsed but is not shaped as expected. Look in "
                    f"{dl.RUNS_DIR} for a truncated or hand-edited run."
                ),
            ],
            style={"padding": "2rem", "fontSize": "1.05rem"},
        )
    if RUNS.empty:
        return html.Div(
            [
                html.P("No runs found in data/runs/. Load your data one of two ways:"),
                html.Ul(
                    [
                        html.Li("Free — bulk archive:  python import_archive.py export.zip"),
                        html.Li("API (needs Strava subscription):  python export_runs.py"),
                    ]
                ),
                html.P(
                    "Both write the same data/runs/*.json, so the dashboard reads "
                    "either — or a mix of both."
                ),
            ],
            style={"padding": "2rem", "fontSize": "1.05rem"},
        )
    total_km = RUNS["distance_km"].sum()
    n = len(RUNS)
    hours = RUNS["moving_min"].sum() / 60.0
    elev = RUNS["elev_gain_m"].sum(skipna=True)
    best_pace = RUNS["pace_min_km"].min()
    cards = [
        ("Runs", f"{n}"),
        ("Distance", f"{total_km:,.0f} km"),
        ("Moving time", f"{hours:,.0f} h"),
        ("Elevation", f"{elev:,.0f} m"),
        ("Best avg pace", fmt_pace(best_pace)),
    ]
    return html.Div(
        [
            html.Div(
                [html.Div(v, className="kpi-val"), html.Div(k, className="kpi-lbl")],
                className="kpi",
            )
            for k, v in cards
        ],
        className="kpi-row",
    )


def graph(fig_or_id, **kw):
    return dcc.Graph(figure=fig_or_id, config={"displayModeBar": False}, **kw)


# Tabs are built on first view, not at import, and cached after that. Building
# them eagerly meant every figure had to succeed before the server would even
# start, so a single bad figure took the whole dashboard down -- and startup
# paid for charts the user might never open.
def _row(*cols):
    return html.Div([html.Div(c, className="col") for c in cols], className="row")


@cache
def tab_overview():
    return html.Div(
        [
            kpi_cards(),
            graph(fig_calendar()),
            _row(graph(fig_weekly_mileage()), graph(fig_cumulative_ytd())),
            graph(fig_dow_hour()),
        ]
    )


@cache
def tab_performance():
    return html.Div(
        [
            graph(fig_pace_over_time()),
            _row(graph(fig_pace_vs_distance()), graph(fig_best_efforts())),
        ]
    )


@cache
def tab_geography():
    return html.Div([graph(fig_heatmap_map())])


@cache
def tab_physiology():
    return html.Div([_row(graph(fig_hr_zones()), graph(fig_cadence_vs_pace()))])


@cache
def tab_advanced():
    return html.Div(
        [
            graph(fig_gap_vs_actual()),
            _row(graph(fig_decoupling()), graph(fig_pace_at_hr())),
        ]
    )


@cache
def tab_detail():
    """Run picker only; the body below it is filled in by render_run_detail."""
    options = [
        {"label": f"{r.date} — {r.name} ({r.distance_km:.1f} km)", "value": r.id}
        for r in RUNS.itertuples()
    ]
    return html.Div(
        [
            dcc.Dropdown(
                id="run-picker",
                options=options,
                # RUNS is sorted oldest-first, so the last option is the newest run.
                value=(options[-1]["value"] if options else None),
                placeholder="Pick a run",
                clearable=False,
                style={"maxWidth": "640px", "margin": "1rem 0"},
            ),
            html.Div(id="run-detail-body"),
        ]
    )


app.layout = html.Div(
    [
        html.H1("🏃 Strava Explorer", className="title"),
        dcc.Tabs(
            id="tabs",
            value="overview",
            children=[
                dcc.Tab(label="Overview", value="overview"),
                dcc.Tab(label="Performance", value="performance"),
                dcc.Tab(label="Geography", value="geography"),
                dcc.Tab(label="Physiology", value="physiology"),
                dcc.Tab(label="Advanced", value="advanced"),
                dcc.Tab(label="Run detail", value="detail"),
            ],
        ),
        html.Div(id="tab-content", style={"padding": "1rem"}),
    ],
    className="app",
)

_TABS = {
    "overview": tab_overview,
    "performance": tab_performance,
    "geography": tab_geography,
    "physiology": tab_physiology,
    "advanced": tab_advanced,
    "detail": tab_detail,
}


@app.callback(Output("tab-content", "children"), Input("tabs", "value"))
def render_tab(tab):
    builder = _TABS.get(tab, tab_overview)
    try:
        return builder()
    except Exception as exc:
        # Tabs build lazily, so one bad figure only costs its own tab -- but the
        # default is Dash's error overlay, which says nothing about which chart
        # or what to do. Name it and leave the rest of the app usable.
        return html.Div(
            [
                html.P(
                    f"This tab could not be built: {type(exc).__name__}: {exc}",
                    style={"fontWeight": "700", "color": ERROR_COLOR},
                ),
                html.P("The other tabs are unaffected."),
            ],
            style={"padding": "2rem"},
        )


@app.callback(Output("run-detail-body", "children"), Input("run-picker", "value"))
def render_run_detail(run_id):
    if run_id is None:
        return html.Div("No run selected.")
    track = dl.run_track(run_id)
    splits = dl.load_splits_df(run_id)

    # Route map colored by pace.
    if not track.empty:
        route = px.scatter_map(
            track,
            lat="lat",
            lon="lon",
            color="pace_min_km",
            color_continuous_scale="Viridis_r",
            zoom=12,
        )
        route.update_layout(
            map_style="open-street-map",
            height=440,
            map=dict(center=dict(lat=track["lat"].mean(), lon=track["lon"].mean())),
            margin=dict(l=0, r=0, t=30, b=0),
            title="Route (colored by pace)",
            coloraxis_colorbar_title="min/km",
        )
        elev = px.area(track, x="distance_km", y="altitude_m")
        elev.update_layout(
            template=TEMPLATE,
            height=240,
            title="Elevation profile",
            xaxis_title="km",
            yaxis_title="m",
        )
        route_g, elev_g = graph(route), graph(elev)
    else:
        route_g = graph(_empty_fig("No GPS stream for this run"))
        elev_g = graph(_empty_fig("No elevation stream"))

    if not splits.empty:
        sp = px.bar(
            splits, x="split", y="pace_min_km", color="avg_hr", color_continuous_scale="Turbo"
        )
        sp.update_yaxes(autorange="reversed", title="pace (min/km)")
        sp.update_layout(
            template=TEMPLATE,
            height=280,
            title="Per-km splits",
            xaxis_title="km",
            coloraxis_colorbar_title="HR",
        )
        splits_g = graph(sp)
    else:
        splits_g = graph(_empty_fig("No split data"))

    return html.Div([route_g, elev_g, splits_g])


app.index_string = """<!DOCTYPE html>
<html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background:#fafafa; }
  .app { max-width: 1200px; margin: 0 auto; }
  .title { padding: 1rem; color:#fc4c02; }
  .kpi-row { display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }
  .kpi { flex:1; min-width:140px; background:#fff; border:1px solid #eee;
         border-radius:12px; padding:1rem; text-align:center; }
  .kpi-val { font-size:1.6rem; font-weight:700; color:#222; }
  .kpi-lbl { color:#888; font-size:.85rem; margin-top:.25rem; }
  .row { display:flex; gap:1rem; flex-wrap:wrap; }
  .col { flex:1; min-width:380px; }
</style>
</head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>"""


if __name__ == "__main__":
    # 8050 is Dash's default and may be taken by another local dashboard. Stash
    # the choice in the environment so the debug reloader's child process
    # inherits the same port instead of scanning again and picking a second one.
    # (setdefault would still call find_free_port in the child, since Python
    # evaluates the default eagerly -- hence the explicit check.)
    port = os.environ.get("STRAVA_EXPLORER_PORT")
    if not port:
        port = str(find_free_port())
        os.environ["STRAVA_EXPLORER_PORT"] = port
    # Opt-in: debug mode serves Werkzeug's interactive traceback console, which
    # executes arbitrary Python from the browser. Handy while editing figures,
    # not something to leave on by default.
    debug = os.environ.get("STRAVA_EXPLORER_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(debug=debug, port=int(port))
