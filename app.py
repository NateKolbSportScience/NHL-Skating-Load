"""NHL Skating Load: real NHL games replayed on a rink, then GPS-style post-game reports,
built on real play-by-play, real shift charts and real NHL EDGE skating profiles.

Run:  shiny run app.py   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from shiny import App, reactive, render, ui
from shinywidgets import output_widget, render_plotly

import data
import nhl_api
import replay
import validate
from replay import mmss_to_s
from sim import (ACC_MS2, HSD_KMH, MPH, SPRINT_KMH, ZONES, by_period, game_summary, simulate_game,
                 simulate_real_shifts, vs_baseline, zone_totals)

# ------------------------------------------------------------------ look
BG, CARD, LINE = "#2F3A2A", "#38452F", "#4B5A40"
TEXT, TEXT2, MUTED, ACCENT = "#F1EEE2", "#BDBBA6", "#8E9081", "#E3C770"
S1, S2, S3 = "#B58C1F", "#4A92D0", "#D66450"           # validated categorical slots (dark, olive surface)
STRENGTH_COL = {"ES": S1, "PP": S2, "PK": S3}
ZONE_RAMP = ["#5E6040", "#6B6834", "#8B8231", "#AD9B33", "#CDB547", "#EBD27A"]  # sequential, one hue
FLAG = {"Typical": ("#6CC28A", "✓"), "Above": ("#E3B341", "▲"), "Below": ("#E3B341", "▼"),
        "High": ("#F07167", "▲▲"), "Low": ("#F07167", "▼▼"), "n/a": (MUTED, "–")}
SNAP = data.load()
DEFAULT_TEAM = "CGY"                            # team whose real games ship with the app (works offline)
GAMES_DF = replay.bundled_games(DEFAULT_TEAM)
LATEST_GAME = int(GAMES_DF["id"].iloc[0])


def games_for(team: str, season: str) -> list[dict]:
    """A team's completed games in a season: live from the NHL API, or the games saved in data/games if offline."""
    try:
        return nhl_api.schedule(team, season)
    except Exception:
        df = replay.bundled_games(team)
        if len(df):
            df = df[df["id"].astype(str).str[:4] == season[:4]]
        return df.to_dict("records") if len(df) else []


def game_choices(team: str, season: str) -> dict:
    ch = {str(g["id"]): g["label"] + ("" if nhl_api.is_cached(g["id"]) else "  ⤓") for g in games_for(team, season)}
    ch["sim"] = "Simulated game"
    return ch


def season_label(s: str) -> str:
    return f"{s[:4]}-{s[6:]}"


def focus_team(gid: int, team: str) -> str:
    G = replay.parse(gid)
    return team if team in (G["home"]["abbrev"], G["away"]["abbrev"]) else G["home"]["abbrev"]


# ------------------------------------------------------------------ real-game helpers
@lru_cache(maxsize=16)
def replay_json(gid: int, focus: str) -> str:
    r = dict(replay.build_replay(gid))
    r["focus"] = focus
    return json.dumps(r, separators=(",", ":"), default=float)


def real_game_info(gid: int, team: str):
    """(opponent, is_home, date) for a team in a real game, or None if the team didn't play in it."""
    G = replay.parse(gid)
    h, a = G["home"]["abbrev"], G["away"]["abbrev"]
    if team not in (h, a):
        return None
    return (a if team == h else h), team == h, G["pbp"]["gameDate"]


def players_for(team: str, gid: int | None) -> dict:
    """Player choices: in a real game, the team's skaters who played (by TOI); otherwise the full roster."""
    if gid and real_game_info(gid, team):
        G = replay.parse(gid)
        sh = G["shifts"]
        toi = (sh["t1"] - sh["t0"]).groupby(sh["pid"]).sum()
        known = set(data.table()["id"])
        pids = [p for p in toi.sort_values(ascending=False).index
                if G["players"][p]["team"] == team and G["players"][p]["pos"] != "G" and p in known]
        return {str(p): f"{G['players'][p]['name']} · {G['players'][p]['pos']} · #{G['players'][p]['num']}" for p in pids}
    return data.players(team)


def real_shifts_game(gid: int, pid: int):
    """Simulated skating load on a player's REAL shifts in a real game (None if he didn't play)."""
    G = replay.parse(gid)
    if pid not in G["players"] or pid not in set(G["shifts"]["pid"]) or pid not in set(data.table()["id"]):
        return None
    pr = data.profile(pid)
    opp, home, _ = real_game_info(gid, G["players"][pid]["team"])
    return simulate_real_shifts(pr, replay.player_shifts(gid, pid), seed=gid % 100000 + pid % 997, opp=opp, home=home, game_id=gid)


SEASON = f"{SNAP['season'][:4]}-{SNAP['season'][6:]}"

CSS = f"""
:root {{ --bs-body-bg:{BG}; --bs-body-color:{TEXT}; }}
body {{ background:{BG}; color:{TEXT}; font-family: 'Segoe UI', system-ui, sans-serif; }}
.navbar {{ background:{BG} !important; border-bottom:1px solid {LINE}; }}
.navbar-brand {{ color:{ACCENT} !important; font-weight:800; letter-spacing:.04em; }}
.nav-link {{ color:{TEXT2} !important; }} .nav-link.active {{ color:{ACCENT} !important; border-bottom:2px solid {ACCENT}; }}
.bslib-sidebar-layout > .sidebar {{ background:{CARD}; color:{TEXT}; border-right:1px solid {LINE}; }}
.form-control, .form-select, .selectize-input {{ background:{BG} !important; color:{TEXT} !important; border-color:{LINE} !important; }}
.selectize-dropdown, .selectize-dropdown .option {{ background:{BG}; color:{TEXT}; }}
.selectize-dropdown .active {{ background:{LINE}; color:{TEXT}; }}
.card {{ background:{CARD}; border:1px solid {LINE}; border-radius:10px; color:{TEXT}; }}
.card-header {{ background:transparent; color:{ACCENT}; font-weight:700; border-bottom:1px solid {LINE}; }}
.btn-sim {{ background:{ACCENT}; color:{BG}; font-weight:700; border:none; width:100%; }}
.btn-sim:hover {{ background:#F0D98C; color:{BG}; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(125px,1fr)); gap:10px; margin-bottom:12px; }}
.tile {{ background:{CARD}; border:1px solid {LINE}; border-radius:10px; padding:10px 12px; }}
.tile .lab {{ color:{TEXT2}; font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; }}
.tile .val {{ font-size:1.35rem; white-space:nowrap; font-weight:800; line-height:1.15; color:{TEXT}; }}
.tile .sub {{ color:{MUTED}; font-size:.75rem; }}
.hdr {{ display:flex; align-items:center; gap:16px; margin:4px 0 14px; }}
.hdr img {{ width:78px; height:78px; border-radius:50%; border:3px solid {ACCENT}; background:{CARD}; object-fit:cover; }}
.hdr .nm {{ font-size:1.6rem; font-weight:800; color:{ACCENT}; line-height:1.1; }}
.hdr .meta {{ color:{TEXT2}; }}
.sim-tag {{ display:inline-block; font-size:.7rem; padding:2px 8px; border-radius:20px; border:1px solid {ACCENT}; color:{ACCENT}; margin-left:6px; vertical-align:middle; }}
.real-tag {{ display:inline-block; font-size:.7rem; padding:2px 8px; border-radius:20px; border:1px solid #6CC28A; color:#6CC28A; margin-left:6px; vertical-align:middle; }}
table.gps {{ width:100%; border-collapse:collapse; font-size:.86rem; }}
table.gps th {{ color:{TEXT2}; font-weight:600; text-align:right; padding:6px 8px; border-bottom:1px solid {LINE}; white-space:nowrap; }}
table.gps td {{ text-align:right; padding:5px 8px; border-bottom:1px solid {LINE}55; white-space:nowrap; }}
table.gps th:first-child, table.gps td:first-child {{ text-align:left; }}
table.gps tr:hover td {{ background:{LINE}66; }}
.flag {{ font-weight:700; }}
.note {{ color:{MUTED}; font-size:.78rem; }}
.about h4 {{ color:{ACCENT}; margin-top:18px; }} .about p, .about li {{ color:{TEXT2}; }}
a {{ color:{ACCENT}; }}
.rp-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:6px 0 8px; }}
.rp-title {{ font-size:1.25rem; font-weight:800; color:{ACCENT}; }}
.rp-board {{ display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; background:{CARD}; border:1px solid {LINE};
            border-radius:10px; padding:8px 14px; margin-bottom:10px; }}
.rp-team-big {{ font-size:1.6rem; font-weight:800; }} .rp-score {{ font-size:1.9rem; font-weight:800; color:{TEXT}; }}
.rp-clock {{ font-size:1.2rem; font-weight:700; color:{ACCENT}; margin-left:auto; font-variant-numeric:tabular-nums; }}
.rp-sit {{ color:{TEXT2}; font-size:.85rem; }}
#rp-canvas-wrap {{ width:100%; background:{CARD}; border:1px solid {LINE}; border-radius:12px; padding:8px; }}
#rp-canvas {{ display:block; cursor:pointer; }}
.rp-legend {{ color:{TEXT2}; font-size:.8rem; margin:8px 0 2px; }} .rp-legend b {{ color:{TEXT}; }}
.rp-controls {{ display:flex; align-items:center; gap:10px; margin-top:10px; flex-wrap:wrap; }}
.rp-controls button, .rp-controls select {{ background:{BG}; color:{TEXT}; border:1px solid {LINE}; border-radius:8px; padding:6px 12px; font-weight:600; }}
.rp-controls button.primary {{ background:{ACCENT}; color:{BG}; border:none; }}
.rp-controls input[type=range] {{ flex:1; min-width:180px; accent-color:{ACCENT}; }}
.rp-tick-card {{ background:{CARD}; border:1px solid {LINE}; border-radius:12px; padding:10px 12px; height:100%; }}
.rp-tick-h {{ color:{ACCENT}; font-weight:700; margin-bottom:6px; }}
.rp-ev {{ display:flex; gap:6px; align-items:baseline; font-size:.8rem; color:{TEXT2}; padding:4px 0; border-bottom:1px solid {LINE}66; }}
.rp-ev.rp-goal {{ color:{ACCENT}; font-weight:700; }}
.rp-dot {{ width:8px; height:8px; border-radius:50%; flex:none; display:inline-block; }}
.rp-when {{ color:{MUTED}; white-space:nowrap; font-variant-numeric:tabular-nums; }} .rp-team {{ font-weight:700; color:{TEXT}; }}
.legend-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin:0 4px 0 10px; vertical-align:middle; }}
.final {{ display:flex; align-items:center; gap:18px; margin:4px 0 14px; flex-wrap:wrap; }}
.final img {{ width:64px; height:64px; }} .final .sc {{ font-size:2.4rem; font-weight:800; }}
.final .lab {{ color:{TEXT2}; }}
.btn-ghost {{ background:transparent; color:{ACCENT}; border:1px solid {ACCENT}; border-radius:8px; padding:6px 14px; font-weight:700; }}
#rp-main {{ display:grid; grid-template-columns:minmax(0,3fr) minmax(0,1fr); gap:16px; align-items:start; }}
/* ---------- 8-bit mode (replay tab) ---------- */
.rp-toggle {{ color:{TEXT2}; font-size:.85rem; display:flex; align-items:center; gap:6px; cursor:pointer; }}
.rp-toggle input {{ accent-color:{ACCENT}; width:16px; height:16px; }}
#rp-stage.retro, #rp-stage.retro button, #rp-stage.retro select, #rp-stage.retro .rp-toggle {{ font-family:'Press Start 2P', monospace; }}
#rp-stage.retro .rp-title {{ font-size:.85rem; line-height:1.6; letter-spacing:.02em; }}
#rp-stage.retro .rp-board {{ background:#0E120C; border:4px solid #0A0D08; border-radius:0;
    box-shadow:0 0 0 4px {LINE}, 0 6px 0 4px #1C2318; padding:12px 18px; }}
#rp-stage.retro .rp-team-big {{ font-size:1.25rem; text-shadow:3px 3px 0 #000; }}
#rp-stage.retro .rp-score {{ font-size:1.6rem; color:#F6D45B; text-shadow:0 0 8px rgba(246,212,91,.55), 3px 3px 0 #000; letter-spacing:.1em; }}
#rp-stage.retro .rp-clock {{ font-size:1rem; color:#FF5A3C; text-shadow:0 0 6px rgba(255,90,60,.6); }}
#rp-stage.retro .rp-sit {{ font-size:.55rem; color:#9FE870; }}
#rp-stage.retro #rp-canvas-wrap {{ border-radius:0; border:4px solid #0A0D08; box-shadow:0 0 0 4px {LINE}; position:relative; background:#0E120C; }}
#rp-stage.retro #rp-canvas {{ image-rendering:pixelated; }}
#rp-stage.retro #rp-canvas-wrap::after {{ content:""; position:absolute; inset:8px; pointer-events:none;
    background:repeating-linear-gradient(0deg, rgba(0,0,0,.10) 0 1px, transparent 1px 3px); }}
#rp-stage.retro .rp-controls button, #rp-stage.retro .rp-controls select {{ border-radius:0; font-size:.6rem; padding:9px 10px;
    border:3px solid #0A0D08; box-shadow:3px 3px 0 #0A0D08; }}
#rp-stage.retro .rp-controls button:active {{ transform:translate(2px,2px); box-shadow:1px 1px 0 #0A0D08; }}
#rp-stage.retro .rp-controls .rp-toggle {{ font-size:.55rem; }}
#rp-stage.retro .rp-tick-card {{ border-radius:0; border:4px solid #0A0D08; box-shadow:0 0 0 4px {LINE}; background:#0E120C; }}
#rp-stage.retro .rp-tick-h {{ font-family:'Press Start 2P', monospace; font-size:.65rem; color:#F6D45B; }}
#rp-stage.retro .rp-ev {{ font-family:'Press Start 2P', monospace; font-size:.5rem; line-height:1.7; color:#CFE8C0; }}
#rp-stage.retro .rp-ev.rp-goal {{ color:#F6D45B; animation: blink 1s steps(2) 3; }}
#rp-stage.retro .rp-dot {{ border-radius:0; }}
#rp-stage.retro .rp-legend {{ font-family:'Press Start 2P', monospace; font-size:.5rem; line-height:2; }}
#rp-stage.retro .legend-dot {{ border-radius:0; }}
#rp-stage.retro .note {{ font-family:'Press Start 2P', monospace; font-size:.48rem; line-height:1.9; }}
@keyframes blink {{ 50% {{ opacity:.2; }} }}
@keyframes goalflash {{ 0%,100% {{ box-shadow:0 0 0 4px {LINE}; }} 50% {{ box-shadow:0 0 0 4px #F6D45B, 0 0 28px 6px rgba(246,212,91,.55); }} }}
.rp-board.goal-flash {{ animation: goalflash .35s steps(2) 9; }}
.rp-board.goal-flash .rp-score {{ animation: blink .35s steps(2) 9; }}
"""


def fig_base(fig: go.Figure, h: int = 300, **kw) -> go.Figure:
    fig.update_layout(height=h, margin=dict(l=48, r=16, t=28, b=40), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT2, size=12),
                      hoverlabel=dict(bgcolor=BG, font_color=TEXT, bordercolor=LINE),
                      legend=dict(orientation="h", y=1.12, x=0, font=dict(color=TEXT2)), **kw)
    fig.update_xaxes(gridcolor="rgba(241,238,226,0.08)", zeroline=False, linecolor=LINE)
    fig.update_yaxes(gridcolor="rgba(241,238,226,0.10)", zeroline=False, linecolor=LINE)
    return fig


def tile(label, value, sub=""):
    return ui.div(ui.div(label, class_="lab"), ui.div(value, class_="val"), ui.div(sub, class_="sub"), class_="tile")


def mmss(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def headshot(p):
    return data.headshot(p.id)


def rink_shapes(fig):
    """Draw an NHL rink (200 x 85 ft) as plotly shapes in data coordinates."""
    ice, red, blue = "#F4F6F3", "#C8102E", "#1F4E99"
    path = "M -72,-42.5 L 72,-42.5 Q 100,-42.5 100,-14.5 L 100,14.5 Q 100,42.5 72,42.5 L -72,42.5 Q -100,42.5 -100,14.5 L -100,-14.5 Q -100,-42.5 -72,-42.5 Z"
    fig.add_shape(type="path", path=path, fillcolor=ice, line=dict(color="#5B6650", width=2), layer="below")
    for x, c, w in ((0, red, 3), (-25, blue, 3), (25, blue, 3), (-89, red, 1), (89, red, 1)):
        fig.add_shape(type="line", x0=x, x1=x, y0=-42.5 if abs(x) < 80 else -36, y1=42.5 if abs(x) < 80 else 36, line=dict(color=c, width=w), layer="below")
    for cx, cy in ((0, 0), (-69, 22), (-69, -22), (69, 22), (69, -22)):
        fig.add_shape(type="circle", x0=cx - 15, x1=cx + 15, y0=cy - 15, y1=cy + 15, line=dict(color=blue if cx == 0 else red, width=1), layer="below")
    for s in (-1, 1):
        fig.add_shape(type="rect", x0=s * 89, x1=s * 92.3, y0=-3, y1=3, line=dict(color=red, width=1), layer="below")


def flag_html(f):
    c, icon = FLAG[f]
    return f'<span class="flag" style="color:{c}">{icon} {f}</span>'


# ------------------------------------------------------------------ UI
teams = data.teams()
default_team = DEFAULT_TEAM if DEFAULT_TEAM in teams else teams[0]
start_choices = {str(r.id): r.label for r in GAMES_DF.itertuples()}
start_choices["sim"] = "Simulated game"

sidebar = ui.sidebar(
    ui.input_select("team", "Team", {t: t for t in teams}, selected=default_team),
    ui.input_select("player", "Player", players_for(default_team, LATEST_GAME)),
    ui.input_select("season", "Season", {"20252026": "2025-26"}, selected="20252026"),
    ui.input_select("game_src", "Game", start_choices, selected=str(LATEST_GAME)),
    ui.p("Every completed game for the selected team and season, regular season and playoffs, updated live from the NHL. "
         "Games marked ⤓ download the first time you open them (a few seconds); Calgary's last 10 of 2025-26 are saved for offline use.",
         class_="note"),
    ui.input_action_button("simulate", "▶  Simulate game", class_="btn-sim"),
    ui.input_numeric("seed", "Game seed", value=8, min=1, step=1),
    ui.p("The same seed always gives the same game, so a report can be reproduced.", class_="note"),
    ui.hr(),
    ui.p(ui.span("Real", class_="real-tag"), " NHL EDGE season profile, ", SEASON, " regular season.", class_="note"),
    ui.p(ui.span("Simulated", class_="sim-tag"), " shift-by-shift tracking, calibrated to that profile.", class_="note"),
    width=260, bg=CARD,
)

replay_tab = ui.nav_panel(
    "Game replay",
    ui.div(
        ui.div(ui.div(id="rp-title", class_="rp-title"),
               ui.span("Real events · real line changes", class_="real-tag"),
               ui.span("Skater movement between events estimated", class_="sim-tag"), class_="rp-head"),
        ui.div(ui.span(id="rp-away", class_="rp-team-big"), ui.span(id="rp-score", class_="rp-score"),
               ui.span(id="rp-home", class_="rp-team-big"), ui.span(id="rp-sit", class_="rp-sit"),
               ui.span(id="rp-clock", class_="rp-clock"), class_="rp-board"),
        ui.div(
            ui.div(
                ui.div(ui.tags.canvas(id="rp-canvas"), id="rp-canvas-wrap"),
                ui.div(
                    ui.tags.button("▶ PLAY", id="rp-play", class_="primary"),
                    ui.tags.select(*[ui.tags.option(f"{v}× speed", value=str(v), selected=(v == 10)) for v in (1, 5, 10, 30, 60, 120, 240)], id="rp-speed"),
                    ui.tags.input(type="range", id="rp-scrub", min="0", max="3600", step="1", value="0"),
                    ui.tags.button("⟲ Restart", id="rp-restart"),
                    ui.tags.button("Skip to stats ▸", id="rp-skip"),
                    ui.tags.label(ui.tags.input(type="checkbox", id="rp-retro", checked=""), " 8-bit", class_="rp-toggle"),
                    class_="rp-controls"),
                ui.HTML("<div class='rp-legend'><b>Speed vectors</b> (direction of travel, estimated): "
                        "<span class='legend-dot' style='background:#1B1F1A'></span>under 20 km/h"
                        "<span class='legend-dot' style='background:#2E9E5B'></span>20–29 km/h"
                        "<span class='legend-dot' style='background:#D7263D'></span>high-speed, 29+ km/h (18+ mph)"
                        " · <b>Click a player</b> to follow him: he's drawn bigger with his face card and live speed, and everyone else fades back."
                        " The sidebar Player follows too.</div>"),
                ui.p("Players appear and leave with their real shifts. Every shot, hit, faceoff, takeaway, giveaway, block, penalty and goal "
                     "is placed at its real rink location and game time; ★ = goal, dots = shot attempts. The puck path and skater positions "
                     "between events are estimated from each player's role, which team has the puck, and where it is.", class_="note"),
            ),
            ui.div(
                ui.div(ui.div("Play by play", class_="rp-tick-h"), ui.div(id="rp-ticker"), class_="rp-tick-card", id="rp-side-ticker"),
                class_="rp-right"),
            id="rp-main",
        ),
        id="rp-stage",
    ),
    ui.div(ui.output_ui("game_stats"), id="rp-stats", style="display:none"),
)

report_tab = ui.nav_panel(
    "Post-game report",
    ui.output_ui("hdr"),
    ui.output_ui("tiles"),
    ui.layout_columns(
        ui.card(ui.card_header("Shift by shift ", ui.span("Simulated", class_="sim-tag")), output_widget("shift_chart"),
                ui.p("Distance per shift, coloured by game state. Hover a bar for shift length, rest before it, top speed and bursts.", class_="note")),
        col_widths=[12],
    ),
    ui.layout_columns(
        ui.card(ui.card_header("By period"), ui.output_ui("period_table"),
                ui.p("m/min = distance per minute on ice (intensity). Bursts are counted in NHL EDGE's bands: 18–20, 20–22 and 22+ mph.", class_="note")),
        col_widths=[12],
    ),
    ui.layout_columns(
        ui.card(ui.card_header("Distance by speed zone"), output_widget("zone_chart")),
        ui.card(ui.card_header("Speed trace"), ui.output_ui("shift_pick"), output_widget("trace_chart")),
        col_widths=[5, 7],
    ),
    ui.layout_columns(
        ui.card(ui.card_header("Game vs baseline ", ui.span("Real", class_="real-tag")), ui.output_ui("baseline_table"),
                ui.p("Season = the player's real 2025-26 per-game average. SD = between-game SD from his real last 10 games. "
                     "SWC (smallest worthwhile change) = 0.2 × SD. Typical = within SWC; ▲/▼ beyond SWC; ▲▲/▼▼ beyond 1 SD.", class_="note")),
        ui.card(ui.card_header("Distance: this game vs his last 10 real games"), output_widget("baseline_chart")),
        col_widths=[6, 6],
    ),
)

team_tab = ui.nav_panel(
    "Team session report",
    ui.output_ui("team_hdr"),
    ui.card(ui.card_header("Team GPS report ", ui.span("Estimated skating load", class_="sim-tag")), ui.output_ui("team_table"),
            ui.p("Real game: the real lineup, with each skater's real shifts, TOI and game state. Simulated game: a typical dressed lineup "
                 "(top 12 F and 6 D by TOI). Either way, each player's speed traces are estimated from his own real EDGE profile. "
                 "Flag compares distance with his own season average (SWC = 0.2 × between-game SD).", class_="note")),
    ui.card(ui.card_header("High-speed distance by player"), output_widget("team_chart")),
)

profile_tab = ui.nav_panel(
    "Season profile",
    ui.output_ui("prof_hdr"),
    ui.output_ui("prof_tiles"),
    ui.layout_columns(
        ui.card(ui.card_header("Last 10 games: distance (km) ", ui.span("Real", class_="real-tag")), output_widget("l10_dist")),
        ui.card(ui.card_header("Last 10 games: time on ice (min) ", ui.span("Real", class_="real-tag")), output_widget("l10_toi")),
        col_widths=[6, 6],
    ),
    ui.layout_columns(
        ui.card(ui.card_header("League context: distance per 60 vs bursts per 60 ", ui.span("Real", class_="real-tag")), output_widget("league_scatter")),
        ui.card(ui.card_header("Top 10 skating speeds ", ui.span("Real", class_="real-tag")), ui.output_ui("top_speeds")),
        col_widths=[7, 5],
    ),
)

validation_tab = ui.nav_panel(
    "Validation",
    ui.div(ui.div("How accurate are the estimates?", class_="nm"),
           ui.div("The NHL's own tracking system records every skater's real distance for his last 10 games. Here, each of those "
                  "real numbers is compared with what this app estimated for the same player in the same game.", class_="meta"),
           ui.span("Real NHL tracking vs estimated", class_="real-tag"), class_="hdr", style="display:block"),
    ui.output_ui("val_tiles"),
    ui.layout_columns(
        ui.card(ui.card_header("Estimated vs real distance per player-game"), output_widget("val_scatter"),
                ui.p("Each dot is one player in one game. On the dashed line = a perfect estimate.", class_="note")),
        ui.card(ui.card_header("Bland-Altman agreement"), output_widget("val_ba"),
                ui.p("Difference (estimated − real) against the average of the two. Solid line = mean bias; dashed = 95% limits of agreement.", class_="note")),
        col_widths=[6, 6],
    ),
    ui.card(ui.card_header("What this does and doesn't show"),
            ui.HTML("<ul class='note' style='margin:0;padding-left:18px'>"
                    "<li><b>What it shows:</b> a player's real shifts from a game, combined with his season skating intensity from NHL EDGE, "
                    "predict how far he really skated that night to within about 5%.</li>"
                    "<li><b>What it doesn't show:</b> that the estimate tracks how hard he skated <i>that specific night</i>. Each player's "
                    "season distance per 60 already includes these games, and most of the night-to-night difference comes from ice time, which is real. "
                    "Speeds, bursts and on-ice positions can't be checked: the NHL doesn't publish them per game.</li>"
                    "<li><b>Replay movement</b> was tuned once (one drift setting for the whole league) so the replay's average distance matches real "
                    "tracking. It isn't tuned per player.</li></ul>")),
    ui.layout_columns(
        ui.card(ui.card_header("By game"), ui.output_ui("val_games")),
        ui.card(ui.card_header("Selected game, player by player"), ui.output_ui("val_players")),
        col_widths=[5, 7],
    ),
)

about_tab = ui.nav_panel(
    "About",
    ui.div(ui.markdown(open(data.Path(__file__).parent / "ABOUT.md", encoding="utf-8").read()), class_="about"),
)

app_ui = ui.page_navbar(
    replay_tab, report_tab, team_tab, profile_tab, validation_tab, about_tab,
    title="NHL SKATING LOAD", sidebar=sidebar, fillable=False, id="nav",
    header=ui.TagList(
        ui.tags.link(rel="stylesheet", href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap"),
        ui.tags.link(rel="icon", type="image/png", href="favicon.png"),
        ui.tags.style(CSS), ui.tags.script(src="replay.js")), window_title="NHL Skating Load",
)



# ------------------------------------------------------------------ server
def server(input, output, session):
    @reactive.effect
    @reactive.event(input.team, input.game_src)
    def _players():
        choices = players_for(input.team(), real_gid())
        cur = input.player()
        ui.update_select("player", choices=choices, selected=cur if cur in choices else next(iter(choices), None))

    @reactive.effect
    @reactive.event(input.simulate)
    def _new_seed():
        ui.update_numeric("seed", value=int(np.random.default_rng().integers(1, 100000)))

    @reactive.calc
    def prof():
        pid = input.player()
        if not pid or int(pid) not in set(data.table()["id"]):
            pid = next(iter(data.players(input.team())))
        return data.profile(int(pid))

    def opponent(team, seed):
        rng = np.random.default_rng(seed + 7)
        others = [t for t in teams if t != team]
        return others[rng.integers(len(others))], bool(rng.integers(2))

    @reactive.calc
    def real_gid():
        """Selected real game, downloaded from the NHL API first if it isn't saved yet (None = simulated)."""
        src = input.game_src()
        if not src or src == "sim":
            return None
        gid = int(src)
        if not nhl_api.is_cached(gid):
            nid = ui.notification_show("Loading the game from the NHL API…", duration=None, type="message")
            try:
                nhl_api.fetch_game(gid)
            except Exception as e:
                ui.notification_show(f"Couldn't load that game ({e}). Showing a simulated game instead.", type="error", duration=8)
                return None
            finally:
                ui.notification_remove(nid)
        return gid

    @reactive.effect
    def _seasons():
        cur = nhl_api.current_season()
        prev = nhl_api.previous_season(cur)
        # start on the current season once the team has played in it, otherwise last season
        start = cur if games_for(DEFAULT_TEAM, cur) else prev
        ui.update_select("season", choices={cur: season_label(cur), prev: season_label(prev)}, selected=start)

    @reactive.effect
    @reactive.event(input.team, input.season)
    def _games():
        ch = game_choices(input.team(), input.season())
        cur = input.game_src()
        first_real = next((k for k in ch if k != "sim"), "sim")
        ui.update_select("game_src", choices=ch, selected=cur if cur in ch and cur != "sim" else first_real)

    @reactive.calc
    def game():
        p = prof()
        gid = real_gid()
        if gid:
            g = real_shifts_game(gid, p.id)
            if g is not None:
                return g
        seed = int(input.seed() or 1)
        opp, home = opponent(p.team, seed)
        return simulate_game(p, seed=seed * 1000 + p.id % 997, opp=opp, home=home)

    @reactive.calc
    def summ():
        return game_summary(game())

    # ---------------- report: header + tiles
    @render.ui
    def hdr():
        g, p = game(), prof()
        where = f"vs {g.opp}" if g.home else f"@ {g.opp}"
        if g.source == "real shifts":
            date = replay.parse(g.game_id)["pbp"]["gameDate"]
            meta = f"#{p.num} · {p.pos} · {p.team}  {where} · {date}"
            tags = [ui.span("Real game: real shifts, TOI & game state", class_="real-tag"),
                    ui.span("Speed trace estimated from his EDGE profile", class_="sim-tag")]
        else:
            note = ""
            if real_gid():
                note = " (didn't play in the selected game, so this is a simulated game)"
            meta = f"#{p.num} · {p.pos} · {p.team}  {where} · game seed {input.seed()}{note}"
            tags = [ui.span("Simulated game", class_="sim-tag"), ui.span(f"Profile: real EDGE {SEASON}", class_="real-tag")]
        return ui.div(
            ui.tags.img(src=headshot(p), alt=p.name),
            ui.div(ui.div(p.name, class_="nm"), ui.div(meta, class_="meta"), *tags),
            class_="hdr")

    @render.ui
    def tiles():
        s, p = summ(), prof()
        return ui.div(
            tile("Time on ice", mmss(s["toi_s"]), f"{s['shifts']} shifts · avg {s['avg_shift_s']:.0f} s"),
            tile("Distance", f"{s['dist_km']:.2f} km", f"season avg {p.expected_km:.2f} km"),
            tile("Intensity", f"{s['m_per_min']:.0f} m/min", f"{s['km_per60']:.1f} km per 60"),
            tile("High-speed", f"{s['hsd_m']:.0f} m", "> 29 km/h (18 mph)"),
            tile("Sprint", f"{s['sprint_m']:.0f} m", "> 35 km/h (22 mph)"),
            tile("Bursts", f"{s['bursts']}", f"{s['b18']} · {s['b20']} · {s['b22']} by band"),
            tile("Max speed", f"{s['max_kmh']:.1f} km/h", f"{s['pct_vmax']:.0%} of season max"),
            tile("Accel / Decel", f"{s['acc']} / {s['dec']}", f"≥ {ACC_MS2:g} m/s² over 1 s"),
            class_="tiles")

    # ---------------- shift chart
    @render_plotly
    def shift_chart():
        sh = game().shifts
        fig = go.Figure()
        for st, col in STRENGTH_COL.items():
            d = sh[sh["strength"] == st]
            if not len(d):
                continue
            fig.add_bar(x=d["shift"], y=d["dist_m"], name=st, marker=dict(color=col, line=dict(color=CARD, width=2)),
                        customdata=np.c_[d["period"], d["length_s"], d["rest_before_s"].fillna(0), d["max_kmh"],
                                         d[["b18", "b20", "b22"]].sum(axis=1), d["m_per_min"]],
                        hovertemplate=("Shift %{x} · P%{customdata[0]}<br>%{y:.0f} m in %{customdata[1]:.0f} s "
                                       "(%{customdata[5]:.0f} m/min)<br>rest before %{customdata[2]:.0f} s<br>"
                                       "top speed %{customdata[3]:.1f} km/h · %{customdata[4]} bursts<extra>%{fullData.name}</extra>"))
        # period separators
        for per in (2, 3):
            first = sh.loc[sh["period"] == per, "shift"].min()
            if pd.notna(first):
                fig.add_vline(x=first - 0.5, line=dict(color=MUTED, dash="dot", width=1))
                fig.add_annotation(x=first - 0.5, y=1.02, yref="paper", text=f"P{per}", showarrow=False, font=dict(color=MUTED, size=11), xanchor="left")
        fig.add_annotation(x=0.5, y=1.02, yref="paper", text="P1", showarrow=False, font=dict(color=MUTED, size=11), xanchor="left")
        fig_base(fig, 300, bargap=0.15)
        fig.update_xaxes(title="Shift", dtick=5)
        fig.update_yaxes(title="Distance (m)")
        return fig

    # ---------------- period table
    @render.ui
    def period_table():
        bp = by_period(game())
        s = summ()
        rows = "".join(
            f"<tr><td>P{int(r.period)}</td><td>{mmss(r.toi_s)}</td><td>{int(r.shifts)}</td><td>{r.dist_m:,.0f}</td>"
            f"<td>{r.m_per_min:.0f}</td><td>{r.hsd_m:.0f}</td><td>{r.sprint_m:.0f}</td>"
            f"<td>{int(r.b18 + r.b20 + r.b22)}</td><td>{r.max_kmh:.1f}</td><td>{int(r.acc)} / {int(r.dec)}</td></tr>"
            for r in bp.itertuples())
        rows += (f"<tr style='font-weight:700'><td>Game</td><td>{mmss(s['toi_s'])}</td><td>{s['shifts']}</td>"
                 f"<td>{s['dist_km'] * 1000:,.0f}</td><td>{s['m_per_min']:.0f}</td><td>{s['hsd_m']:.0f}</td>"
                 f"<td>{s['sprint_m']:.0f}</td><td>{s['bursts']}</td><td>{s['max_kmh']:.1f}</td><td>{s['acc']} / {s['dec']}</td></tr>")
        return ui.HTML("<table class='gps'><tr><th>Period</th><th>TOI</th><th>Shifts</th><th>Dist (m)</th><th>m/min</th>"
                       "<th>HSD (m)</th><th>Sprint (m)</th><th>Bursts</th><th>Max km/h</th><th>Acc / Dec</th></tr>" + rows + "</table>")

    # ---------------- zone chart
    @render_plotly
    def zone_chart():
        z = zone_totals(game())
        tot = z["dist_m"].sum()
        labels = [f"{n}  ({lo:.0f}–{hi:.0f} km/h)" if hi < 90 else f"{n}  (>{lo:.0f} km/h)" for n, lo, hi in ZONES]
        fig = go.Figure(go.Bar(y=labels, x=z["dist_m"], orientation="h", marker=dict(color=ZONE_RAMP, line=dict(color=CARD, width=2)),
                               text=[f"{v:,.0f} m · {v / tot:.0%}" for v in z["dist_m"]], textposition="outside",
                               textfont=dict(color=TEXT2), cliponaxis=False,
                               hovertemplate="%{y}<br>%{x:,.0f} m<extra></extra>"))
        fig_base(fig, 290, showlegend=False)
        fig.update_layout(margin=dict(l=170, r=90, t=10, b=30))
        fig.update_yaxes(autorange="reversed", showgrid=False)
        fig.update_xaxes(title="Distance (m)")
        return fig

    # ---------------- speed trace
    @render.ui
    def shift_pick():
        sh = game().shifts
        hardest = int(sh.loc[sh["max_kmh"].idxmax(), "shift"])
        choices = {str(int(r.shift)): f"Shift {int(r.shift)} · P{int(r.period)} · {r.strength} · {r.dist_m:.0f} m · top {r.max_kmh:.1f} km/h"
                   for r in sh.itertuples()}
        return ui.input_select("shift", None, choices, selected=str(hardest), width="100%")

    @render_plotly
    def trace_chart():
        g = game()
        sh = g.shifts
        k = int(input.shift()) if input.shift() and int(input.shift()) <= len(sh) else int(sh.loc[sh["max_kmh"].idxmax(), "shift"])
        v = g.traces[k - 1]
        t = np.arange(len(v)) / 10
        fig = go.Figure(go.Scatter(x=t, y=v, mode="lines", line=dict(color=ACCENT, width=2), name="Speed",
                                   hovertemplate="%{x:.1f} s · %{y:.1f} km/h<extra></extra>"))
        for thr, lab in ((HSD_KMH, "18 mph"), (20 * MPH, "20 mph"), (SPRINT_KMH, "22 mph")):
            fig.add_hline(y=thr, line=dict(color=MUTED, dash="dot", width=1))
            fig.add_annotation(x=t[-1], y=thr, text=lab, showarrow=False, xanchor="right", yanchor="bottom", font=dict(color=MUTED, size=10))
        fig_base(fig, 250, showlegend=False)
        fig.update_xaxes(title="Seconds into shift")
        fig.update_yaxes(title="Speed (km/h)", range=[0, max(42, v.max() + 2)])
        return fig

    # ---------------- baseline
    @render.ui
    def baseline_table():
        b = vs_baseline(game())
        rows = "".join(
            f"<tr><td>{r.metric}</td><td>{r.fmt.format(r.game)}</td><td>{r.fmt.format(r.season)}</td>"
            f"<td>{'' if not np.isfinite(r.sd) else r.fmt.format(r.sd)}</td><td>{'' if not np.isfinite(r.z) else f'{r.z:+.1f}'}</td>"
            f"<td>{flag_html(r.flag)}</td></tr>" for r in b.itertuples())
        return ui.HTML("<table class='gps'><tr><th>Metric</th><th>Game</th><th>Season avg</th><th>SD</th><th>z</th><th>Flag</th></tr>" + rows + "</table>")

    @render_plotly
    def baseline_chart():
        g, p = game(), prof()
        l10 = p.last10
        s = summ()
        fig = go.Figure()
        if len(l10) >= 3:
            m, sd = l10["km"].mean(), l10["km"].std()
            fig.add_hrect(y0=m - sd, y1=m + sd, fillcolor="rgba(241,238,226,0.06)", line_width=0)
            fig.add_hrect(y0=p.expected_km - 0.2 * sd, y1=p.expected_km + 0.2 * sd, fillcolor="rgba(108,194,138,0.18)", line_width=0)
        fig.add_hline(y=p.expected_km, line=dict(color=TEXT2, dash="dash", width=1))
        fig.add_annotation(x=0, xref="paper", y=p.expected_km, text="season avg", showarrow=False, yanchor="bottom", xanchor="left", font=dict(color=TEXT2, size=10))
        labels = [f"{d:%b %d}<br>{'vs' if h else '@'} {o}" for d, o, h in zip(l10["date"], l10["opp"], l10["home"])]
        fig.add_scatter(x=labels, y=l10["km"], mode="lines+markers", name="Real games", line=dict(color=S2, width=2),
                        marker=dict(size=9, color=S2, line=dict(color=CARD, width=2)),
                        hovertemplate="%{x}<br>%{y:.2f} km<extra>real</extra>")
        fig.add_scatter(x=["Simulated<br>game"], y=[s["dist_km"]], mode="markers", name="Simulated game",
                        marker=dict(size=14, color=ACCENT, symbol="diamond", line=dict(color=CARD, width=2)),
                        hovertemplate="Simulated game<br>%{y:.2f} km<extra></extra>")
        fig_base(fig, 300)
        fig.update_yaxes(title="Distance (km)")
        fig.add_annotation(x=1, xref="paper", y=1.12, yref="paper", xanchor="right", showarrow=False,
                           text="grey band = ±1 SD · green band = SWC", font=dict(color=MUTED, size=10))
        return fig

    # ---------------- team session
    @reactive.calc
    def team_games():
        team = input.team()
        gid = real_gid()
        info = real_game_info(gid, team) if gid else None
        out = []
        if info:
            opp, home, _ = info
            G = replay.parse(gid)
            pids = [p for p in G["shifts"]["pid"].unique() if G["players"][p]["team"] == team and G["players"][p]["pos"] != "G"]
            for pid in pids:
                g = real_shifts_game(gid, int(pid))
                if g is None:
                    continue
                s, pr = game_summary(g), g.profile
                b = vs_baseline(g).set_index("metric")
                out.append(dict(name=pr.name, pos=pr.pos, num=pr.num, flag=b.loc["Distance (km)", "flag"], **s))
            return pd.DataFrame(out), opp, home
        seed = int(input.seed() or 1)
        opp, home = opponent(team, seed)
        for pid in data.lineup(team):
            pr = data.profile(int(pid))
            g = simulate_game(pr, seed=seed * 1000 + pr.id % 997, opp=opp, home=home)
            s = game_summary(g)
            b = vs_baseline(g).set_index("metric")
            out.append(dict(name=pr.name, pos=pr.pos, num=pr.num, flag=b.loc["Distance (km)", "flag"], **s))
        return pd.DataFrame(out), opp, home

    @render.ui
    def team_hdr():
        df, opp, home = team_games()
        team = input.team()
        return ui.div(ui.div(ui.div(f"{team} {'vs' if home else '@'} {opp}", class_="nm"),
                             ui.div(f"Team session report · {len(df)} skaters", class_="meta"),
                             *([ui.span("Real game: real lineup, shifts & TOI", class_="real-tag"),
                                ui.span("Speed traces estimated", class_="sim-tag")]
                               if real_gid() and real_game_info(real_gid(), team) else [ui.span(f"Simulated game · seed {input.seed()}", class_="sim-tag")])),
                      ui.tags.img(src=f"https://assets.nhle.com/logos/nhl/svg/{team}_dark.svg", alt=team,
                                  style=f"border-radius:0;border:none;background:transparent;width:70px;height:70px"),
                      class_="hdr")

    @render.ui
    def team_table():
        df, _, _ = team_games()
        df = df.sort_values(["pos", "dist_km"], key=lambda c: c.map(lambda x: x == "D") if c.name == "pos" else -c)
        rows = "".join(
            f"<tr><td>{r.name}</td><td>{r.pos}</td><td>{mmss(r.toi_s)}</td><td>{r.shifts}</td><td>{r.dist_km:.2f}</td>"
            f"<td>{r.m_per_min:.0f}</td><td>{r.hsd_m:.0f}</td><td>{r.sprint_m:.0f}</td><td>{r.b18}</td><td>{r.b20}</td><td>{r.b22}</td>"
            f"<td>{r.max_kmh:.1f}</td><td>{r.pct_vmax:.0%}</td><td>{r.acc} / {r.dec}</td><td>{flag_html(r.flag)}</td></tr>"
            for r in df.itertuples())
        avg = df.mean(numeric_only=True)
        rows += (f"<tr style='font-weight:700'><td>Team average</td><td></td><td>{mmss(avg.toi_s)}</td><td>{avg.shifts:.0f}</td>"
                 f"<td>{avg.dist_km:.2f}</td><td>{avg.m_per_min:.0f}</td><td>{avg.hsd_m:.0f}</td><td>{avg.sprint_m:.0f}</td>"
                 f"<td>{avg.b18:.1f}</td><td>{avg.b20:.1f}</td><td>{avg.b22:.1f}</td><td>{avg.max_kmh:.1f}</td><td>{avg.pct_vmax:.0%}</td>"
                 f"<td>{avg.acc:.0f} / {avg.dec:.0f}</td><td></td></tr>")
        return ui.HTML("<table class='gps'><tr><th>Player</th><th>Pos</th><th>TOI</th><th>Shifts</th><th>Dist (km)</th><th>m/min</th>"
                       "<th>HSD (m)</th><th>Sprint (m)</th><th>18–20</th><th>20–22</th><th>22+</th><th>Max km/h</th><th>% max</th>"
                       "<th>Acc / Dec</th><th>vs season</th></tr>" + rows + "</table>")

    @render_plotly
    def team_chart():
        df, _, _ = team_games()
        df = df.sort_values("hsd_m", ascending=False)
        names = [f"{n} ({p})" for n, p in zip(df["name"], df["pos"])]
        fig = go.Figure()
        fig.add_bar(x=names, y=df["hsd_m"] - df["sprint_m"], name="18–22 mph", marker=dict(color=S1, line=dict(color=CARD, width=2)),
                    hovertemplate="%{x}<br>%{y:,.0f} m at 18–22 mph<extra></extra>")
        fig.add_bar(x=names, y=df["sprint_m"], name="22+ mph (sprint)", marker=dict(color=S3, line=dict(color=CARD, width=2)),
                    hovertemplate="%{x}<br>%{y:,.0f} m at 22+ mph<extra></extra>")
        fig_base(fig, 340, barmode="stack", bargap=0.2, legend_traceorder="normal")
        fig.update_yaxes(title="High-speed distance (m)")
        fig.update_xaxes(tickangle=-40)
        return fig

    # ---------------- season profile (real)
    @render.ui
    def prof_hdr():
        p = prof()
        return ui.div(ui.tags.img(src=headshot(p), alt=p.name),
                      ui.div(ui.div(p.name, class_="nm"), ui.div(f"#{p.num} · {p.pos} · {p.team} · {p.gp} GP", class_="meta"),
                             ui.span(f"Real NHL EDGE · {SEASON} regular season", class_="real-tag")), class_="hdr")

    @render.ui
    def prof_tiles():
        p = prof()
        lg = data.league_bursts_pg()
        return ui.div(
            tile("TOI per game", mmss(p.toi_pg_s), f"SD {p.toi_sd_s / 60:.1f} min (last 10)"),
            tile("Distance per game", f"{p.expected_km:.2f} km", f"season total {p.raw['dist_km']:.0f} km"),
            tile("Distance per 60", f"{p.p60['all']:.2f} km", f"{p.p60_pct:.0%} percentile · league {p.p60_lg:.2f}"),
            tile("Bursts 18–20 /gm", f"{p.bursts_pg['b18']:.1f}", f"league avg {lg['b18']:.1f}"),
            tile("Bursts 20–22 /gm", f"{p.bursts_pg['b20']:.1f}", f"league avg {lg['b20']:.1f}"),
            tile("Bursts 22+ /gm", f"{p.bursts_pg['b22']:.2f}", f"league avg {lg['b22']:.2f}"),
            tile("Top speed", f"{p.vmax_kmh:.1f} km/h", f"{p.vmax_kmh / MPH:.1f} mph · {p.vmax_pct:.0%} percentile"),
            class_="tiles")

    def l10_fig(col, title, fmt):
        l10 = prof().last10
        labels = [f"{d:%b %d}<br>{'vs' if h else '@'} {o}" for d, o, h in zip(l10["date"], l10["opp"], l10["home"])]
        y = l10[col] / (60 if col == "toi_s" else 1)
        fig = go.Figure(go.Scatter(x=labels, y=y, mode="lines+markers", line=dict(color=S2, width=2),
                                   marker=dict(size=9, color=S2, line=dict(color=CARD, width=2)),
                                   hovertemplate="%{x}<br>%{y:" + fmt + "}<extra></extra>"))
        if len(y):
            fig.add_hline(y=y.mean(), line=dict(color=TEXT2, dash="dash", width=1))
        fig_base(fig, 260, showlegend=False)
        fig.update_yaxes(title=title)
        return fig

    @render_plotly
    def l10_dist():
        return l10_fig("km", "Distance (km)", ".2f")

    @render_plotly
    def l10_toi():
        return l10_fig("toi_s", "TOI (min)", ".1f")

    @render_plotly
    def league_scatter():
        t = data.table()
        t = t.assign(bursts_p60=t["b18_p60"] + t["b20_p60"] + t["b22_p60"])
        p = prof()
        me = t[t["id"] == p.id]
        fig = go.Figure()
        fig.add_scatter(x=t["km_p60"], y=t["bursts_p60"], mode="markers", name="NHL skaters (10+ GP)",
                        marker=dict(size=7, color="rgba(189,187,166,0.35)", line=dict(width=0)),
                        customdata=np.c_[t["name"], t["team"], t["pos"]],
                        hovertemplate="%{customdata[0]} (%{customdata[1]}, %{customdata[2]})<br>%{x:.2f} km/60 · %{y:.1f} bursts/60<extra></extra>")
        fig.add_scatter(x=me["km_p60"], y=me["bursts_p60"], mode="markers+text", name=p.name, text=[p.name], textposition="top center",
                        textfont=dict(color=TEXT), marker=dict(size=14, color=ACCENT, line=dict(color=CARD, width=2)),
                        hovertemplate=f"{p.name}<br>%{{x:.2f}} km/60 · %{{y:.1f}} bursts/60<extra></extra>")
        fig_base(fig, 330)
        fig.update_xaxes(title="Distance per 60 (km)")
        fig.update_yaxes(title="Bursts per 60 (18+ mph)")
        return fig

    @render.ui
    def top_speeds():
        top = prof().raw.get("top") or []
        rows = "".join(f"<tr><td>{d}</td><td>P{per} {tip}</td><td>{mph * MPH:.1f}</td><td>{mph:.1f}</td></tr>" for d, mph, per, tip in top)
        return ui.HTML("<table class='gps'><tr><th>Date</th><th>When</th><th>km/h</th><th>mph</th></tr>" + rows + "</table>")

    # ---------------- game replay (real game)
    @reactive.calc
    def replay_gid():
        gid = real_gid()
        if gid:
            return gid
        # simulated game selected: replay the team's most recent saved game, else Calgary's
        saved = replay.bundled_games(input.team())
        return int(saved["id"].iloc[0]) if len(saved) else LATEST_GAME

    @reactive.calc
    def rfocus():
        return focus_team(replay_gid(), input.team())

    @reactive.effect
    async def _send_replay():
        gid = replay_gid()
        await session.send_custom_message("replay_load", json.loads(replay_json(gid, rfocus())))
        with reactive.isolate():          # changing the followed player must not restart the replay
            await send_focus()

    async def send_focus():
        pid = input.player()
        if not pid:
            return
        await session.send_custom_message("replay_focus", {"pid": int(pid), "headshot": data.headshot(int(pid))})

    @reactive.effect
    @reactive.event(input.player)
    async def _focus_changed():
        await send_focus()

    @reactive.effect
    @reactive.event(input.rink_pick)
    async def _rink_pick():
        pid = int(input.rink_pick().split(":")[0])
        G = replay.parse(replay_gid())
        p = G["players"].get(pid)
        if not p:
            return
        choices = players_for(input.team(), real_gid())
        if p["team"] == input.team() and str(pid) in choices:
            ui.update_select("player", selected=str(pid))      # sidebar follows (its own effect sends the focus)
        else:
            # other team's player: follow him on the rink without switching the sidebar team
            mug = data.headshot(pid) or next((r.get("headshot") for r in G["pbp"]["rosterSpots"] if r["playerId"] == pid), "")
            await session.send_custom_message("replay_focus", {"pid": pid, "headshot": mug})

    @render.ui
    def game_stats():
        gid = replay_gid()
        FOCUS = rfocus()
        G = replay.parse(gid)
        pbp, box = G["pbp"], G["box"]
        H, A = G["home"], G["away"]
        out = pbp.get("gameOutcome", {}).get("lastPeriodType", "REG")
        focus_side = "home" if H["abbrev"] == FOCUS else "away"

        # team stats from the real play-by-play
        def count(side, types, owner_is_side=True):
            return sum(1 for e in G["events"] if e["type"] in types and ((e["owner"] == side) == owner_is_side) and e["owner"])
        rows = []
        for lab, fn in [
            ("Goals", lambda s: count(s, {"goal"})),
            ("Shots on goal", lambda s: count(s, {"goal", "shot-on-goal"})),
            ("Shot attempts", lambda s: count(s, {"goal", "shot-on-goal", "missed-shot", "blocked-shot"})),
            ("Hits", lambda s: count(s, {"hit"})),
            ("Blocked shots", lambda s: count(s, {"blocked-shot"}, owner_is_side=False)),
            ("Takeaways", lambda s: count(s, {"takeaway"})),
            ("Giveaways", lambda s: count(s, {"giveaway"})),
            ("Faceoffs won", lambda s: count(s, {"faceoff"})),
            ("PIM", lambda s: sum(int(e["d"].get("duration") or 0) for e in G["events"] if e["type"] == "penalty" and e["owner"] == s)),
        ]:
            rows.append(f"<tr><td>{lab}</td><td>{fn('away')}</td><td>{fn('home')}</td></tr>")
        team_tbl = (f"<table class='gps'><tr><th></th><th>{A['abbrev']}</th><th>{H['abbrev']}</th></tr>" + "".join(rows) + "</table>")

        # goals by period
        per_goals = {(s, p): 0 for s in ("away", "home") for p in range(1, G["n_per"] + 1)}
        for e in G["events"]:
            if e["type"] == "goal" and e["owner"]:
                per_goals[(e["owner"], e["period"])] += 1
        heads = "".join(f"<th>{'P' + str(p) if p <= 3 else 'OT'}</th>" for p in range(1, G["n_per"] + 1))
        prow = lambda s, t: f"<tr><td>{t['abbrev']}</td>" + "".join(f"<td>{per_goals[(s, p)]}</td>" for p in range(1, G["n_per"] + 1)) + f"<td><b>{t['score']}</b></td></tr>"
        per_tbl = f"<table class='gps'><tr><th>Team</th>{heads}<th>Final</th></tr>{prow('away', A)}{prow('home', H)}</table>"

        # skater table: real box score + estimated skating load on real shifts
        side_key = "homeTeam" if focus_side == "home" else "awayTeam"
        pbg = box["playerByGameStats"][side_key]
        sk_rows = []
        for r in pbg["forwards"] + pbg["defense"]:
            pid = r["playerId"]
            g = real_shifts_game(gid, pid)
            est = game_summary(g) if g is not None else None
            flag = vs_baseline(g).set_index("metric").loc["Distance (km)", "flag"] if g is not None else "n/a"
            nm = G["players"][pid]["name"] if pid in G["players"] else r["name"]["default"]
            e_cells = (f"<td>{est['dist_km']:.2f}</td><td>{est['m_per_min']:.0f}</td><td>{est['hsd_m']:.0f}</td><td>{est['bursts']}</td>"
                       f"<td>{est['max_kmh']:.1f}</td><td>{flag_html(flag)}</td>") if est else "<td colspan=6 style='text-align:center;color:#8E9081'>no EDGE profile</td>"
            sk_rows.append((r["position"] == "D", -mmss_to_s(r["toi"]),
                            f"<tr><td>#{r['sweaterNumber']} {nm}</td><td>{r['position']}</td><td>{r['goals']}</td><td>{r['assists']}</td>"
                            f"<td>{r['plusMinus']:+d}</td><td>{r['sog']}</td><td>{r['hits']}</td><td>{r['blockedShots']}</td>"
                            f"<td>{r['toi']}</td><td>{r['shifts']}</td>{e_cells}</tr>"))
        sk_rows.sort(key=lambda x: (x[0], x[1]))
        sk_tbl = ("<table class='gps'><tr><th>Player</th><th>Pos</th><th>G</th><th>A</th><th>+/-</th><th>SOG</th><th>Hits</th><th>Blk</th>"
                  "<th>TOI</th><th>Shifts</th><th>Est. km</th><th>Est. m/min</th><th>Est. HSD (m)</th><th>Est. bursts</th><th>Est. max km/h</th><th>vs season</th></tr>"
                  + "".join(x[2] for x in sk_rows) + "</table>")

        fs = H if focus_side == "home" else A
        os_ = A if focus_side == "home" else H
        res = "WIN" if fs["score"] > os_["score"] else "LOSS"
        return ui.div(
            ui.div(ui.tags.img(src=f"https://assets.nhle.com/logos/nhl/svg/{A['abbrev']}_dark.svg", alt=A["abbrev"]),
                   ui.div(ui.div(f"{A['abbrev']} {A['score']} – {H['score']} {H['abbrev']}", class_="sc"),
                          ui.div(f"Final{'' if out == 'REG' else ' / ' + out} · {pbp['gameDate']} · {pbp.get('venue', {}).get('default', '')} · {FOCUS} {res}", class_="lab")),
                   ui.tags.img(src=f"https://assets.nhle.com/logos/nhl/svg/{H['abbrev']}_dark.svg", alt=H["abbrev"]),
                   ui.tags.button("⟲ Watch the replay again", id="rp-again", class_="btn-ghost"),
                   class_="final"),
            ui.layout_columns(
                ui.card(ui.card_header("Score by period ", ui.span("Real", class_="real-tag")), ui.HTML(per_tbl)),
                ui.card(ui.card_header("Team stats ", ui.span("Real", class_="real-tag")), ui.HTML(team_tbl)),
                ui.card(ui.card_header("Shot map ", ui.span("Real", class_="real-tag")), output_widget("shot_map"),
                        ui.p(f"{FOCUS} shooting right, {os_['abbrev']} shooting left. ★ = goal.", class_="note")),
                col_widths=[3, 3, 6],
            ),
            ui.card(ui.card_header(f"{FOCUS} skaters: box score ", ui.span("Real", class_="real-tag"), " + skating load ",
                                   ui.span("Estimated on real shifts", class_="sim-tag")),
                    ui.HTML(sk_tbl),
                    ui.p("G, A, +/-, SOG, hits, blocks, TOI and shifts are the real box score. Skating load is estimated: each real shift "
                         "(its length, period and game state) gets a speed trace calibrated to the player's real 2025-26 EDGE profile. "
                         "Open a player in the Post-game report for his shift-by-shift GPS report from this game.", class_="note")),
        )

    @render_plotly
    def shot_map():
        gid = replay_gid()
        FOCUS = rfocus()
        G = replay.parse(gid)
        focus_side = "home" if G["home"]["abbrev"] == FOCUS else "away"
        fig = go.Figure()
        rink_shapes(fig)
        for side, col, sign in ((focus_side, S3, 1), ("away" if focus_side == "home" else "home", S2, -1)):
            ab = G[side]["abbrev"]
            for kind, sym, size in (("shot", "circle", 8), ("goal", "star", 16)):
                types = {"goal"} if kind == "goal" else {"shot-on-goal", "missed-shot"}
                ev = [e for e in G["events"] if e["type"] in types and e["owner"] == side and e["x"] is not None]
                xs = [sign * abs(e["x"]) for e in ev]
                ys = [e["y"] * (1 if (e["x"] >= 0) == (sign > 0) else -1) for e in ev]
                fig.add_scatter(x=xs, y=ys, mode="markers", name=f"{ab} {'goals' if kind == 'goal' else 'shots'}",
                                marker=dict(symbol=sym, size=size, color=col, opacity=0.95 if kind == "goal" else 0.6,
                                            line=dict(color="#ffffff", width=1)),
                                text=[f"P{e['period']} {e['clock']} · {replay.describe(e, G['players'])}" for e in ev],
                                hovertemplate="%{text}<extra></extra>")
        fig_base(fig, 300)
        fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), legend=dict(y=1.14, font=dict(size=10)))
        fig.update_xaxes(range=[-101, 101], visible=False)
        fig.update_yaxes(range=[-43.5, 43.5], visible=False, scaleanchor="x")
        return fig

    @reactive.effect
    @reactive.event(input.replay_done)
    def _done():
        pass  # the replay panel swaps itself to the stats client-side; kept for logging / future use


    # ---------------- validation vs real NHL tracking
    @reactive.calc
    def val_df():
        n = None
        if not validate.CACHE.exists():
            n = ui.notification_show("Running the validation for the first time (about 30 s)…", duration=None)
        try:
            return validate.results()
        finally:
            if n:
                ui.notification_remove(n)

    @render.ui
    def val_tiles():
        df = val_df()
        if not len(df):
            return ui.p("No saved games overlap with players' last 10 EDGE games yet.", class_="note")
        e, r = validate.summary(df, "est_km"), validate.summary(df, "replay_km")
        return ui.div(
            tile("Player-games", f"{e['n']}", f"{df['game_id'].nunique()} games · real NHL tracking"),
            tile("Skating load: error", f"±{e['mae']:.2f} km", f"{e['mape']:.1f}% average · bias {e['bias_pct']:+.1f}%"),
            tile("Skating load: r", f"{e['r']:.2f}", "correlation with real distance"),
            tile("Replay movement: error", f"±{r['mae']:.2f} km", f"{r['mape']:.1f}% average · bias {r['bias_pct']:+.1f}%"),
            tile("Replay movement: r", f"{r['r']:.2f}", "correlation with real distance"),
            class_="tiles")

    @render_plotly
    def val_scatter():
        df = val_df()
        fig = go.Figure()
        if len(df):
            lo, hi = float(min(df.real_km.min(), df.est_km.min())) - 0.2, float(max(df.real_km.max(), df.est_km.max())) + 0.2
            fig.add_scatter(x=[lo, hi], y=[lo, hi], mode="lines", line=dict(color=MUTED, dash="dash", width=1), showlegend=False, hoverinfo="skip")
            for pos, col in (("F", S1), ("D", S2)):
                d = df[df.pos == pos]
                fig.add_scatter(x=d.real_km, y=d.est_km, mode="markers", name="Forwards" if pos == "F" else "Defence",
                                marker=dict(size=8, color=col, opacity=0.8, line=dict(color=CARD, width=1)),
                                customdata=np.c_[d.name, d.date, d.home + " v " + d.away],
                                hovertemplate="%{customdata[0]} · %{customdata[1]} %{customdata[2]}<br>real %{x:.2f} km · estimated %{y:.2f} km<extra></extra>")
        fig_base(fig, 340)
        fig.update_xaxes(title="Real distance, NHL tracking (km)")
        fig.update_yaxes(title="Estimated distance (km)")
        return fig

    @render_plotly
    def val_ba():
        df = val_df()
        fig = go.Figure()
        if len(df):
            m = (df.est_km + df.real_km) / 2
            d = df.est_km - df.real_km
            st = validate.summary(df, "est_km")
            for pos, col in (("F", S1), ("D", S2)):
                k = df.pos == pos
                fig.add_scatter(x=m[k], y=d[k], mode="markers", name="Forwards" if pos == "F" else "Defence",
                                marker=dict(size=8, color=col, opacity=0.8, line=dict(color=CARD, width=1)),
                                customdata=np.c_[df.name[k]], hovertemplate="%{customdata[0]}<br>difference %{y:+.2f} km<extra></extra>")
            fig.add_hline(y=st["bias"], line=dict(color=TEXT2, width=1.5))
            for y in (st["bias"] - st["loa"], st["bias"] + st["loa"]):
                fig.add_hline(y=y, line=dict(color=MUTED, dash="dash", width=1))
            fig.add_annotation(x=1, xref="paper", y=st["bias"] + st["loa"], text=f"+{st['loa']:.2f} km", showarrow=False, xanchor="right", yanchor="bottom", font=dict(color=MUTED, size=10))
            fig.add_annotation(x=1, xref="paper", y=st["bias"] - st["loa"], text=f"−{st['loa']:.2f} km", showarrow=False, xanchor="right", yanchor="top", font=dict(color=MUTED, size=10))
        fig_base(fig, 340)
        fig.update_xaxes(title="Mean of estimated and real (km)")
        fig.update_yaxes(title="Estimated − real (km)")
        return fig

    @render.ui
    def val_games():
        df = val_df()
        if not len(df):
            return ui.p("—", class_="note")
        g = df.assign(err=(df.est_km - df.real_km).abs(), pct=((df.est_km - df.real_km) / df.real_km * 100).abs())
        t = g.groupby(["game_id", "date", "away", "home"]).agg(n=("name", "count"), mae=("err", "mean"), mape=("pct", "mean")).reset_index()
        rows = "".join(f"<tr><td>{r.date}</td><td>{r.away} @ {r.home}</td><td>{r.n}</td><td>±{r.mae:.2f}</td><td>{r.mape:.1f}%</td></tr>"
                       for r in t.sort_values("date", ascending=False).itertuples())
        return ui.HTML("<table class='gps'><tr><th>Date</th><th>Game</th><th>Players</th><th>Error (km)</th><th>Error (%)</th></tr>" + rows + "</table>")

    @render.ui
    def val_players():
        df = val_df()
        gid = replay_gid()
        d = df[df.game_id == gid] if len(df) else df
        if not len(d):
            return ui.p("The game selected in the sidebar isn't in any player's last 10 EDGE games, so there's no real "
                        "per-game distance to compare against. Pick one of the games listed on the left.", class_="note")
        d = d.assign(err=(d.est_km - d.real_km) / d.real_km * 100).sort_values(["pos", "real_km"], ascending=[False, False])
        rows = "".join(f"<tr><td>{r.name}</td><td>{r.team}</td><td>{r.pos}</td><td>{mmss(r.toi_min * 60)}</td><td><b>{r.real_km:.2f}</b></td>"
                       f"<td>{r.est_km:.2f}</td><td>{r.err:+.1f}%</td><td>{r.replay_km:.2f}</td></tr>" for r in d.itertuples())
        return ui.HTML("<table class='gps'><tr><th>Player</th><th>Team</th><th>Pos</th><th>TOI</th><th>Real km</th><th>Est. km</th>"
                       "<th>Error</th><th>Replay km</th></tr>" + rows + "</table>")


app = App(app_ui, server, static_assets=Path(__file__).parent / "www")
