"""Faceoff ratings that account for who a centre took his draws against.

Win percentage alone flatters a centre who mostly faces weak opponents and punishes one sent out against the
league's best. Every draw of a season is fitted at once (a logistic model: each centre's strength, the opponent's,
and the end of the ice the draw is in), and each draw then gets the chance an average centre would have won it
against that opponent in that spot. A player's wins minus those expected wins, added to an even 50%, is his
faceoff rate against average opposition.

Tested 2026-10-13 (model plan Y4): predicting each season's second-half draws from its first half, this rate beat
plain win percentage pulled toward 50% in every full season (log loss 2023-24 0.68477 vs 0.68608, 2024-25 0.68635
vs 0.68737, 2025-26 0.68455 vs 0.68558, at 100 draws of pull for both) and matched the full head-to-head model.
"""
from functools import lru_cache

import numpy as np
import polars as pl
from scipy import sparse
from sklearn.linear_model import LogisticRegression

from pipeline.config import TABLES

PULL = 0.1  # ridge strength of the fit (sklearn's C): centres with few draws stay near average


@lru_cache(maxsize=8)
def expected(season: int) -> pl.DataFrame:
    """Per game and player: `fo_exp`, the draws an average centre would have won against the same opponents in the
    same spots. Empty when the season has no faceoffs saved."""
    path = TABLES / str(season) / "events.parquet"
    empty = pl.DataFrame(schema={"game_id": pl.Int64, "player_id": pl.Int64, "fo_exp": pl.Float64})
    if not path.exists():
        return empty
    f = (pl.read_parquet(path, columns=["game_id", "type", "p1", "p2", "is_home", "zone"])
         .filter((pl.col("type") == "faceoff") & (pl.col("p1").is_not_null() | pl.col("p2").is_not_null())))
    if f.is_empty():
        return empty
    # Recast each draw as home centre against away centre; the zone is read from the home side.
    f = f.with_columns(y=pl.col("is_home").cast(pl.Float64),
                       hc=pl.when(pl.col("is_home")).then(pl.col("p1")).otherwise(pl.col("p2")),
                       ac=pl.when(pl.col("is_home")).then(pl.col("p2")).otherwise(pl.col("p1")),
                       hz=pl.when(pl.col("zone") == "N").then(pl.lit("N")).when((pl.col("zone") == "O") == pl.col("is_home")).then(pl.lit("O")).otherwise(pl.lit("D")))
    both = f.filter(pl.col("hc").is_not_null() & pl.col("ac").is_not_null())
    ps = sorted(set(both["hc"].to_list()) | set(both["ac"].to_list()))
    ix = {p: i for i, p in enumerate(ps)}
    n, rows = len(ps), np.arange(both.height)
    M = sparse.csr_matrix((np.r_[np.ones(both.height), -np.ones(both.height)],
                           (np.r_[rows, rows], np.r_[[ix[p] for p in both["hc"]], [ix[p] for p in both["ac"]]])), shape=(both.height, n))
    zone = lambda df: np.column_stack([(df["hz"] == "O").to_numpy(), (df["hz"] == "D").to_numpy()]).astype(float)
    fit = LogisticRegression(C=PULL, max_iter=2000).fit(sparse.hstack([M, sparse.csr_matrix(zone(both))]).tocsr(), both["y"].to_numpy())
    rating = dict(zip(ps, fit.coef_[0][:n]))
    lean = zone(f) @ fit.coef_[0][n:] + fit.intercept_[0]  # how the spot tilts the draw toward the home centre
    # "Average" is the typical man across the dot, weighted by draws taken (the fit's zero is pulled toward players
    # who rarely take one, so the league's regular centres would all look above average against it).
    taken = np.r_[[rating[p] for p in both["hc"]], [rating[p] for p in both["ac"]]]
    ref = float(taken.mean())
    r_home = np.array([rating.get(p, ref) if p is not None else ref for p in f["hc"]]) - ref
    r_away = np.array([rating.get(p, ref) if p is not None else ref for p in f["ac"]]) - ref
    sig = lambda z: 1 / (1 + np.exp(-z))
    f = f.with_columns(e_home=pl.Series(sig(lean - r_away)), e_away=pl.Series(1 - sig(r_home + lean)))
    parts = [f.filter(pl.col("hc").is_not_null()).select("game_id", pl.col("hc").alias("player_id"), pl.col("e_home").alias("e")),
             f.filter(pl.col("ac").is_not_null()).select("game_id", pl.col("ac").alias("player_id"), pl.col("e_away").alias("e"))]
    return pl.concat(parts).group_by("game_id", "player_id").agg(pl.col("e").sum().alias("fo_exp"))


def attach(pg: pl.DataFrame, season: int) -> pl.DataFrame:
    """player_game rows with `fo_exp` added (0 for players who took no draws)."""
    return pg.join(expected(season), on=["game_id", "player_id"], how="left").with_columns(pl.col("fo_exp").fill_null(0.0))
