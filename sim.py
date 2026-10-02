"""Simulated in-game skating tracking, calibrated to a player's real NHL EDGE profile.

NHL EDGE publishes season-level skating data per skater: distance per 60 minutes,
speed bursts by band (18-20, 20-22, 22+ mph) and top speed. It does not publish
shift-level or per-second tracking. This module fills that gap with a *simulated*
game: it builds a realistic shift pattern and a 10 Hz speed trace for every shift,
scaled so that the game's distance per 60 and burst rates match the player's real
season profile (with natural game-to-game variation).

Everything produced here is simulated. The real EDGE profile it is built from is
kept alongside it so the report can show both.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HZ = 10                      # samples per second in the simulated speed trace
MPH = 1.609344               # km/h per mph
PERIOD_S = 20 * 60           # regulation period length, game clock

# Speed bands (km/h). The top three are NHL EDGE's own burst bands.
ZONES = [
    ("Z1 Glide", 0.0, 12.0),
    ("Z2 Cruise", 12.0, 20.0),
    ("Z3 Fast", 20.0, 18 * MPH),
    ("Z4 18-20 mph", 18 * MPH, 20 * MPH),
    ("Z5 20-22 mph", 20 * MPH, 22 * MPH),
    ("Z6 22+ mph", 22 * MPH, 99.0),
]
HSD_KMH = 18 * MPH           # high-speed skating threshold (EDGE's lowest burst band)
SPRINT_KMH = 22 * MPH        # sprint threshold (EDGE's top burst band)
ACC_MS2 = 2.5                # high-intensity acceleration / deceleration threshold (m/s^2)


# ------------------------------------------------------------------ real profile
@dataclass
class Profile:
    """A skater's real season profile, derived from the NHL EDGE snapshot."""
    id: int
    name: str
    team: str
    pos: str
    num: int | None
    gp: int
    toi_pg_s: float          # average time on ice per game (s)
    toi_sd_s: float          # game-to-game SD of TOI (from last 10 games)
    p60: dict                # km per 60 min by strength: all / es / pp / pk
    p60_lg: float            # league average km per 60
    p60_pct: float           # percentile of distance per 60
    share: dict              # share of TOI at es / pp / pk
    bursts_ph: dict          # bursts per hour of TOI by band: b18 / b20 / b22
    bursts_pg: dict          # bursts per game
    bursts_lg_pg: dict       # league-average bursts per game (EDGE league averages / typical GP)
    vmax_kmh: float
    vmax_pct: float
    last10: pd.DataFrame     # real last-10 games: date, opp, km, toi_s, km_per60
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def is_d(self) -> bool:
        return self.pos == "D"

    @property
    def expected_km(self) -> float:
        return self.p60["all"] * self.toi_pg_s / 3600


def build_profile(p: dict) -> Profile:
    toi_total_h = p["dist_km"] / p["p60_km"]
    gp = max(int(p["gp"] or 1), 1)
    toi_pg = toi_total_h * 3600 / gp

    l10 = pd.DataFrame(p.get("last10") or [], columns=["date", "opp", "home", "km", "toi_s", "km_es", "km_pp", "km_pk"])
    if len(l10):
        l10 = l10[l10["toi_s"] > 0].copy()
        l10["km_per60"] = l10["km"] / l10["toi_s"] * 3600
        l10["date"] = pd.to_datetime(l10["date"])
        l10 = l10.sort_values("date")
    toi_sd = float(l10["toi_s"].std()) if len(l10) >= 3 else np.nan
    if not np.isfinite(toi_sd) or toi_sd < 0.06 * toi_pg:
        toi_sd = 0.08 * toi_pg

    def t_at(strength):  # hours at a strength = distance / distance-per-60
        d, r = p.get(f"dist_{strength}"), p.get(f"p60_{strength}")
        return d / r if d and r else 0.0

    t = {s: t_at(s) for s in ("es", "pp", "pk")}
    tot = sum(t.values()) or 1.0
    share = {s: v / tot for s, v in t.items()}

    bursts = {k: p.get(k) or 0 for k in ("b18", "b20", "b22")}
    lg_gp = 60  # EDGE league averages are season totals over all skaters; ~60 GP is typical
    return Profile(
        id=p["id"], name=p["name"], team=p["team"], pos=p["pos"], num=p.get("num"), gp=gp,
        toi_pg_s=toi_pg, toi_sd_s=toi_sd,
        p60={"all": p["p60_km"], "es": p.get("p60_es") or p["p60_km"],
             "pp": p.get("p60_pp") or p["p60_km"], "pk": p.get("p60_pk") or p["p60_km"]},
        p60_lg=p.get("p60_lg") or 15.45, p60_pct=p.get("p60_pct") or 0.5,
        share=share,
        bursts_ph={k: v / toi_total_h for k, v in bursts.items()},
        bursts_pg={k: v / gp for k, v in bursts.items()},
        bursts_lg_pg={k: (p.get(f"{k}_lg") or 0) / lg_gp for k in bursts},
        vmax_kmh=(p.get("vmax_mph") or 22) * MPH, vmax_pct=p.get("vmax_pct") or 0.5,
        last10=l10, raw=p,
    )


# ------------------------------------------------------------------ simulation
@dataclass
class Game:
    profile: Profile
    opp: str
    home: bool
    seed: int
    shifts: pd.DataFrame
    traces: list            # one np.ndarray of km/h at 10 Hz per shift
    source: str = "simulated"
    game_id: int | None = None


def _ou(rng, n, mean, sd, theta, x0):
    """Ornstein-Uhlenbeck speed process (km/h) at 10 Hz."""
    dt = 1 / HZ
    x = np.empty(n)
    x[0] = x0
    noise = rng.normal(0, sd * np.sqrt(2 * theta * dt), n)
    for i in range(1, n):
        x[i] = x[i - 1] + theta * (mean - x[i - 1]) * dt + noise[i]
    return x


def _burst(n, start, peak, acc, dec, hold):
    """Speed profile of one burst (km/h): fast-then-easing acceleration to peak, a short hold,
    then a smooth deceleration. The profile starts and ends at 0, so combining it with the
    baseline (np.maximum) leaves and rejoins the baseline without jumps."""
    out = np.zeros(n)
    tau = peak / (acc * 3.6) / 1.3                       # time constant; initial slope ~1.3 x acc
    t_up = 3.0 * tau                                     # ~95% of peak
    t_dn = peak / (dec * 3.6) * 1.5
    t = (np.arange(n) - start) / HZ
    up = (t >= 0) & (t < t_up)
    out[up] = peak * (1 - np.exp(-t[up] / tau)) / (1 - np.exp(-3.0))
    hd = (t >= t_up) & (t < t_up + hold)
    out[hd] = peak
    dn = (t >= t_up + hold) & (t < t_up + hold + t_dn)
    out[dn] = peak * 0.5 * (1 + np.cos(np.pi * (t[dn] - t_up - hold) / t_dn))
    return out


def _shift_trace(rng, length_s, target_km, rates_ps, vmax, cap):
    """10 Hz speed trace for one shift whose distance matches target_km."""
    n = max(int(length_s * HZ), HZ * 5)
    mean = target_km / (length_s / 3600)
    base = np.clip(_ou(rng, n, mean, 5.5, 0.35, rng.uniform(8, 16)), 2.0, None)
    base = np.convolve(base, np.ones(5) / 5, mode="same")   # skating speed doesn't change instantly
    bursts = np.zeros(n)
    bands = [("b18", 18 * MPH, 20 * MPH), ("b20", 20 * MPH, 22 * MPH), ("b22", 22 * MPH, vmax)]
    events = []
    for key, lo, hi in bands:
        if hi <= lo + 0.2:
            continue
        for _ in range(rng.poisson(rates_ps[key] * length_s)):
            peak = lo + 0.25 + (hi - lo - 0.4) * (rng.beta(1.2, 2.2) if key == "b22" else rng.uniform())
            events.append((key, peak))
    # place bursts so they don't overlap (each needs ~8 s)
    slots = rng.permutation(max(int(length_s // 8), 1))[: len(events)]
    for (key, peak), slot in zip(events, slots):
        start = int((slot * 8 + rng.uniform(-2, 0.5)) * HZ)
        prof = _burst(n, start, peak, rng.uniform(2.6, 4.2), rng.uniform(3.0, 5.0), rng.uniform(0.6, 1.2))
        bursts = np.maximum(bursts, prof)

    # scale the baseline (kept under the 18 mph band so burst counts stay exact)
    def soft_cap(x, knee=4.0):
        # compress smoothly toward the cap instead of flattening at it
        top = cap - knee
        return np.where(x > top, top + knee * np.tanh((x - top) / knee), np.maximum(x, 0))

    def dist(k):
        v = np.maximum(soft_cap(base * k), bursts)
        return v.sum() / HZ / 3600

    lo, hi = 0.2, 3.0
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if dist(mid) < target_km else (lo, mid)
    v = np.maximum(soft_cap(base * (lo + hi) / 2), bursts)
    return np.convolve(np.r_[v[0], v, v[-1]], np.ones(3) / 3, mode="valid")


def simulate_game(prof: Profile, seed: int, opp: str = "OPP", home: bool = True) -> Game:
    rng = np.random.default_rng(seed)

    # game-level variation
    toi = float(np.clip(rng.normal(prof.toi_pg_s, prof.toi_sd_s), 0.55 * prof.toi_pg_s, 1.4 * prof.toi_pg_s))
    toi = max(toi, 240.0)
    # game-to-game distance-per-60 variation, from the player's real last-10 CV
    l10 = prof.last10
    cv = float(l10["km_per60"].std() / l10["km_per60"].mean()) if len(l10) >= 3 else 0.03
    intensity = rng.normal(1.0, float(np.clip(cv, 0.012, 0.05)))
    burst_mult = rng.lognormal(0, 0.18)         # game-to-game burst variation

    # shifts (game-clock seconds on ice)
    mean_len = 54 if prof.is_d else 48
    lens = []
    while sum(lens) < toi:
        lens.append(float(np.clip(rng.normal(mean_len, 12), 18, 105)))
    lens[-1] -= sum(lens) - toi
    if lens[-1] < 12 and len(lens) > 1:
        lens[-2] += lens.pop()
    n = len(lens)

    # strength per shift (PP / PK shifts are drawn from the player's real share)
    strength = rng.choice(["es", "pp", "pk"], size=n, p=[prof.share["es"], prof.share["pp"], prof.share["pk"]])
    # periods: shifts split evenly in order; P3 gets the remainder
    per = np.minimum((np.arange(n) * 3) // n + 1, 3)

    # lay shifts out on the game clock with random rest between them
    plan = []
    for period in (1, 2, 3):
        idx = np.where(per == period)[0]
        on = sum(lens[i] for i in idx)
        gaps = rng.dirichlet(np.ones(len(idx) + 1)) * max(PERIOD_S - on, 0)
        clock = gaps[0]
        for j, i in enumerate(idx):
            plan.append(dict(period=period, start_s=clock, length_s=lens[i], strength=strength[i].upper()))
            clock += lens[i] + gaps[j + 1]
    return _run_shifts(prof, rng, pd.DataFrame(plan), intensity, burst_mult, opp, home, seed)


def _run_shifts(prof, rng, plan, intensity, burst_mult, opp, home, seed, source="simulated", game_id=None) -> Game:
    """Simulate the speed trace for each planned shift and measure it."""
    rows, traces = [], []
    cap = HSD_KMH - 0.4
    rates_ps = {k: v / 3600 * burst_mult for k, v in prof.bursts_ph.items()}
    fatigue = {1: 1.015, 2: 1.0, 3: 0.975}
    plan = plan.sort_values(["period", "start_s"]).reset_index(drop=True)
    prev_end = {}
    for i, r in enumerate(plan.itertuples()):
        L = float(r.length_s)
        v60 = prof.p60[r.strength.lower()] * intensity * fatigue.get(int(r.period), 0.97) * rng.normal(1.0, 0.06)
        tr = _shift_trace(rng, L, v60 * L / 3600, rates_ps, prof.vmax_kmh, cap)
        traces.append(tr)
        rest = r.start_s - prev_end[r.period] if r.period in prev_end else np.nan
        prev_end[r.period] = r.start_s + L
        rows.append(dict(shift=i + 1, period=int(r.period), start_s=float(r.start_s), length_s=L,
                         rest_before_s=rest, strength=r.strength))
    shifts = pd.DataFrame(rows)
    met = pd.DataFrame([shift_metrics(t) for t in traces])
    shifts = pd.concat([shifts, met], axis=1)
    g = Game(profile=prof, opp=opp, home=home, seed=seed, shifts=shifts, traces=traces)
    g.source, g.game_id = source, game_id
    return g


def simulate_real_shifts(prof: Profile, plan: pd.DataFrame, seed: int, opp: str, home: bool, game_id: int) -> Game:
    """Skating load for a REAL game: the player's real shifts (timing, length, game state),
    with a speed trace per shift calibrated to his real EDGE profile."""
    rng = np.random.default_rng(seed)
    l10 = prof.last10
    cv = float(l10["km_per60"].std() / l10["km_per60"].mean()) if len(l10) >= 3 else 0.03
    intensity = rng.normal(1.0, float(np.clip(cv, 0.012, 0.05)))
    return _run_shifts(prof, rng, plan, intensity, rng.lognormal(0, 0.18), opp, home, seed, source="real shifts", game_id=game_id)


# ------------------------------------------------------------------ metrics
def _events(mask):
    """Start indices of contiguous True runs."""
    m = np.asarray(mask, dtype=int)
    return np.flatnonzero(np.diff(np.r_[0, m]) == 1), np.flatnonzero(np.diff(np.r_[m, 0]) == -1)


def shift_metrics(v: np.ndarray) -> dict:
    dt = 1 / HZ
    d_m = v / 3.6 * dt                                   # metres per sample
    out = {"dist_m": d_m.sum(), "max_kmh": v.max(),
           "hsd_m": d_m[v >= HSD_KMH].sum(), "sprint_m": d_m[v >= SPRINT_KMH].sum()}
    # bursts: each excursion above 18 mph, classified by its peak (EDGE bands)
    s, e = _events(v >= HSD_KMH)
    peaks = np.array([v[a:b + 1].max() for a, b in zip(s, e)])
    out["b18"] = int(((peaks >= 18 * MPH) & (peaks < 20 * MPH)).sum())
    out["b20"] = int(((peaks >= 20 * MPH) & (peaks < 22 * MPH)).sum())
    out["b22"] = int((peaks >= 22 * MPH).sum())
    # high-intensity accelerations / decelerations: mean rate over 1 s at or beyond the threshold
    a = (v[HZ:] - v[:-HZ]) / 3.6
    out["acc"] = len(_events(a >= ACC_MS2)[0])
    out["dec"] = len(_events(a <= -ACC_MS2)[0])
    for name, lo, hi in ZONES:
        out[name] = d_m[(v >= lo) & (v < hi)].sum()
    out["m_per_min"] = out["dist_m"] / (len(v) * dt / 60)
    return out


def game_summary(g: Game) -> dict:
    s = g.shifts
    toi = s["length_s"].sum()
    return {
        "toi_s": toi, "shifts": len(s), "avg_shift_s": s["length_s"].mean(),
        "avg_rest_s": s["rest_before_s"].mean(),
        "dist_km": s["dist_m"].sum() / 1000, "m_per_min": s["dist_m"].sum() / (toi / 60),
        "km_per60": s["dist_m"].sum() / 1000 / toi * 3600,
        "hsd_m": s["hsd_m"].sum(), "sprint_m": s["sprint_m"].sum(),
        "b18": int(s["b18"].sum()), "b20": int(s["b20"].sum()), "b22": int(s["b22"].sum()),
        "bursts": int(s[["b18", "b20", "b22"]].sum().sum()),
        "max_kmh": s["max_kmh"].max(), "pct_vmax": s["max_kmh"].max() / g.profile.vmax_kmh,
        "acc": int(s["acc"].sum()), "dec": int(s["dec"].sum()),
    }


def by_period(g: Game) -> pd.DataFrame:
    s = g.shifts
    agg = s.groupby("period").agg(toi_s=("length_s", "sum"), shifts=("shift", "count"), dist_m=("dist_m", "sum"),
                                  hsd_m=("hsd_m", "sum"), sprint_m=("sprint_m", "sum"), b18=("b18", "sum"),
                                  b20=("b20", "sum"), b22=("b22", "sum"), max_kmh=("max_kmh", "max"),
                                  acc=("acc", "sum"), dec=("dec", "sum"))
    agg["m_per_min"] = agg["dist_m"] / (agg["toi_s"] / 60)
    return agg.reset_index()


def zone_totals(g: Game) -> pd.DataFrame:
    return pd.DataFrame({"zone": [z[0] for z in ZONES], "dist_m": [g.shifts[z[0]].sum() for z in ZONES]})


def vs_baseline(g: Game) -> pd.DataFrame:
    """Compare the simulated game with the player's real season and last-10-game data.

    Between-game SD comes from the real last 10 games (distance, TOI, distance per 60).
    SWC (smallest worthwhile change) = 0.2 x between-game SD.
    """
    p, sm, l10 = g.profile, game_summary(g), g.profile.last10
    rows = []

    def add(metric, value, season, sd, unit, fmt):
        z = (value - season) / sd if sd and np.isfinite(sd) and sd > 0 else np.nan
        swc = 0.2 * sd if sd and np.isfinite(sd) else np.nan
        if not np.isfinite(z):
            flag = "n/a"
        elif abs(value - season) <= swc:
            flag = "Typical"
        elif z >= 1:
            flag = "High"
        elif z <= -1:
            flag = "Low"
        else:
            flag = "Above" if z > 0 else "Below"
        rows.append(dict(metric=metric, game=value, season=season, sd=sd, swc=swc, z=z, flag=flag, unit=unit, fmt=fmt))

    has = len(l10) >= 3
    add("Distance (km)", sm["dist_km"], p.expected_km, l10["km"].std() if has else 0.1 * p.expected_km, "km", "{:.2f}")
    add("TOI (min)", sm["toi_s"] / 60, p.toi_pg_s / 60, p.toi_sd_s / 60, "min", "{:.1f}")
    add("Distance per 60 (km)", sm["km_per60"], p.p60["all"], l10["km_per60"].std() if has else 0.04 * p.p60["all"], "km", "{:.2f}")
    bpg = sum(p.bursts_pg.values())
    add("Bursts 18+ mph", sm["bursts"], bpg, max(np.sqrt(bpg), 0.5), "#", "{:.0f}")
    return pd.DataFrame(rows)
