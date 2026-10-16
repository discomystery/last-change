"""How many draws an average faceoff man would have won in each player's spots, for the adjusted faceoff score.

A plain win share punishes a centre whose draws are hard: the shorthanded centre wins only about 46% of draws, the
power-play centre about 55%, and some centres keep drawing against the league's best. So every regular-season faceoff
of this season and last goes into one logistic model (seconds don't matter here, each draw is one row): a rating for
each player who takes draws (+1 for the home man, -1 for the visitor), plus home ice, the home side's manpower
(power play / penalty kill) and the zone, ridge-penalized (C = 0.1, chosen on held-out half-seasons of 2023-26).

For each draw, the expected win for a player is the chance that an AVERAGE faceoff man (the draw-weighted mean rating)
wins against this opponent, at this end of the ice, at this manpower, home or away. Summed per game that is `fo_xw`;
a player's adjusted win share is 50 + 100 * (won - fo_xw) / draws.

Tested 2023-24 to 2025-26, fitting on one half of each season's games and predicting the other half's draws: the
ratings beat plain win shares (shrunk toward 50%, with their scale fitted) in all six halves (log loss 0.6835 vs
0.6853; no player information at all: 0.6922). Jordan Staal over 2025-27: 55.8% as recorded, 56.3% adjusted, 29th ->
14th of 192 forwards; defensive centres rise, scoring centres who rarely kill penalties fall.
"""
import numpy as np
import polars as pl
import scipy.sparse as sp
from sklearn.linear_model import LogisticRegression

from pipeline.config import REGULAR, TABLES

C = 0.1


def _draws(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return pl.DataFrame()
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
    ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "type", "is_home", "p1", "p2", "zone", "situation_code"])
    f = ev.filter((pl.col("type") == "faceoff") & pl.col("p1").is_not_null() & pl.col("p2").is_not_null() & pl.col("is_home").is_not_null()).join(games, on="game_id")
    # The play-by-play files a faceoff under the winner: p1 won, p2 lost, is_home and zone are the winner's.
    code = pl.col("situation_code").fill_null("1551")
    away_n, home_n = code.str.slice(1, 1).cast(pl.Int32, strict=False), code.str.slice(2, 1).cast(pl.Int32, strict=False)
    goalies = (code.str.slice(0, 1) == "1") & (code.str.slice(3, 1) == "1")  # a pulled goalie's extra man isn't a power play
    return f.select(
        "game_id",
        hp=pl.when(pl.col("is_home")).then(pl.col("p1")).otherwise(pl.col("p2")),
        ap=pl.when(pl.col("is_home")).then(pl.col("p2")).otherwise(pl.col("p1")),
        y=pl.col("is_home"),
        hz=pl.when(pl.col("is_home")).then(pl.col("zone")).otherwise(pl.col("zone").replace({"O": "D", "D": "O"})),
        pp=goalies & (home_n > away_n), pk=goalies & (home_n < away_n))


def _context(f: pl.DataFrame) -> np.ndarray:
    """The home side's situation: power play, penalty kill, offensive-zone draw, defensive-zone draw."""
    return np.column_stack([f["pp"].to_numpy(), f["pk"].to_numpy(), (f["hz"] == "O").to_numpy(), (f["hz"] == "D").to_numpy()]).astype(float)


def fit(f: pl.DataFrame) -> tuple[dict[int, float], np.ndarray, float]:
    """Player ratings (logit), context weights and the intercept (home ice)."""
    ids = sorted(set(f["hp"].to_list()) | set(f["ap"].to_list()))
    idx = {p: i for i, p in enumerate(ids)}
    n = f.height
    h = np.array([idx[p] for p in f["hp"]])
    a = np.array([idx[p] for p in f["ap"]])
    X = sp.csr_matrix((np.r_[np.ones(n), -np.ones(n)], (np.r_[np.arange(n), np.arange(n)], np.r_[h, a])), shape=(n, len(ids)))
    X = sp.hstack([X, sp.csr_matrix(_context(f))]).tocsr()
    m = LogisticRegression(C=C, max_iter=2000).fit(X, f["y"].to_numpy())
    coef = m.coef_[0]
    return dict(zip(ids, coef[: len(ids)])), coef[len(ids):], float(m.intercept_[0])


def expected(season: int) -> pl.DataFrame:
    """game_id, player_id, fo_xw: draws an average faceoff man would have won in this player's draws that game."""
    now = _draws(season)
    if now.is_empty():
        return pl.DataFrame(schema={"game_id": pl.Int64, "player_id": pl.Int64, "fo_xw": pl.Float64})
    both = pl.concat([x for x in (_draws(season - 1), now) if not x.is_empty()])
    skill, ctx_w, b = fit(both)
    # The average faceoff man: every draw counts both of the players in it.
    takers = pl.concat([both["hp"], both["ap"]])
    mu = float(np.mean([skill[p] for p in takers]))
    s_h = np.array([skill[p] for p in now["hp"]])
    s_a = np.array([skill[p] for p in now["ap"]])
    base = b + _context(now) @ ctx_w
    sig = lambda z: 1 / (1 + np.exp(-z))
    home_xw = sig(base + mu - s_a)        # an average man in the home player's spot
    away_xw = 1 - sig(base + s_h - mu)    # an average man in the visiting player's spot
    rows = pl.concat([
        pl.DataFrame({"game_id": now["game_id"], "player_id": now["hp"], "fo_xw": home_xw}),
        pl.DataFrame({"game_id": now["game_id"], "player_id": now["ap"], "fo_xw": away_xw})])
    return rows.group_by("game_id", "player_id").agg(pl.col("fo_xw").sum())
