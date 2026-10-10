"""Team style fingerprint: real values, league percentiles and likely ranges, in two modes.

"season" uses this season's games only. "blend" pulls each team toward its own previous season,
by an amount that depends on how repeatable the trait is (measured from split halves of past seasons).
"""
import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, TABLES
from pipeline.metrics import adjust, rink_bias

# name -> (numerator columns, denominator columns, multiplier, higher is "more/better" for the percentile)
DIMS = {
    "volume": (["cf_adj"], ["sec5"], 3600, True),
    "quality": (["xgf_adj"], ["ff_adj"], 1, True),
    "suppression": (["ca_adj"], ["sec5"], 3600, False),
    "qualityAllowed": (["xga_adj"], ["fa_adj"], 1, False),
    "pace": (["ff_adj", "fa_adj"], ["sec5"], 3600, True),
    "rebounds": (["reb_xgf"], ["xgf5"], 100, True),
    "turnover": (["to_xgf"], ["xgf5"], 100, True),
    "point": (["point_cf"], ["cf5"], 100, True),
    "pp": (["pp_xgf"], ["pp_sec"], 3600, True),
    "pk": (["pk_xga"], ["pk_sec"], 3600, False),
    "powerKill": (["pk_xgf"], ["pk_sec"], 3600, True),
    "goalie": (["g_saved"], ["g_sec"], 3600, True),
    "breakdowns": (["bd_a"], ["sec5"], 3600, False),
    "discipline": (["pp_opps", "neg_short"], ["game_sec"], 3600, True),
    "physical": (["hits_adj"], ["close_sec"], 3600, True),
    "forecheck": (["fc_adj"], ["sec5"], 3600, True),
    "depth": (["bottom6_sec"], ["fwd_sec"], 100, True),
    # The same two traits exactly as recorded, with no arena correction (shown when the visitor picks "Raw").
    "physical_raw": (["hits_close"], ["close_sec"], 3600, True),
    "forecheck_raw": (["fc_raw"], ["sec5"], 3600, True),
}
INDEXED = {"physical": "physical_raw", "forecheck": "forecheck_raw"}  # shown as a score against league average
COLS = sorted({c for num, den, _, _ in DIMS.values() for c in num + den})
BOOTSTRAPS = 300
MAX_K = 400.0


def per_game(season: int, w: pl.DataFrame) -> pl.DataFrame:
    """One row per team per regular-season game with every sum the fingerprint needs."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date", "home_id", "away_id")
    arena = _arena().select(pl.col("team_id").alias("home_id"), pl.col("hits").alias("f_hits"), pl.col("gives").alias("f_gives"), pl.col("takes").alias("f_takes"))
    games = games.join(arena, on="home_id", how="left").with_columns(pl.col("f_hits", "f_gives", "f_takes").fill_null(1.0))
    t = adjust.apply(pl.read_parquet(d / "team_game.parquet"), w).join(games, on="game_id")
    close = (pl.col("strength") == "5v5") & (pl.col("score_state").abs() <= 1)
    fc_raw = pl.col("oz_hits") + pl.col("oz_takes") + pl.col("forced_gives")
    hr = rink_bias.home_road()

    def venue(event: str, own: bool = True):
        # Weight for an event credited to this team (own) or to its opponent, given who is at home.
        at_home = pl.col("is_home") if own else ~pl.col("is_home")
        return pl.when(at_home).then(hr[event]["w_home"]).otherwise(hr[event]["w_away"])

    fc_adj = (pl.col("oz_hits") / pl.col("f_hits") * venue("hits") + pl.col("oz_takes") / pl.col("f_takes") * venue("takes")
              + pl.col("forced_gives") / pl.col("f_gives") * venue("gives", own=False))
    disc = pl.read_parquet(d / "discipline.parquet")
    opp = disc.join(games.select("game_id", "home_id", "away_id"), on="game_id").with_columns(
        team_id=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("away_id")).otherwise(pl.col("home_id"))).select("game_id", "team_id", pl.col("times_short").alias("pp_opps"))
    disc = disc.join(opp, on=["game_id", "team_id"]).with_columns(pl.col("pp_opps", "game_sec").cast(pl.Float64), neg_short=-pl.col("times_short").cast(pl.Float64)).drop("times_short")
    s5, pp, pk, net = pl.col("strength") == "5v5", pl.col("strength") == "5v4", pl.col("strength") == "4v5", pl.col("strength") != "EA"

    def tot(col, cond):
        return pl.when(cond).then(pl.col(col)).otherwise(0.0).sum()

    return t.group_by("game_id", "team_id", "date").agg(
        tot("sec", s5).alias("sec5"), tot("cf_adj", s5).alias("cf_adj"), tot("ca_adj", s5).alias("ca_adj"),
        tot("ff_adj", s5).alias("ff_adj"), tot("fa_adj", s5).alias("fa_adj"), tot("xgf_adj", s5).alias("xgf_adj"), tot("xga_adj", s5).alias("xga_adj"),
        tot("xgf", s5).alias("xgf5"), tot("cf", s5).alias("cf5"), tot("reb_xgf", s5).alias("reb_xgf"),
        tot("to_xgf", s5).alias("to_xgf"), tot("point_cf", s5).alias("point_cf"),
        tot("sec", pp).alias("pp_sec"), tot("xgf", pp).alias("pp_xgf"), tot("sec", pk).alias("pk_sec"), tot("xga", pk).alias("pk_xga"), tot("xgf", pk).alias("pk_xgf"),
        tot("sec", net).alias("g_sec"), (tot("xga", net) - tot("ga", net)).alias("g_saved"),
        tot("bd_a", s5).alias("bd_a"), tot("sec", close).alias("close_sec"), tot("hits", close).alias("hits_close"),
        (pl.when(close).then(pl.col("hits") / pl.col("f_hits") * venue("hits")).otherwise(0.0).sum()).alias("hits_adj"),
        (pl.when(s5).then(fc_raw).otherwise(0.0).sum()).alias("fc_raw"), (pl.when(s5).then(fc_adj).otherwise(0.0).sum()).alias("fc_adj"),
    ).join(disc, on=["game_id", "team_id"]).join(pl.read_parquet(d / "depth.parquet"), on=["game_id", "team_id"]).sort("team_id", "date", "game_id")


_ARENA = None


def _arena() -> pl.DataFrame:
    global _ARENA
    if _ARENA is None:
        _ARENA = rink_bias.factors()
    return _ARENA


def _value(sums: dict, dim: str) -> float:
    num, den, mult, _ = DIMS[dim]
    d = sum(sums[c] for c in den)
    return float("nan") if d == 0 else mult * sum(sums[c] for c in num) / d


def _matrix(df: pl.DataFrame) -> dict[int, np.ndarray]:
    return {tid: g.select(COLS).to_numpy() for (tid,), g in df.group_by("team_id", maintain_order=True)}


def _values(mat: np.ndarray) -> dict[str, float]:
    return {dim: _value(dict(zip(COLS, mat.sum(axis=0))), dim) for dim in DIMS}


def stabilization(w: pl.DataFrame) -> dict[str, dict]:
    """How many games it takes for a trait to be half signal, from odd/even game splits of past team-seasons."""
    halves = {dim: ([], []) for dim in DIMS}
    for season in FULL_SEASONS:
        for mat in _matrix(per_game(season, w)).values():
            odd, even = _values(mat[::2]), _values(mat[1::2])
            for dim in DIMS:
                halves[dim][0].append(odd[dim])
                halves[dim][1].append(even[dim])
    out = {}
    for dim, (a, b) in halves.items():
        r = float(np.corrcoef(a, b)[0, 1])
        out[dim] = {"r": round(r, 3), "k_games": round(min(MAX_K, 41 * (1 - r) / r) if r > 0.05 else MAX_K, 1)}
    return out


def _pct(value: float, league: np.ndarray, higher: bool) -> float:
    below = (league < value).mean() if higher else (league > value).mean()
    return float(np.clip(100 * below, 1, 99))


def run(season: int = CURRENT_SEASON, idle: bool = False) -> dict:
    """With `idle`, teams that have not played yet this season are included too: their blended estimate is last
    season pulled toward the league, and their season-only estimate is empty (None). Used for previews rebuilt as of
    a past date, when some teams had yet to open their season."""
    w = adjust.weights()
    k = stabilization(w)
    now, last = _matrix(per_game(season, w)), _matrix(per_game(season - 1, w))
    if idle:
        now |= {tid: np.empty((0, len(COLS))) for tid in last if tid not in now}
    league_last = {dim: float(np.nanmean([_values(m)[dim] for m in last.values()])) for dim in DIMS}
    rng = np.random.default_rng(season)

    def estimates(mat: np.ndarray, tid: int) -> dict[str, dict[str, float]]:
        n = len(mat)
        obs = _values(mat) if n else {dim: None for dim in DIMS}
        prev = _values(last[tid]) if tid in last else league_last
        out = {"season": obs, "blend": {}}
        for dim in DIMS:
            kk = k[dim]["k_games"]
            n_prev = len(last[tid]) if tid in last else 0
            prior = (n_prev * prev[dim] + kk * league_last[dim]) / (n_prev + kk)  # last season, itself pulled toward the league
            out["blend"][dim] = (n * obs[dim] + kk * prior) / (n + kk) if n else prior
        return out

    point = {tid: estimates(mat, tid) for tid, mat in now.items()}
    boots = {tid: [estimates(mat[rng.integers(0, len(mat), len(mat))], tid) for _ in range(BOOTSTRAPS)] for tid, mat in now.items() if len(mat)}

    teams = {}
    for tid, mat in now.items():
        dims = {}
        for dim, (_, _, _, higher) in DIMS.items():
            dims[dim] = {}
            for mode in ("blend", "season"):
                ok = lambda x: x is not None and not np.isnan(x)
                league = np.array([point[t][mode][dim] for t in now if ok(point[t][mode][dim])])
                v = point[tid][mode][dim]
                if not ok(v):  # no games yet (or, a game or two in, none of what this trait counts): nothing to show
                    dims[dim][mode] = {"v": None, "pct": None, "lo": None, "hi": None, "rank": None, "index": None}
                    continue
                draws = np.array([b[mode][dim] for b in boots[tid]]) if tid in boots else np.array([v])
                lo, hi = np.nanpercentile(draws, [10, 90])
                p_lo, p_hi = sorted((_pct(lo, league, higher), _pct(hi, league, higher)))
                better = (league > v).sum() if higher else (league < v).sum()
                dims[dim][mode] = {"v": round(v, 4), "pct": round(100 * (len(league) - better - 0.5) / len(league)), "lo": round(p_lo), "hi": round(p_hi), "rank": int(better) + 1,
                                   "index": round(100 * v / float(np.mean(league))) if np.mean(league) else None}
        teams[tid] = {"games": len(mat), "dims": dims}
    return {"season": season, "stabilization": k, "weights": w.to_dicts(), "teams": teams}
