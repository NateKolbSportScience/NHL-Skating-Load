"""Load the NHL EDGE snapshot that ships with the app (refresh it with tools/fetch_edge.py)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from sim import Profile, build_profile

DATA = Path(__file__).parent / "data"
MIN_GP = 10  # skaters with fewer games don't have a stable EDGE profile


def snapshot_path() -> Path:
    files = sorted(DATA.glob("edge_snapshot_*.json"))
    if not files:
        raise FileNotFoundError("No EDGE snapshot in data/. Run: python tools/fetch_edge.py")
    return files[-1]


@lru_cache(maxsize=1)
def load() -> dict:
    """All EDGE snapshots merged, newest season first.

    A player's profile comes from the newest season in which he has 10+ games, so early in a
    new season the app keeps using last season's profile until there's enough new data.
    His team always comes from the newest snapshot (so trades and signings show up).
    """
    files = sorted(DATA.glob("edge_snapshot_*.json"))
    if not files:
        raise FileNotFoundError("No EDGE snapshot in data/. Run: python tools/fetch_edge.py")
    merged, team_now, seasons, newest = {}, {}, [], None
    for f in files:                                   # oldest -> newest
        with open(f, encoding="utf-8") as fh:
            snap = json.load(fh)
        seasons.append(snap["season"])
        newest = snap
        for p in snap["players"]:
            team_now[p["id"]] = p["team"]
            if (p.get("gp") or 0) >= MIN_GP and p.get("p60_km") and p.get("dist_km"):
                merged[p["id"]] = dict(p, season=snap["season"],
                                       mug=f"https://assets.nhle.com/mugs/nhl/{snap['season']}/{p['team']}/{p['id']}.png")
    for pid, p in merged.items():
        p["team"] = team_now.get(pid, p["team"])
    return dict(season=newest["season"], seasons=seasons, pulled=newest.get("pulled"), teams=newest["teams"],
                players=list(merged.values()))


@lru_cache(maxsize=1)
def table() -> pd.DataFrame:
    """One row per skater with per-game and per-60 numbers (real EDGE data)."""
    rows = []
    for p in load()["players"]:
        toi_h = p["dist_km"] / p["p60_km"]
        rows.append(dict(
            id=p["id"], name=p["name"], team=p["team"], pos=p["pos"], num=p.get("num"), gp=p["gp"],
            toi_pg=toi_h * 60 / p["gp"], km_pg=p["dist_km"] / p["gp"], km_p60=p["p60_km"], p60_pct=p.get("p60_pct"),
            b18_p60=(p["b18"] or 0) / toi_h, b20_p60=(p["b20"] or 0) / toi_h, b22_p60=(p["b22"] or 0) / toi_h,
            b18_pg=(p["b18"] or 0) / p["gp"], b20_pg=(p["b20"] or 0) / p["gp"], b22_pg=(p["b22"] or 0) / p["gp"],
            bursts_pg=sum(p[k] or 0 for k in ("b18", "b20", "b22")) / p["gp"],
            vmax_kmh=(p.get("vmax_mph") or 0) * 1.609344, vmax_pct=p.get("vmax_pct"),
        ))
    return pd.DataFrame(rows)


@lru_cache(maxsize=None)
def profile(player_id: int) -> Profile:
    p = next(x for x in load()["players"] if x["id"] == player_id)
    return build_profile(p)


def teams() -> list[str]:
    return sorted(table()["team"].unique())


def team_names() -> dict[str, str]:
    return {t: t for t in teams()}


def players(team: str) -> dict[str, str]:
    t = table()
    t = t[t["team"] == team].sort_values(["pos", "toi_pg"], key=lambda c: c.map({"C": 0, "L": 0, "R": 0, "D": 1}) if c.name == "pos" else -c)
    return {str(r.id): f"{r.name} · {r.pos} · #{r.num}" for r in t.itertuples()}


def lineup(team: str, n_f: int = 12, n_d: int = 6) -> list[int]:
    """Typical dressed lineup: top 12 forwards and top 6 D by TOI per game."""
    t = table()
    t = t[t["team"] == team]
    f = t[t["pos"] != "D"].nlargest(n_f, "toi_pg")
    d = t[t["pos"] == "D"].nlargest(n_d, "toi_pg")
    return list(f["id"]) + list(d["id"])


def league_bursts_pg() -> dict:
    """League-average bursts per game by band (skaters with 10+ GP)."""
    t = table()
    return {k: float(t[f"{k}_pg"].mean()) for k in ("b18", "b20", "b22")}


def profile_season(player_id: int) -> str:
    """Season the player's profile comes from, e.g. '2025-26' (last season until he has 10+ games in the new one)."""
    p = next((x for x in load()["players"] if x["id"] == player_id), None)
    s = (p or {}).get("season") or load()["season"]
    return f"{s[:4]}-{s[6:]}"


def headshot(player_id: int) -> str:
    p = next((x for x in load()["players"] if x["id"] == player_id), None)
    return p["mug"] if p else ""
