"""Small client for the NHL's public API, used to load any team's real games on demand.

Games are cached in data/games/<game_id>.json, so each one is downloaded once.
If the API can't be reached, the app falls back to the games already in that folder.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

import requests

WEB = "https://api-web.nhle.com"
STATS = "https://api.nhle.com/stats/rest/en"
GAMES = Path(__file__).parent / "data" / "games"
session = requests.Session()
session.headers["User-Agent"] = "nhl-skating-load (portfolio project)"


def get(url: str, tries: int = 3, timeout: int = 20):
    err = None
    for i in range(tries):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            err = e
            time.sleep(1.0 * (i + 1))
    raise ConnectionError(f"NHL API unreachable: {url} ({err})")


@lru_cache(maxsize=1)
def current_season() -> str:
    """The NHL's current season, e.g. '20262027' (falls back to 2025-26 if the API can't be reached)."""
    try:
        s = get(f"{WEB}/v1/club-schedule-season/TOR/now", tries=2, timeout=10)
        return str(s.get("currentSeason") or s["games"][0]["season"])
    except Exception:
        return "20252026"


def previous_season(season: str) -> str:
    y = int(season[:4])
    return f"{y - 1}{y}"


@lru_cache(maxsize=128)
def _schedule_cached(team: str, season: str, hour: int) -> list[dict]:
    s = get(f"{WEB}/v1/club-schedule-season/{team}/{season}", tries=2, timeout=10)
    out = []
    for g in s["games"]:
        if g["gameType"] not in (2, 3) or g["gameState"] not in ("OFF", "FINAL"):
            continue
        h, a = g["homeTeam"], g["awayTeam"]
        home = h["abbrev"] == team
        us, them = (h, a) if home else (a, h)
        ot = (g.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        res = "W" if us.get("score", 0) > them.get("score", 0) else "L"
        po = "Playoffs · " if g["gameType"] == 3 else ""
        out.append(dict(id=g["id"], date=g["gameDate"], opp=them["abbrev"], home=home,
                        label=f"{po}{g['gameDate']}  {'vs' if home else '@'} {them['abbrev']}  {res} {us.get('score')}-{them.get('score')}"
                              f"{'' if ot == 'REG' else ' ' + ot}"))
    return sorted(out, key=lambda r: r["date"], reverse=True)


def schedule(team: str, season: str = "20252026") -> list[dict]:
    """Completed regular-season and playoff games for a team, newest first (live; re-checked every hour)."""
    return _schedule_cached(team, season, int(time.time() // 3600))


def is_cached(gid: int) -> bool:
    return (GAMES / f"{gid}.json").exists()


def fetch_game(gid: int) -> Path:
    """Download play-by-play, box score and shift charts for one game (skips if cached)."""
    path = GAMES / f"{gid}.json"
    if path.exists():
        return path
    pbp = get(f"{WEB}/v1/gamecenter/{gid}/play-by-play")
    box = get(f"{WEB}/v1/gamecenter/{gid}/boxscore")
    sh = get(f"{STATS}/shiftcharts?cayenneExp=gameId={gid}")["data"]
    if not sh:
        raise ValueError(f"No shift charts published for game {gid}")
    shifts = [[d["playerId"], d["teamAbbrev"], d["period"], d["startTime"], d["endTime"], d["duration"],
               d["typeCode"], d["detailCode"], d["shiftNumber"]] for d in sh]
    GAMES.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pbp": pbp, "box": box, "shifts": shifts}, f, separators=(",", ":"))
    tmp.replace(path)
    return path
