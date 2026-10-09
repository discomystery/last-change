"""Skater ratings against others at his position (forwards with forwards, defensemen with defensemen).

Each trait is a rate built from per-game sums in player_game. Small samples are pulled toward the position
average by an amount set from split-half reliability (odd vs even games of past seasons), the same way team
traits are. "blend" also leans on the player's own previous season; "season" uses this season only.
Likely ranges are 80% bootstrap ranges over games. Percentiles compare with regulars at the same position.
"""
import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, TABLES

COLS = ["sec", "sec5", "sec_pp", "icf5", "ixg5", "ixg_pp", "ixg_net", "iff_net", "g_net", "a1_5", "a1_pp", "on_xgf_adj", "on_xga_adj",
        "t_xgf_adj", "t_xga_adj", "t_sec5", "hits", "hits_adj", "takes", "takes_adj", "blocks", "blocks_adj", "gives", "gives_adj", "fow", "fol",
        "pen_taken", "pen_drawn"]
C = {c: i for i, c in enumerate(COLS)}
GROUP = {"C": "F", "L": "F", "R": "F", "D": "D"}
POOL_GAMES = {"blend": 20, "season": 3}  # games (this season, plus last in blend mode) to count as a regular at the position
BOOTSTRAPS = 200
MAX_K_FACTOR = 4.0  # never let the prior outweigh four seasons of a regular's sample


def _rate(num: str, den: str, mult: float = 3600.0):
    return lambda s: mult * s[..., C[num]] / s[..., C[den]]


def _rel(col: str):
    """With him on the ice minus without him, per 60 at 5-on-5 (team's own totals, score-and-venue adjusted)."""
    def f(s):
        on = s[..., C[col]] / s[..., C["sec5"]]
        off = (s[..., C["t_" + col[3:]]] - s[..., C[col]]) / (s[..., C["t_sec5"]] - s[..., C["sec5"]])
        return 3600 * (on - off)
    return f


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
    "onOffense": (_rel("on_xgf_adj"), "sec5", True, None),
    "onDefense": (_rel("on_xga_adj"), "sec5", False, None),
    "powerPlay": (_rate("ixg_pp", "sec_pp"), "sec_pp", True, {"blend": 3600, "season": 600}),
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


def _groups(pg: pl.DataFrame) -> dict[int, str]:
    """Each player's position group: the one he played most games at."""
    top = pg.group_by("player_id", "pos").len().sort("len", descending=True).unique("player_id", keep="first")
    return {r["player_id"]: GROUP[r["pos"]] for r in top.iter_rows(named=True)}


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
    stab = stabilization()
    now_pg, last_pg = load(season), load(season - 1)
    now, last = _matrices(now_pg), _matrices(last_pg)
    grp = {**_groups(last_pg), **_groups(now_pg)}
    rng = np.random.default_rng(season)

    # Position averages from last season's full sample (the prior everything is pulled toward).
    mu = {}
    for g in ("F", "D"):
        tot = sum(m.sum(axis=0) for pid, m in last.items() if grp.get(pid) == g)
        mu[g] = {t: float(_value(t, tot)) for t in stab[g]}
        for t in ("onOffense", "onDefense"):
            mu[g][t] = 0.0  # on-off differences average out to zero

    def estimate(t: str, g: str, s_now: np.ndarray, s_prev: np.ndarray | None, mode: str) -> np.ndarray:
        k = stab[g][t]["k"]
        n, v = _weight(t, s_now), np.nan_to_num(_value(t, s_now))
        prior = mu[g][t]
        if mode == "blend" and s_prev is not None:
            n_p, v_p = _weight(t, s_prev), np.nan_to_num(_value(t, s_prev))
            prior = (n_p * v_p + k * prior) / (n_p + k)
        return (n * v + k * prior) / (n + k)

    players = {}
    for pid, m in now.items():
        g = grp[pid]
        prev = last.get(pid)
        s_now, s_prev = m.sum(axis=0), None if prev is None else prev.sum(axis=0)
        w_now = rng.multinomial(len(m), np.full(len(m), 1 / len(m)), size=BOOTSTRAPS)
        boot_now = w_now @ m
        boot_prev = None if prev is None else rng.multinomial(len(prev), np.full(len(prev), 1 / len(prev)), size=BOOTSTRAPS) @ prev
        p = {"group": g, "games": len(m), "games_last": 0 if prev is None else len(prev), "sums": {"season": s_now, "last": s_prev}, "est": {}, "boot": {}}
        for mode in ("blend", "season"):
            p["est"][mode], p["boot"][mode] = {}, {}
            for t in stab[g]:
                p["est"][mode][t] = float(estimate(t, g, s_now, s_prev, mode))
                p["boot"][mode][t] = estimate(t, g, boot_now, boot_prev, mode)
        players[pid] = p

    def weight_in(p: dict, t: str, mode: str) -> float:
        s = p["sums"]["season"] if mode == "season" or p["sums"]["last"] is None else p["sums"]["season"] + p["sums"]["last"]
        return float(_weight(t, s))

    def regular(p: dict, mode: str) -> bool:
        return p["games"] + (p["games_last"] if mode == "blend" else 0) >= POOL_GAMES[mode]

    out = {}
    for mode in ("blend", "season"):
        for g in ("F", "D"):
            members = [pid for pid, p in players.items() if p["group"] == g]
            for t, (_, _, higher, min_w) in TRAITS.items():
                if t not in stab[g]:
                    continue
                ok = lambda p: regular(p, mode) and (min_w is None or weight_in(p, t, mode) >= min_w[mode])
                pool = np.array([players[pid]["est"][mode][t] for pid in members if ok(players[pid])])
                mean = float(pool.mean()) if len(pool) else float("nan")
                raw_pool = None
                if t in RAW:
                    raw_vals = []
                    for pid in members:
                        if ok(players[pid]):
                            pp_ = players[pid]
                            s = pp_["sums"]["season"] if mode == "season" or pp_["sums"]["last"] is None else pp_["sums"]["season"] + pp_["sums"]["last"]
                            raw_vals.append(float(RAW[t](s)))
                    raw_pool = np.array(raw_vals)
                for pid in members:
                    p = players[pid]
                    v = p["est"][mode][t]
                    lo, hi = np.nanpercentile(p["boot"][mode][t], [10, 90])
                    a, b = sorted((_pct(lo, pool, higher), _pct(hi, pool, higher)))
                    rec = {"v": round(v, 3), "pct": round(_pct(v, pool, higher)), "lo": round(a), "hi": round(b), "ok": bool(ok(p)), "of": int(len(pool)),
                           "rank": int((pool > v).sum() + 1 if higher else (pool < v).sum() + 1)}
                    if min_w is not None and weight_in(p, t, mode) < min_w[mode]:
                        rec["ok"] = False
                    if t in RAW:
                        s = p["sums"]["season"] if mode == "season" or p["sums"]["last"] is None else p["sums"]["season"] + p["sums"]["last"]
                        raw = float(RAW[t](s)) if s[C["sec"]] > 0 else 0.0
                        rec |= {"index": round(100 * v / mean) if mean else None, "raw": round(raw, 2), "raw_pct": round(_pct(raw, raw_pool, higher))}
                    out.setdefault(pid, {"group": g, "games": p["games"], "games_last": p["games_last"], "traits": {}})["traits"].setdefault(t, {})[mode] = rec
    for pid, p in players.items():
        out[pid]["totals"] = {"season": {c: float(p["sums"]["season"][i]) for c, i in C.items()},
                              "last": None if p["sums"]["last"] is None else {c: float(p["sums"]["last"][i]) for c, i in C.items()}}
    return {"season": season, "stabilization": stab, "means": mu, "players": out}
