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
"""
from functools import lru_cache

import numpy as np
import polars as pl
from scipy import sparse

from pipeline.config import REGULAR, TABLES

UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
Z80 = 1.2816


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
    shots = ev.filter(pl.col("type").is_in(UNBLOCKED) & pl.col("is_home").is_not_null()).join(xg, on=["game_id", "event_id"], how="left")
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


def fit(X, y, w, lam: float, n_players: int, *, intervals: bool = True) -> dict:
    """Weighted ridge with an unpenalized intercept and unpenalized context columns."""
    mean = np.average(y, weights=w)
    yc = y - mean
    W = sparse.diags(w)
    XtW = X.T @ W
    A = (XtW @ X).toarray()
    pen = np.zeros(A.shape[0])
    pen[: 2 * n_players] = lam
    A[np.diag_indices_from(A)] += pen + 1e-9
    b = np.linalg.solve(A, XtW @ yc)
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
