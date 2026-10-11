"""Isolated 5-on-5 impact: what a skater adds to his team's chances and takes away from the opponent's,
after accounting for his linemates, the players he faces, where his shifts start and the score.

This is regularized adjusted plus-minus (RAPM), the method public sites such as Evolving-Hockey use. Every
5-on-5 stretch with the same ten skaters is two rows: one per team attacking. The target is that team's
expected goals per 60 in the stretch; each row has a +1 for each attacking skater (offense columns), a +1 for
each defending skater (defense columns), and columns for home ice, the attacking team's score state and the
faceoff that started the stretch. Rows are weighted by length. Ridge regression pulls every player toward
average (zero), harder the less he has played, so small samples cannot produce extreme ratings.

Defense is reported so that positive is good (expected goals per 60 taken away from the opponent).
Likely ranges come from the ridge posterior: the diagonal of sigma^2 (X'WX + lambda I)^-1.

Ratings that remember (`chained`, 2026-10-11): instead of starting every player at zero from a two-season window,
each season is fitted starting from the player's rating at the end of the season before (the ridge pulls toward
that rating instead of toward zero), chained back to 2023-24. A player who misses a season keeps his last rating.
Tested on play the ratings had not seen (weighted error of each 5v5 stretch's chances): predicting the second half
of 2025-26 from everything before it, the chained ratings explained 13% more than the two-season window (90% range
6-18%); predicting this season's first games from last season's ratings, 20% more (range 1-37%). With only one
earlier season (second half of 2024-25) they did 4% worse, so they pay off once two seasons are chained. The
penalty (80,000 s) and no decay between seasons were picked from a grid (40-240k, decay 0.7-1).
"""
from functools import lru_cache

import numpy as np
import polars as pl
from scipy import sparse

from pipeline.build.stints import PENALTY_SHOT
from pipeline.config import REGULAR, TABLES

UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
Z80 = 1.2816
FIRST_SEASON = 2023  # event timing was recorded differently before 2023-24, so the chain starts there
CHAIN_LAMBDA = 80000.0


@lru_cache(maxsize=8)
def stretches(season: int) -> pl.DataFrame:
    """One row per 5-on-5 stint: skaters, length, expected goals each way, score state and how it began."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
    st = pl.read_parquet(d / "stints.parquet").join(games, on="game_id").filter(
        (pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null() & (pl.col("duration") > 0))
    st = st.with_row_index("sid").select("sid", "game_id", "start", "end", "duration", "home_skaters", "away_skaters")
    ev = pl.read_parquet(d / "events.parquet").join(games, on="game_id")
    xg = pl.read_parquet(d / "shots_xg_own.parquet")

    # Expected goals in each stint: an event belongs to the stint with start < t <= end.
    shots = ev.filter(pl.col("type").is_in(UNBLOCKED) & pl.col("is_home").is_not_null() & ~pl.col("situation_code").is_in(list(PENALTY_SHOT))).join(xg, on=["game_id", "event_id"], how="left")
    shots = shots.select("game_id", (pl.col("sec").cast(pl.Float64) - 1e-6).alias("t"), "sec", "is_home", pl.col("xg").fill_null(0.0)).sort("t")
    keyed = st.select("game_id", pl.col("start").cast(pl.Float64).alias("t"), "sid", "end").sort("t")
    hit = shots.join_asof(keyed, on="t", by="game_id", strategy="backward", check_sortedness=False).filter(pl.col("sec") <= pl.col("end"))
    per = hit.group_by("sid").agg(pl.col("xg").filter(pl.col("is_home")).sum().alias("xg_home"), pl.col("xg").filter(~pl.col("is_home")).sum().alias("xg_away"))

    # Score before the stint, from the home side.
    goals = ev.filter((pl.col("type") == "goal") & pl.col("is_home").is_not_null()).select(
        "game_id", pl.col("sec").cast(pl.Float64).alias("t"), (pl.col("home_score") + pl.col("is_home").cast(pl.Int64)).alias("hs"),
        (pl.col("away_score") + (~pl.col("is_home")).cast(pl.Int64)).alias("as_")).sort("t")
    start_t = st.select("sid", "game_id", (pl.col("start").cast(pl.Float64) - 1e-6).alias("t")).sort("t")
    score = start_t.join_asof(goals, on="t", by="game_id", strategy="backward", check_sortedness=False).select("sid", (pl.col("hs").fill_null(0) - pl.col("as_").fill_null(0)).clip(-3, 3).alias("home_state"))

    # A faceoff at the very start of the stint: which zone, from the home side ("O" = home's attacking zone).
    fo = ev.filter(pl.col("type") == "faceoff").select("game_id", pl.col("sec").alias("start"), "zone", "is_home").unique(["game_id", "start"], keep="first")
    fo = fo.with_columns(home_zone=pl.when(pl.col("zone") == "N").then(pl.lit("N")).when((pl.col("zone") == "O") == pl.col("is_home")).then(pl.lit("O")).otherwise(pl.lit("D")))
    out = st.join(per, on="sid", how="left").join(score, on="sid", how="left").join(fo.select("game_id", "start", "home_zone"), on=["game_id", "start"], how="left")
    return out.with_columns(pl.col("xg_home", "xg_away").fill_null(0.0), pl.col("home_state").fill_null(0), pl.col("home_zone").fill_null("F"))


def design(stints: pl.DataFrame, weights: np.ndarray | None = None):
    """Sparse rows (two per stint), targets, row weights and the player list."""
    players = sorted({p for col in ("home_skaters", "away_skaters") for lst in stints[col].to_list() for p in lst})
    idx = {p: i for i, p in enumerate(players)}
    n_p = len(players)
    # Extra columns: home attacking, attacking team's score state (-3..3 without 0), start zone for the attacker (O, D, N; on the fly is the base).
    extra = ["home"] + [f"state{s}" for s in (-3, -2, -1, 1, 2, 3)] + ["zoneO", "zoneD", "zoneN"]
    ex = {c: 2 * n_p + i for i, c in enumerate(extra)}
    rows, cols = [], []
    y, w = [], []
    hs, as_, dur = stints["home_skaters"].to_list(), stints["away_skaters"].to_list(), stints["duration"].to_numpy()
    xh, xa, state, zone = stints["xg_home"].to_numpy(), stints["xg_away"].to_numpy(), stints["home_state"].to_numpy(), stints["home_zone"].to_list()
    sw = np.ones(len(dur)) if weights is None else weights
    r = 0
    for i in range(len(dur)):
        for home in (True, False):
            att, dfn = (hs[i], as_[i]) if home else (as_[i], hs[i])
            for p in att:
                rows.append(r); cols.append(idx[p])
            for p in dfn:
                rows.append(r); cols.append(n_p + idx[p])
            if home:
                rows.append(r); cols.append(ex["home"])
            s = int(state[i]) if home else -int(state[i])
            if s:
                rows.append(r); cols.append(ex[f"state{s}"])
            z = zone[i]
            if z in ("O", "D"):
                z = z if home else ("D" if z == "O" else "O")
            if z != "F":
                rows.append(r); cols.append(ex[f"zone{z}"])
            y.append(3600 * (xh[i] if home else xa[i]) / dur[i])
            w.append(dur[i] * sw[i])
            r += 1
    X = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(r, 2 * n_p + len(extra)))
    return X, np.array(y), np.array(w), players, extra


def fit(X, y, w, lam: float, n_players: int, *, intervals: bool = True, prior: np.ndarray | None = None) -> dict:
    """Weighted ridge with an unpenalized intercept and unpenalized context columns. With `prior` (one value per
    offense and defense column, 2 x n_players), player columns are pulled toward it instead of toward zero."""
    mean = np.average(y, weights=w)
    yc = y - mean
    W = sparse.diags(w)
    XtW = X.T @ W
    A = (XtW @ X).toarray()
    pen = np.zeros(A.shape[0])
    pen[: 2 * n_players] = lam
    A[np.diag_indices_from(A)] += pen + 1e-9
    rhs = XtW @ yc
    if prior is not None:
        rhs[: 2 * n_players] += lam * prior
    b = np.linalg.solve(A, rhs)
    out = {"beta": b, "mean": mean}
    if intervals:
        resid = yc - X @ b
        sigma2 = float(np.sum(w * resid ** 2) / np.sum(w))
        # Rows are seconds-weighted; one second of play is one unit of weight, so sigma2 is per-second variance.
        out["sd"] = np.sqrt(sigma2 * np.diag(np.linalg.inv(A)))
    return out


def cross_validate(stints: pl.DataFrame, lams: list[float], folds: int = 5) -> dict[float, float]:
    """Weighted squared error on held-out games for each penalty."""
    games = np.array(sorted(stints["game_id"].unique().to_list()))
    rng = np.random.default_rng(0)
    fold = dict(zip(games, rng.integers(0, folds, len(games))))
    X, y, w, players, _ = design(stints)
    g = np.repeat(stints["game_id"].to_numpy(), 2)
    f = np.array([fold[x] for x in g])
    err = {}
    for lam in lams:
        tot = 0.0
        for k in range(folds):
            tr, te = f != k, f == k
            m = fit(X[tr], y[tr], w[tr], lam, len(players), intervals=False)
            pred = m["mean"] + X[te] @ m["beta"]
            tot += float(np.sum(w[te] * (y[te] - pred) ** 2))
        err[lam] = tot / float(np.sum(w))
    return err


def run(seasons: list[int], lam: float, weights: dict[int, float] | None = None) -> pl.DataFrame:
    """Offense and defense impact per player (expected goals per 60), with 80% ranges and 5v5 minutes."""
    parts = [stretches(s).with_columns(pl.lit((weights or {}).get(s, 1.0)).alias("sw")) for s in seasons]
    st = pl.concat(parts)
    X, y, w, players, extra = design(st, st["sw"].to_numpy())
    m = fit(X, y, w, lam, len(players))
    n = len(players)
    secs = {}
    for col in ("home_skaters", "away_skaters"):
        for lst, d_ in zip(st[col].to_list(), st["duration"].to_list()):
            for p in lst:
                secs[p] = secs.get(p, 0) + d_
    return pl.DataFrame({
        "player_id": players, "sec5": [secs[p] for p in players],
        "off": m["beta"][:n], "off_sd": m["sd"][:n],
        "def": -m["beta"][n: 2 * n], "def_sd": m["sd"][n: 2 * n],  # positive = fewer chances against
    })


@lru_cache(maxsize=8)
def _chain(through: int) -> dict[int, tuple[float, float, float, float]]:
    """Each player's (offense, defense column, their sds) after chaining full seasons FIRST_SEASON..through."""
    if through < FIRST_SEASON or not (TABLES / str(through) / "stints.parquet").exists():
        return {}
    prior = _chain(through - 1)
    return {**prior, **_fit_from(stretches(through), prior)}


def _fit_from(st: pl.DataFrame, prior: dict[int, tuple[float, float, float, float]]) -> dict[int, tuple[float, float, float, float]]:
    if st.is_empty():
        return {}
    X, y, w, players, _ = design(st)
    n = len(players)
    mu = np.array([prior.get(p, (0.0, 0.0))[0] for p in players] + [prior.get(p, (0.0, 0.0))[1] for p in players])
    m = fit(X, y, w, CHAIN_LAMBDA, n, prior=mu)
    b, sd = m["beta"], m["sd"]
    return {p: (float(b[i]), float(b[n + i]), float(sd[i]), float(sd[n + i])) for i, p in enumerate(players)}


def chained(season: int, before: str | None = None) -> pl.DataFrame:
    """Ratings that remember: everything up to the end of last season carried forward, updated with this season's
    5-on-5 play (only games before `before` if given). Same columns as `run`; sec5 is this season's 5v5 seconds."""
    prior = _chain(season - 1)
    st = stretches(season) if (TABLES / str(season) / "stints.parquet").exists() else pl.DataFrame()
    if before is not None and not st.is_empty():
        keep = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("date") < before)["game_id"]
        st = st.filter(pl.col("game_id").is_in(keep))
    rated = {**prior, **_fit_from(st, prior)}
    secs: dict[int, float] = {}
    if not st.is_empty():
        for col in ("home_skaters", "away_skaters"):
            for lst, d_ in zip(st[col].to_list(), st["duration"].to_list()):
                for p in lst:
                    secs[p] = secs.get(p, 0) + d_
    ids = sorted(rated)
    return pl.DataFrame({
        "player_id": ids, "sec5": [secs.get(p, 0) for p in ids],
        "off": [rated[p][0] for p in ids], "off_sd": [rated[p][2] for p in ids],
        "def": [-rated[p][1] for p in ids], "def_sd": [rated[p][3] for p in ids],
    }, schema={"player_id": pl.Int64, "sec5": pl.Float64, "off": pl.Float64, "off_sd": pl.Float64, "def": pl.Float64, "def_sd": pl.Float64})


def competition(seasons: list[int], ratings: pl.DataFrame) -> dict[int, dict[str, float | None]]:
    """How dangerous the opponents a player faces are: the summed offense ratings of the five opposing skaters,
    averaged over his 5-on-5 time, in all games, at home and on the road (where the home coach has the last change)."""
    off = dict(zip(ratings["player_id"].to_list(), ratings["off"].to_list()))
    acc: dict[int, list[float]] = {}
    for s in seasons:
        st = stretches(s)
        for hs, as_, d in zip(st["home_skaters"].to_list(), st["away_skaters"].to_list(), st["duration"].to_list()):
            for mine, opp, home in ((hs, as_, True), (as_, hs, False)):
                q = sum(off.get(p, 0.0) for p in opp) * d
                for p in mine:
                    a = acc.setdefault(p, [0.0, 0.0, 0.0, 0.0])  # home seconds, home sum, road seconds, road sum
                    a[0 if home else 2] += d
                    a[1 if home else 3] += q
    return {p: {"all": (a[1] + a[3]) / (a[0] + a[2]), "home": a[1] / a[0] if a[0] >= 1800 else None, "road": a[3] / a[2] if a[2] >= 1800 else None}
            for p, a in acc.items()}
