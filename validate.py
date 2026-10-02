"""Validate the app's skating estimates against real NHL tracking.

NHL EDGE publishes each skater's REAL tracked distance for his last 10 games. For every saved
game that falls in a player's last 10, we compare that real distance with:
  * est_km: the post-game skating-load estimate (real shifts + EDGE-calibrated speed traces)
  * replay_km: the distance implied by the rink replay's estimated movement

Results are cached in data/validation.csv; only new games are computed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import data
import replay
from sim import game_summary, simulate_real_shifts

CACHE = Path(__file__).parent / "data" / "validation.csv"
FT_TO_KM = 0.0003048


def _game_rows(gid: int) -> list[dict]:
    G = replay.parse(gid)
    date = pd.Timestamp(G["pbp"]["gameDate"])
    known = set(data.table()["id"])
    R = None
    rows = []
    for pid in G["shifts"]["pid"].unique():
        pid = int(pid)
        p = G["players"][pid]
        if p["pos"] == "G" or pid not in known:
            continue
        prof = data.profile(pid)
        l10 = prof.last10
        m = l10[l10["date"] == date] if len(l10) else l10
        if not len(m):
            continue
        if R is None:
            R = replay.build_replay(gid)
        g = simulate_real_shifts(prof, replay.player_shifts(gid, pid), seed=gid % 100000 + pid % 997,
                                 opp="", home=True, game_id=gid)
        d = 0.0
        for _start, xy in R["tracks"].get(str(pid), []):
            a = np.asarray(xy, dtype=float).reshape(-1, 2)
            step = np.hypot(*np.diff(a, axis=0).T)
            d += step[step <= 33].sum()   # skip faceoff resets
        rows.append(dict(game_id=gid, date=G["pbp"]["gameDate"], home=G["home"]["abbrev"], away=G["away"]["abbrev"],
                         player_id=pid, name=p["name"], team=p["team"], pos="D" if p["pos"] == "D" else "F",
                         toi_min=float(m["toi_s"].iloc[0]) / 60, real_km=float(m["km"].iloc[0]),
                         est_km=game_summary(g)["dist_km"], replay_km=d * FT_TO_KM))
    return rows


def results(game_ids: list[int] | None = None) -> pd.DataFrame:
    """Validation rows for all saved games (computing any that aren't cached yet)."""
    game_ids = game_ids if game_ids is not None else [int(f.stem) for f in replay.GAMES.glob("*.json")]
    cached = pd.read_csv(CACHE) if CACHE.exists() else pd.DataFrame()
    done = set(cached["game_id"]) if len(cached) else set()
    new = []
    for gid in game_ids:
        if gid in done:
            continue
        new.extend(_game_rows(gid))
    if new:
        cached = pd.concat([cached, pd.DataFrame(new)], ignore_index=True)
        cached.to_csv(CACHE, index=False)
    return cached[cached["game_id"].isin(game_ids)] if len(cached) else cached


def summary(df: pd.DataFrame, col: str) -> dict:
    """Agreement stats for one estimate column vs real_km."""
    if not len(df):
        return {}
    diff = df[col] - df["real_km"]
    pct = diff / df["real_km"] * 100
    return dict(n=len(df), mae=diff.abs().mean(), bias_pct=pct.mean(), mape=pct.abs().mean(),
                r=float(np.corrcoef(df[col], df["real_km"])[0, 1]) if len(df) > 2 else np.nan,
                loa=1.96 * diff.std(), bias=diff.mean())
