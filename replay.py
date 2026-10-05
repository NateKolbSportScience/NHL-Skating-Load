"""Rebuild a real NHL game for replay on a rink.

Real (from the NHL's public API):
  * play-by-play: every shot, goal, hit, faceoff, takeaway, giveaway, block and penalty,
    with its game-clock time and x/y location on the ice
  * shift charts: exactly which players were on the ice at every second
  * box score

Estimated:
  * the puck path between event locations (straight lines with a little drift)
  * skater and goalie positions between events. Each skater moves toward a target spot
    set by his role (C / wing / D), which team has the puck, and where the puck is.

Rink coordinates follow the NHL API: x from -100 to 100 ft (goal lines at +/-89),
y from -42.5 to 42.5 ft.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

GAMES = Path(__file__).parent / "data" / "games"
PERIOD_S = 1200
SHOT_TYPES = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}
LOGGED = {"faceoff", "hit", "shot-on-goal", "missed-shot", "blocked-shot", "goal", "giveaway", "takeaway", "penalty"}


def mmss_to_s(s: str | None) -> int:
    if not s:
        return 0
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


@lru_cache(maxsize=None)
def load_game(game_id: int) -> dict:
    with open(GAMES / f"{game_id}.json", encoding="utf-8") as f:
        return json.load(f)


def bundled_games(team: str = "CGY") -> pd.DataFrame:
    rows = []
    for f in sorted(GAMES.glob("*.json")):
        g = load_game(int(f.stem))["pbp"]
        h, a = g["homeTeam"], g["awayTeam"]
        if team not in (h["abbrev"], a["abbrev"]):
            continue
        home = h["abbrev"] == team
        opp = a if home else h
        us, them = (h, a) if home else (a, h)
        res = "W" if us["score"] > them["score"] else "L"
        ot = g.get("gameOutcome", {}).get("lastPeriodType", "REG")
        rows.append(dict(id=g["id"], date=g["gameDate"], opp=opp["abbrev"], home=home,
                         label=f"{g['gameDate']}  {'vs' if home else '@'} {opp['abbrev']}  {res} {us['score']}-{them['score']}{'' if ot == 'REG' else ' ' + ot}"))
    return pd.DataFrame(rows).sort_values("date", ascending=False)


# ------------------------------------------------------------------ parsing
def _game_t(period: int, clock: str) -> int:
    return (period - 1) * PERIOD_S + mmss_to_s(clock)


def parse(game_id: int) -> dict:
    g = load_game(game_id)
    pbp = g["pbp"]
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    side = {home["id"]: "home", away["id"]: "away"}
    players = {}
    for r in pbp["rosterSpots"]:
        players[r["playerId"]] = dict(id=r["playerId"], name=f'{r["firstName"]["default"]} {r["lastName"]["default"]}',
                                      num=r.get("sweaterNumber"), pos=r["positionCode"], side=side[r["teamId"]],
                                      team=home["abbrev"] if side[r["teamId"]] == "home" else away["abbrev"])

    # shifts (typeCode 517 = shift; 505 rows are goal markers)
    sh = pd.DataFrame(g["shifts"], columns=["pid", "team", "period", "start", "end", "dur", "type", "detail", "num"])
    sh = sh[(sh["type"] == 517) & sh["pid"].isin(players)].copy()
    sh["t0"] = [(_p - 1) * PERIOD_S + mmss_to_s(s) for _p, s in zip(sh["period"], sh["start"])]
    sh["t1"] = [(_p - 1) * PERIOD_S + mmss_to_s(e) for _p, e in zip(sh["period"], sh["end"])]
    sh = sh[sh["t1"] > sh["t0"]]

    # events
    events, home_def = [], {}
    for e in pbp["plays"]:
        per = e["periodDescriptor"]["number"]
        t = _game_t(per, e["timeInPeriod"])
        if e.get("homeTeamDefendingSide"):
            home_def.setdefault(per, e["homeTeamDefendingSide"])
        d = e.get("details", {})
        events.append(dict(t=t, period=per, clock=e["timeInPeriod"], type=e["typeDescKey"], x=d.get("xCoord"), y=d.get("yCoord"),
                           owner=side.get(d.get("eventOwnerTeamId")), sit=e.get("situationCode"), d=d))
    end_t = max([ev["t"] for ev in events if ev["type"] in ("period-end", "game-end")] + [int(sh["t1"].max())])
    n_per = max(ev["period"] for ev in events)
    # home attacks +x when it defends the left end
    att = {p: (1 if home_def.get(p, "left") == "left" else -1) for p in range(1, n_per + 1)}
    return dict(id=game_id, pbp=pbp, box=g["box"], home=home, away=away, players=players, shifts=sh,
                events=events, end_t=end_t, n_per=n_per, att=att)


# ------------------------------------------------------------------ text for the event ticker
def _nm(players, pid):
    p = players.get(pid)
    return f"#{p['num']} {p['name'].split(' ', 1)[-1]}" if p else ""


def describe(ev, players) -> str:
    d, t = ev["d"], ev["type"]
    if t == "goal":
        a = [_nm(players, d.get(k)) for k in ("assist1PlayerId", "assist2PlayerId") if d.get(k)]
        return f"GOAL {_nm(players, d.get('scoringPlayerId'))}" + (f" ({', '.join(a)})" if a else " (unassisted)")
    if t == "shot-on-goal":
        return f"Shot on goal: {_nm(players, d.get('shootingPlayerId'))}"
    if t == "missed-shot":
        return f"Missed shot: {_nm(players, d.get('shootingPlayerId'))}"
    if t == "blocked-shot":
        return f"Blocked: {_nm(players, d.get('blockingPlayerId'))} blocks {_nm(players, d.get('shootingPlayerId'))}"
    if t == "hit":
        return f"Hit: {_nm(players, d.get('hittingPlayerId'))} on {_nm(players, d.get('hitteePlayerId'))}"
    if t == "faceoff":
        return f"Faceoff won: {_nm(players, d.get('winningPlayerId'))}"
    if t == "giveaway":
        return f"Giveaway: {_nm(players, d.get('playerId'))}"
    if t == "takeaway":
        return f"Takeaway: {_nm(players, d.get('playerId'))}"
    if t == "penalty":
        return f"Penalty: {_nm(players, d.get('committedByPlayerId'))} ({d.get('descKey', '').replace('-', ' ')}, {d.get('duration', '')} min)"
    return t.replace("-", " ").title()


# ------------------------------------------------------------------ movement model
BENCH_Y = 44.0
WANDER_THETA, WANDER_SIGMA = 0.21, 17.76  # drift around the target spot (ft); tuned to real tracking (see README)
FOLLOW = 0.685                            # share of the gap to the target closed each second
VMAX_FTS = 32.0                          # top skating speed in the model (~35 km/h)
INERTIA = 0.36                            # share of last second's velocity carried into the next (momentum)
WANDER_PHI = 0.54                         # smoothness of the drift itself (0 = white noise)


def _clamp(x, y):
    x = float(np.clip(x, -96, 96))
    y = float(np.clip(y, -40, 40))
    # rounded corners (radius 28 ft)
    cx, cy = 100 - 28, 42.5 - 28
    if abs(x) > cx and abs(y) > cy:
        vx, vy = abs(x) - cx, abs(y) - cy
        r = np.hypot(vx, vy)
        if r > 25:
            x = np.sign(x) * (cx + vx * 25 / r)
            y = np.sign(y) * (cy + vy * 25 / r)
    return x, y


def _slots(pids, players):
    """Assign on-ice skaters to roles: C, LW, RW, LD, RD (extra skaters fill spare forward slots)."""
    f = [p for p in pids if players[p]["pos"] in ("C", "L", "R")]
    d = [p for p in pids if players[p]["pos"] == "D"]
    out = {}
    order = {"C": 0, "L": 1, "R": 2}
    f.sort(key=lambda p: order[players[p]["pos"]])
    fslots = ["C", "LW", "RW", "F4", "F5"]
    used = set()
    for p in f:
        want = {"C": "C", "L": "LW", "R": "RW"}[players[p]["pos"]]
        slot = want if want not in used else next(s for s in fslots if s not in used)
        used.add(slot)
        out[p] = slot
    for i, p in enumerate(d):
        out[p] = ["LD", "RD", "D3"][min(i, 2)]
    return out


def _target(slot, dir_, has_puck, px, py, rng):
    """Where a skater wants to be, given his role, attack direction and possession."""
    left = dir_  # facing +x, a player's left is +y
    if has_puck:
        oz = dir_ * px > 25
        if slot == "C":
            tx, ty = px - dir_ * 5, py * 0.7
        elif slot in ("LW", "RW", "F4", "F5"):
            s = left if slot in ("LW", "F4") else -left
            tx, ty = px + dir_ * (10 if oz else 15), s * 20 + py * 0.25
        else:
            s = left if slot in ("LD", "D3") else -left
            tx = dir_ * 30 if oz else px - dir_ * 28
            ty = s * 17 + py * 0.15
    else:
        net = -dir_ * 86
        frac = {"C": 0.25, "LW": 0.4, "RW": 0.4, "F4": 0.45, "F5": 0.45, "LD": 0.62, "RD": 0.62, "D3": 0.62}[slot]
        s = {"LW": left, "RW": -left, "LD": left, "RD": -left}.get(slot, 0)
        tx = px + (net - px) * frac
        ty = py + (0 - py) * frac + s * (18 if slot in ("LW", "RW") else 11)
    return tx + rng.normal(0, 2.5), ty + rng.normal(0, 2.5)


def _faceoff_spot(slot, dir_, fx, fy, winner):
    off = {"C": (-dir_ * 2, 0), "LW": (-dir_ * 4, dir_ * 17), "RW": (-dir_ * 4, -dir_ * 17),
           "F4": (-dir_ * 12, dir_ * 8), "F5": (-dir_ * 12, -dir_ * 8),
           "LD": (-dir_ * 30, dir_ * 12), "RD": (-dir_ * 30, -dir_ * 12), "D3": (-dir_ * 30, 0)}[slot]
    return fx + off[0], fy + off[1]


def build_replay(game_id: int, seed: int = 7) -> dict:
    """Second-by-second puck and player positions, plus events, for the rink replay."""
    G = parse(game_id)
    rng = np.random.default_rng(seed + game_id % 1000)
    players, sh, T = G["players"], G["shifts"], G["end_t"]
    ev = [e for e in G["events"]]

    # puck keyframes from events with coordinates; faceoffs hold the puck at the dot
    keys = [(0, 0.0, 0.0)]
    for e in ev:
        if e["x"] is not None and e["y"] is not None:
            keys.append((e["t"], float(e["x"]), float(e["y"])))
    keys.append((T, 0.0, 0.0))
    kt = np.array([k[0] for k in keys], dtype=float)
    # several events can share a second: spread them over that second
    for i in range(1, len(kt)):
        if kt[i] <= kt[i - 1]:
            kt[i] = kt[i - 1] + 0.2
    kx, ky = np.array([k[1] for k in keys]), np.array([k[2] for k in keys])
    ts = np.arange(T + 1)
    puck_x = np.interp(ts, kt, kx)
    puck_y = np.interp(ts, kt, ky)
    # a little drift on long gaps so the puck isn't perfectly still between events
    gap = np.interp(ts, kt[1:], np.diff(kt))
    wob = np.clip(gap / 20, 0, 1)
    puck_x = puck_x + wob * 6 * np.sin(ts / 3.1 + 1.3)
    puck_y = puck_y + wob * 5 * np.sin(ts / 2.3)
    for t in range(T + 1):
        puck_x[t], puck_y[t] = _clamp(puck_x[t], puck_y[t])

    # possession: team owning the most recent possession-revealing event
    poss = np.empty(T + 1, dtype=object)
    cur = "home"
    ev_by_t = {}
    for e in ev:
        ev_by_t.setdefault(e["t"], []).append(e)
    for t in range(T + 1):
        for e in ev_by_t.get(t, []):
            o, ty = e["owner"], e["type"]
            if not o:
                continue
            other = "away" if o == "home" else "home"
            if ty in SHOT_TYPES or ty in ("faceoff", "takeaway"):
                cur = o
            elif ty in ("giveaway", "hit"):
                cur = other
        poss[t] = cur

    # faceoff times -> location and winner
    faceoffs = {e["t"]: e for e in ev if e["type"] == "faceoff" and e["x"] is not None}

    # on-ice sets per second
    on = {s: [[] for _ in range(T + 1)] for s in ("home", "away")}
    for r in sh.itertuples():
        s = players[r.pid]["side"]
        for t in range(int(r.t0), min(int(r.t1), T + 1)):
            on[s][t].append(r.pid)

    pos = {}                                   # pid -> list of [x, y] or None per second
    wander = {}
    wvel = {}                                  # pid -> drift velocity (smooth wander)
    vel = {}                                   # pid -> last second's velocity (momentum)
    cur_xy = {}
    period_of = np.minimum(ts // PERIOD_S + 1, G["n_per"])
    for t in range(T + 1):
        per = int(period_of[t])
        home_dir = G["att"][per]
        px, py = puck_x[t], puck_y[t]
        fo = faceoffs.get(t)
        for side in ("home", "away"):
            dir_ = home_dir if side == "home" else -home_dir
            pids = on[side][t]
            skaters = [p for p in pids if players[p]["pos"] != "G"]
            slots = _slots(skaters, players)
            for p in pids:
                if p not in cur_xy:  # coming off the bench
                    bx = (-12 if side == "home" else 12) * home_dir + rng.normal(0, 6)
                    cur_xy[p] = (bx, BENCH_Y - 3)
                if players[p]["pos"] == "G":
                    gx = -dir_ * 85
                    tx, ty = gx, float(np.clip(py * 0.12, -4, 4))
                    nx, ny = tx, ty
                else:
                    slot = slots[p]
                    if fo is not None:
                        tx, ty = _faceoff_spot(slot, dir_, fo["x"], fo["y"], fo["owner"])
                        nx, ny = tx, ty  # players line up for the draw
                        vel[p] = (0.0, 0.0)
                    else:
                        tx, ty = _target(slot, dir_, poss[t] == side, px, py, rng)
                        # each skater keeps moving around his spot: a smooth random drift (Ornstein-Uhlenbeck)
                        ox, oy = wander.get(p, (0.0, 0.0))
                        wx, wy = wvel.get(p, (0.0, 0.0))
                        wx = WANDER_PHI * wx + rng.normal(0, WANDER_SIGMA)
                        wy = WANDER_PHI * wy + rng.normal(0, WANDER_SIGMA * 0.7)
                        wvel[p] = (wx, wy)
                        ox += -WANDER_THETA * ox + wx
                        oy += -WANDER_THETA * oy + wy
                        wander[p] = (ox, oy)
                        tx, ty = tx + ox, ty + oy
                        cx, cy = cur_xy[p]
                        dx, dy = (tx - cx) * FOLLOW, (ty - cy) * FOLLOW
                        pvx, pvy = vel.get(p, (0.0, 0.0))
                        dx, dy = INERTIA * pvx + (1 - INERTIA) * dx, INERTIA * pvy + (1 - INERTIA) * dy
                        step = np.hypot(dx, dy)
                        vmax = VMAX_FTS
                        if step > vmax:
                            dx, dy = dx * vmax / step, dy * vmax / step
                        nx, ny = cx + dx, cy + dy
                nx, ny = _clamp(nx, ny)
                if players[p]["pos"] != "G" and fo is None:
                    vel[p] = (nx - cur_xy[p][0], ny - cur_xy[p][1])
                cur_xy[p] = (nx, ny)
                pos.setdefault(p, [None] * (T + 1))[t] = [round(nx, 1), round(ny, 1)]
            # players who left the ice come back on from the bench next shift
            for p in [p for p in cur_xy if players[p]["side"] == side and p not in pids]:
                del cur_xy[p]
                vel.pop(p, None)

    # events for the ticker and the rink markers
    ticker = []
    for e in ev:
        if e["type"] not in LOGGED:
            continue
        ticker.append(dict(t=e["t"], type=e["type"], x=e["x"], y=e["y"], side=e["owner"], text=describe(e, players),
                           period=e["period"], clock=e["clock"],
                           hs=e["d"].get("homeScore"), as_=e["d"].get("awayScore")))

    def team_info(t, side):
        return dict(abbrev=t["abbrev"], name=t["commonName"]["default"], score=t["score"], side=side)

    # compact per-player tracks: list of [first_second, flat xy list] runs
    tracks = {}
    for p, arr in pos.items():
        runs, cur_run, start = [], [], None
        for t, v in enumerate(arr):
            if v is None:
                if cur_run:
                    runs.append([start, cur_run])
                    cur_run = []
                continue
            if not cur_run:
                start = t
            cur_run.extend(v)
        if cur_run:
            runs.append([start, cur_run])
        tracks[str(p)] = runs

    return dict(
        id=game_id, T=T, n_per=G["n_per"], date=G["pbp"]["gameDate"],
        venue=G["pbp"].get("venue", {}).get("default", ""),
        home=team_info(G["home"], "home"), away=team_info(G["away"], "away"),
        players={str(k): dict(num=v["num"], name=v["name"], pos=v["pos"], side=v["side"]) for k, v in players.items()},
        puck=[[round(x, 1), round(y, 1)] for x, y in zip(puck_x, puck_y)],
        tracks=tracks, events=ticker,
        sit=_situations(G),
    )


def _situations(G) -> list:
    """[t, away_skaters, home_skaters] changes, from play-by-play situation codes."""
    out, last = [], None
    for e in G["events"]:
        sc = e.get("sit")
        if sc and len(sc) == 4 and sc != last:
            out.append([e["t"], int(sc[1]), int(sc[2])])
            last = sc
    return out


# ------------------------------------------------------------------ real shifts for the GPS layer
def player_shifts(game_id: int, pid: int) -> pd.DataFrame:
    """A player's real shifts in a game, with game state (ES / PP / PK) from situation codes."""
    G = parse(game_id)
    side = G["players"][pid]["side"]
    sits = _situations(G)
    st = pd.DataFrame(sits, columns=["t", "away", "home"]) if sits else pd.DataFrame({"t": [0], "away": [5], "home": [5]})
    rows = []
    for r in G["shifts"][G["shifts"]["pid"] == pid].sort_values("t0").itertuples():
        mid = (r.t0 + r.t1) / 2
        k = st[st["t"] <= mid]
        row = k.iloc[-1] if len(k) else st.iloc[0]
        us, them = (row["home"], row["away"]) if side == "home" else (row["away"], row["home"])
        strength = "PP" if us > them else "PK" if us < them else "ES"
        rows.append(dict(period=int(r.period), start_s=float(r.t0 - (r.period - 1) * PERIOD_S), length_s=float(r.t1 - r.t0),
                         strength=strength))
    return pd.DataFrame(rows)
