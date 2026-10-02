"""Refresh the NHL EDGE snapshot used by the app.

Pulls every rostered skater's season EDGE profile from the NHL's public API
(api-web.nhle.com) and writes data/edge_snapshot_<season>.json.

    python tools/fetch_edge.py                 # 2025-26 regular season
    python tools/fetch_edge.py --season 20262027  # current season (the app blends it with last season)

Takes a few minutes (about 600 skaters x 3 requests). No API key needed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

API = "https://api-web.nhle.com"
OUT = Path(__file__).resolve().parents[1] / "data"
session = requests.Session()
session.headers["User-Agent"] = "nhl-skating-load (portfolio project)"


def get(path: str, tries: int = 3):
    for i in range(tries):
        try:
            r = session.get(API + path, timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(1 + i)
    raise RuntimeError(f"failed: {path}")


def metric(x):
    return round(x["metric"], 4) if x else None


def skater(p: dict, season: str, gt: int) -> dict | None:
    pid = p["id"]
    d = get(f"/v1/edge/skater-detail/{pid}/{season}/{gt}")
    dist = get(f"/v1/edge/skater-skating-distance-detail/{pid}/{season}/{gt}")
    sp = get(f"/v1/edge/skater-skating-speed-detail/{pid}/{season}/{gt}")
    if not (d and dist and sp and dist.get("skatingDistanceDetails")):
        return None
    dd = {s["strengthCode"]: s for s in dist["skatingDistanceDetails"]}
    sd = sp["skatingSpeedDetails"]
    a = dd.get("all", {})

    def last10(g):
        opp = g["awayTeam"]["abbrev"] if g["playerOnHomeTeam"] else g["homeTeam"]["abbrev"]
        return [g["gameDate"], opp, int(g["playerOnHomeTeam"]), metric(g.get("distanceSkatedAll")), g.get("toiAll"),
                metric(g.get("distanceSkatedEven")), metric(g.get("distanceSkatedPP")), metric(g.get("distanceSkatedPK"))]

    return {**p,
            "gp": d["player"].get("gamesPlayed"), "g": d["player"].get("goals"), "a": d["player"].get("assists"),
            "dist_km": metric(a.get("distanceTotal")), "p60_km": metric(a.get("distancePer60")),
            "p60_pct": (a.get("distancePer60") or {}).get("percentile"),
            "p60_lg": ((a.get("distancePer60") or {}).get("leagueAvg") or {}).get("metric"),
            **{f"p60_{s}": metric(dd.get(s, {}).get("distancePer60")) for s in ("es", "pp", "pk")},
            **{f"dist_{s}": metric(dd.get(s, {}).get("distanceTotal")) for s in ("es", "pp", "pk")},
            "maxgame_km": metric(a.get("distanceMaxGame")), "maxper_km": metric(a.get("distanceMaxPeriod")),
            "vmax_mph": sd["maxSkatingSpeed"]["imperial"], "vmax_pct": sd["maxSkatingSpeed"].get("percentile"),
            "vmax_lg": sd["maxSkatingSpeed"]["leagueAvg"]["imperial"],
            **{k: sd[src]["value"] for k, src in (("b22", "burstsOver22"), ("b20", "bursts20To22"), ("b18", "bursts18To20"))},
            **{f"{k}_lg": sd[src]["leagueAvg"] for k, src in (("b22", "burstsOver22"), ("b20", "bursts20To22"), ("b18", "bursts18To20"))},
            **{f"{k}_pct": sd[src]["percentile"] for k, src in (("b22", "burstsOver22"), ("b20", "bursts20To22"), ("b18", "bursts18To20"))},
            "shot_mph": (d.get("topShotSpeed") or {}).get("imperial"),
            "oz": d.get("zoneTimeDetails", {}).get("offensiveZonePctg"),
            "nz": d.get("zoneTimeDetails", {}).get("neutralZonePctg"),
            "dz": d.get("zoneTimeDetails", {}).get("defensiveZonePctg"),
            "top": [[t["gameDate"], round(t["skatingSpeed"]["imperial"], 2), t["periodDescriptor"]["number"], t["timeInPeriod"]]
                    for t in sp.get("topSkatingSpeeds", [])],
            "last10": [last10(g) for g in dist.get("skatingDistanceLast10", [])]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="20252026")
    ap.add_argument("--game-type", type=int, default=2, help="2 = regular season, 3 = playoffs")
    args = ap.parse_args()

    end = f"{args.season[4:]}-04-10"
    st = get(f"/v1/standings/{end}") if dt.date.fromisoformat(end) <= dt.date.today() else None
    st = st or get("/v1/standings/now")
    teams = [t["teamAbbrev"]["default"] for t in st["standings"]]
    roster = []
    for t in teams:
        r = get(f"/v1/roster/{t}/{args.season}")
        for p in (r or {}).get("forwards", []) + (r or {}).get("defensemen", []):
            roster.append({"id": p["id"], "name": f'{p["firstName"]["default"]} {p["lastName"]["default"]}', "team": t,
                           "pos": p["positionCode"], "num": p.get("sweaterNumber"), "ht": p.get("heightInCentimeters"),
                           "wt": p.get("weightInKilograms"), "dob": p.get("birthDate")})
    print(f"{len(roster)} skaters on {len(teams)} rosters")

    with ThreadPoolExecutor(max_workers=8) as ex:
        players = [p for p in ex.map(lambda p: skater(p, args.season, args.game_type), roster) if p]

    OUT.mkdir(exist_ok=True)
    out = OUT / f"edge_snapshot_{args.season}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"season": args.season, "gameType": args.game_type,
                   "pulled": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "teams": teams, "players": players}, f, separators=(",", ":"))
    print(f"wrote {out} ({len(players)} skaters with EDGE data)")


if __name__ == "__main__":
    main()
