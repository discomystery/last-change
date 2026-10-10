"""Skater ratings against others at his position (forwards with forwards, defensemen with defensemen).

Each trait is a rate built from per-game sums in player_game. Small samples are pulled toward the position
average by an amount set from split-half reliability (odd vs even games of past seasons), the same way team
traits are. "blend" also leans on the player's own previous season; "season" uses this season only.
Likely ranges are 80% bootstrap ranges over games. Percentiles compare with regulars at the same position.
"""
import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, TABLES

Z80 = 1.2816

COLS = ["sec", "sec5", "sec_pp", "icf5", "ixg5", "ixg_pp", "ixg_net", "iff_net", "g_net", "a1_5", "a1_pp", "on_xgf_adj", "on_xga_adj",
        "t_xgf_adj", "t_xga_adj", "t_sec5", "hits", "hits_adj", "takes", "takes_adj", "blocks", "blocks_adj", "gives", "gives_adj", "fow", "fol",
        "pen_taken", "pen_drawn", "sec_pk", "sh_xgf"]
C = {c: i for i, c in enumerate(COLS)}
GROUP = {"C": "F", "L": "F", "R": "F", "D": "D"}
SUB = {"C": "C", "L": "W", "R": "W", "D": "D"}
# Traits where centres and wingers really differ (standardized gap of 0.3 or more over 2024-26 regulars): these compare
# centres with centres and wingers with wingers. Everything else compares all forwards together.
SPLIT = {"shooting", "chances", "blocks"}
# How much of last season to carry for a player who has changed teams, per trait. Measured 2023-24 to 2025-26: the
# slope of next season on this season for forwards who moved, divided by the slope for those who stayed. Personal
# habits (shooting, hitting, penalties) carry fully; traits that depend on linemates and role carry partly.
CARRY_IF_MOVED = {"powerPlay": 0.35, "finishing": 0.45, "playmaking": 0.5, "faceoffs": 0.6}
RAPM_LAMBDA = 40000.0  # ridge penalty in seconds of 5v5 play, chosen by cross-validation on held-out 2025-26 games
POOL_GAMES = {"blend": 20, "season": 3}  # games (this season, plus last in blend mode) to count as a regular at the position
BOOTSTRAPS = 200
MAX_K_FACTOR = 4.0  # never let the prior outweigh four seasons of a regular's sample


def _rate(num: str, den: str, mult: float = 3600.0):
    return lambda s: mult * s[..., C[num]] / s[..., C[den]]


def _faceoffs(s):
    return 100 * s[..., C["fow"]] / (s[..., C["fow"]] + s[..., C["fol"]])


def _finishing(s):
    return 100 * (s[..., C["g_net"]] - s[..., C["ixg_net"]]) / s[..., C["iff_net"]]


def _faceoff_weight(s):
    return s[..., C["fow"]] + s[..., C["fol"]]


# key -> (value function, weight column or function, higher is better, minimum weight for the pool in blend / season)
TRAITS = {
    "shooting": (_rate("icf5", "sec5"), "sec5", True, None),
    "chances": (_rate("ixg5", "sec5"), "sec5", True, None),
    "finishing": (_finishing, "iff_net", True, None),
    "playmaking": (_rate("a1_5", "sec5"), "sec5", True, None),
    "powerPlay": (_rate("ixg_pp", "sec_pp"), "sec_pp", True, {"blend": 3600, "season": 600}),
    "shThreat": (_rate("sh_xgf", "sec_pk"), "sec_pk", True, {"blend": 1800, "season": 300}),
    "hits": (_rate("hits_adj", "sec"), "sec", True, None),
    "blocks": (_rate("blocks_adj", "sec"), "sec", True, None),
    "takeaways": (_rate("takes_adj", "sec"), "sec", True, None),
    "drawsPenalties": (_rate("pen_drawn", "sec"), "sec", True, None),
    "discipline": (_rate("pen_taken", "sec"), "sec", False, None),
    "faceoffs": (_faceoffs, _faceoff_weight, True, {"blend": 200, "season": 40}),
}
RAW = {"hits": _rate("hits", "sec"), "blocks": _rate("blocks", "sec"), "takeaways": _rate("takes", "sec")}  # as recorded, for the Raw switch
INDEXED = set(RAW)


def _weight(trait: str, s: np.ndarray) -> np.ndarray:
    w = TRAITS[trait][1]
    return w(s) if callable(w) else s[..., C[w]]


def load(season: int) -> pl.DataFrame:
    path = TABLES / str(season) / "player_game.parquet"
    if not path.exists():  # e.g. a fresh checkout of the data branch, which predates this table
        from pipeline.metrics import player_game
        player_game.build(season)
    return pl.read_parquet(path).filter(pl.col("pos") != "G").sort("player_id", "game_id")


def _matrices(pg: pl.DataFrame) -> dict[int, np.ndarray]:
    return {pid: g.select(COLS).to_numpy() for (pid,), g in pg.group_by("player_id", maintain_order=True)}


def _groups(pg: pl.DataFrame, table: dict[str, str] = GROUP) -> dict[int, str]:
    """Each player's position group (or, with SUB, centre / wing / defense): the one he played most games at."""
    top = pg.group_by("player_id", "pos").len().sort("len", descending=True).unique("player_id", keep="first")
    return {r["player_id"]: table[r["pos"]] for r in top.iter_rows(named=True)}


def _teams(pg: pl.DataFrame, latest: bool = False) -> dict[int, int]:
    """Each player's team in a season: the one he played the most games for, or with latest=True the one he plays for now."""
    t = pg.group_by("player_id", "team_id").agg(pl.len().alias("n"), pl.col("game_id").max().alias("last"))
    t = t.sort("last", descending=True) if latest else t.sort(["n", "last"], descending=True)
    return dict(zip(*t.unique("player_id", keep="first").select("player_id", "team_id").to_dict(as_series=False).values()))


def peer(group: str, sub: str, trait: str) -> str:
    """Who a player is compared with for one trait: F, D, or for SPLIT traits C or W."""
    return sub if group == "F" and trait in SPLIT else group


def _value(trait: str, s: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return TRAITS[trait][0](s)


def stabilization() -> dict[str, dict[str, dict]]:
    """Per position group and trait: the weight (seconds, shots or draws) at which a sample is half signal."""
    halves = {g: {t: ([], [], []) for t in TRAITS} for g in ("F", "D")}
    for season in FULL_SEASONS:
        pg = load(season)
        grp = _groups(pg)
        for pid, m in _matrices(pg).items():
            if len(m) < 40:
                continue
            odd, even = m[::2].sum(axis=0), m[1::2].sum(axis=0)
            for t in TRAITS:
                a, b = _value(t, odd), _value(t, even)
                w = min(_weight(t, odd), _weight(t, even))
                if np.isfinite(a) and np.isfinite(b) and w > 0:
                    halves[grp[pid]][t][0].append(a)
                    halves[grp[pid]][t][1].append(b)
                    halves[grp[pid]][t][2].append(w)
    out = {}
    for g, traits in halves.items():
        out[g] = {}
        for t, (a, b, w) in traits.items():
            if len(a) < 30:
                continue  # e.g. faceoffs for defensemen: too few take them to rate
            r = float(np.corrcoef(a, b)[0, 1])
            half = float(np.median(w))
            k = half * (1 - r) / r if r > 0.02 else half * MAX_K_FACTOR * 2
            out[g][t] = {"r": round(r, 3), "k": round(min(k, half * 2 * MAX_K_FACTOR), 1), "half": round(half, 1)}
    return out


def _pct(v: float, pool: np.ndarray, higher: bool) -> float:
    if not np.isfinite(v) or len(pool) == 0:
        return float("nan")
    below = (pool < v).sum() + 0.5 * (pool == v).sum()
    p = below / len(pool) if higher else 1 - below / len(pool)
    return float(np.clip(100 * p, 1, 99))


def run(season: int = CURRENT_SEASON) -> dict:
    from pipeline.metrics import rapm

    stab = stabilization()
    now_pg, last_pg = load(season), load(season - 1)
    now, last = _matrices(now_pg), _matrices(last_pg)
    grp = {**_groups(last_pg), **_groups(now_pg)}
    sub = {**_groups(last_pg, SUB), **_groups(now_pg, SUB)}
    rng = np.random.default_rng(season)

    # Peer-group averages from last season's full sample (the prior everything is pulled toward).
    mu = {}
    for g, members in (("F", lambda p: grp.get(p) == "F"), ("D", lambda p: grp.get(p) == "D"), ("C", lambda p: sub.get(p) == "C"), ("W", lambda p: sub.get(p) == "W")):
        tot = sum(m.sum(axis=0) for pid, m in last.items() if members(pid))
        base = "D" if g == "D" else "F"
        mu[g] = {t: float(_value(t, tot)) for t in stab[base]}

    def estimate(t: str, g: str, pg_: str, s_now: np.ndarray, s_prev: np.ndarray | None, mode: str, moved: bool = False) -> np.ndarray:
        k = stab[g][t]["k"]
        n, v = _weight(t, s_now), np.nan_to_num(_value(t, s_now))
        prior = mu[pg_][t]
        if mode == "blend" and s_prev is not None:
            n_p, v_p = _weight(t, s_prev), np.nan_to_num(_value(t, s_prev))
            if moved:
                n_p = n_p * CARRY_IF_MOVED.get(t, 1.0)
            prior = (n_p * v_p + k * prior) / (n_p + k)
        return (n * v + k * prior) / (n + k)

    team_now, team_last = _teams(now_pg, latest=True), _teams(last_pg)
    players = {}
    for pid, m in now.items():
        g, sb = grp[pid], sub[pid]
        prev = last.get(pid)
        moved = prev is not None and team_last.get(pid) != team_now.get(pid)
        s_now, s_prev = m.sum(axis=0), None if prev is None else prev.sum(axis=0)
        boot_now = rng.multinomial(len(m), np.full(len(m), 1 / len(m)), size=BOOTSTRAPS) @ m
        boot_prev = None if prev is None else rng.multinomial(len(prev), np.full(len(prev), 1 / len(prev)), size=BOOTSTRAPS) @ prev
        p = {"group": g, "sub": sb, "moved_from": team_last.get(pid) if moved else None, "games": len(m), "games_last": 0 if prev is None else len(prev), "sums": {"season": s_now, "last": s_prev}, "est": {}, "boot": {}}
        for mode in ("blend", "season"):
            p["est"][mode], p["boot"][mode] = {}, {}
            for t in stab[g]:
                pg_ = peer(g, sb, t)
                p["est"][mode][t] = float(estimate(t, g, pg_, s_now, s_prev, mode, moved))
                p["boot"][mode][t] = estimate(t, g, pg_, boot_now, boot_prev, mode, moved)
        players[pid] = p

    def both(p: dict, mode: str) -> np.ndarray:
        return p["sums"]["season"] if mode == "season" or p["sums"]["last"] is None else p["sums"]["season"] + p["sums"]["last"]

    def regular(p: dict, mode: str) -> bool:
        return p["games"] + (p["games_last"] if mode == "blend" else 0) >= POOL_GAMES[mode]

    out = {pid: {"group": p["group"], "sub": p["sub"], "moved_from": p["moved_from"], "games": p["games"], "games_last": p["games_last"], "traits": {}} for pid, p in players.items()}
    for mode in ("blend", "season"):
        for t, (_, _, higher, min_w) in TRAITS.items():
            peers: dict[str, list[int]] = {}
            for pid, p in players.items():
                if t in stab[p["group"]]:
                    peers.setdefault(peer(p["group"], p["sub"], t), []).append(pid)
            for pg_, members in peers.items():
                ok = lambda p: regular(p, mode) and (min_w is None or float(_weight(t, both(p, mode))) >= min_w[mode])
                # Everyone is placed against the spread of regulars over this season and last. A this-season-only
                # estimate is pulled toward average, so early on it sits near the middle until the games add up.
                ok_ref = lambda p: regular(p, "blend") and (min_w is None or float(_weight(t, both(p, "blend"))) >= min_w["blend"])
                pool = np.array([players[pid]["est"]["blend"][t] for pid in members if ok_ref(players[pid])])
                mean = float(pool.mean()) if len(pool) else float("nan")
                raw_pool = np.array([float(RAW[t](both(players[pid], "blend"))) for pid in members if ok_ref(players[pid])]) if t in RAW else None
                for pid in members:
                    p = players[pid]
                    v = p["est"][mode][t]
                    lo, hi = np.nanpercentile(p["boot"][mode][t], [10, 90])
                    a, b = sorted((_pct(lo, pool, higher), _pct(hi, pool, higher)))
                    rec = {"v": round(v, 3), "pct": round(_pct(v, pool, higher)), "lo": round(a), "hi": round(b), "ok": bool(ok(p)), "of": int(len(pool)), "vs": pg_,
                           "rank": int((pool > v).sum() + 1 if higher else (pool < v).sum() + 1)}
                    if t in RAW:
                        s_ = both(p, mode)
                        raw = float(RAW[t](s_)) if s_[C["sec"]] > 0 else 0.0
                        rec |= {"index": round(100 * v / mean) if mean else None, "raw": round(raw, 2), "raw_pct": round(_pct(raw, raw_pool, higher))}
                    out[pid]["traits"].setdefault(t, {})[mode] = rec

    # Isolated 5-on-5 impact (offense and defense) and the competition each player faces, from rapm.py.
    fits = {"blend": rapm.run([season - 1, season], RAPM_LAMBDA), "season": rapm.run([season], RAPM_LAMBDA)}
    ratings = fits["blend"]
    comp = {"blend": rapm.competition([season - 1, season], ratings), "season": rapm.competition([season], ratings)}
    for mode, fit_ in fits.items():
        est = {r["player_id"]: r for r in fit_.iter_rows(named=True)}
        for key, col in (("offImpact", "off"), ("defImpact", "def")):
            for g in ("F", "D"):
                members = [pid for pid, p in players.items() if p["group"] == g and pid in est]
                ref = {r["player_id"]: r[col] for r in fits["blend"].iter_rows(named=True)}
                pool = np.array([ref[pid] for pid in members if pid in ref and regular(players[pid], "blend")])
                for pid in members:
                    v, sd = est[pid][col], est[pid][f"{col}_sd"]
                    a, b = sorted((_pct(v - Z80 * sd, pool, True), _pct(v + Z80 * sd, pool, True)))
                    out[pid]["traits"].setdefault(key, {})[mode] = {"v": round(v, 3), "pct": round(_pct(v, pool, True)), "lo": round(a), "hi": round(b), "ok": regular(players[pid], mode),
                                                                     "of": int(len(pool)), "vs": g, "rank": int((pool > v).sum() + 1), "sd": round(sd, 3)}
        c = comp[mode]
        for g in ("F", "D"):
            members = [pid for pid, p in players.items() if p["group"] == g and pid in c]
            pools = {k: np.array([c[pid][k] for pid in members if regular(players[pid], mode) and c[pid][k] is not None]) for k in ("all", "home", "road")}
            for pid in members:
                out[pid].setdefault("competition", {})[mode] = {k: None if c[pid][k] is None else {"v": round(c[pid][k], 3), "pct": round(_pct(c[pid][k], pools[k], True))} for k in ("all", "home", "road")}
    for pid, p in players.items():
        out[pid]["totals"] = {"season": {c_: float(p["sums"]["season"][i]) for c_, i in C.items()},
                              "last": None if p["sums"]["last"] is None else {c_: float(p["sums"]["last"][i]) for c_, i in C.items()}}
    return {"season": season, "stabilization": stab, "means": mu, "players": out}
