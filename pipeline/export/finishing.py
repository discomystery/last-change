"""How well he finishes: goals against what his chances were worth, game by game since 2023-24 (player page, Shots).

Our best guess at his finishing after each game, counted from every regular-season unblocked shot at a goalie since
2023-24 (empty nets left out). Each season's expected goals are scaled to that season's league (league goals per
expected goal), since scoring rates move from year to year, so 1.0 means an average finisher of his time.

The guess is pulled toward average with K expected goals (a gamma prior, mean 1 and sd 1/sqrt(K)): best guess
(goals + K) / (expected + K), likely range from sqrt(goals + K) / (expected + K). K = 90 comes from the study for the
"Player Page Graph Ideas" mock-ups (Sora, 2026-10-31): true finishing skill spreads only about ±10% around average
(split-half covariance 2023-27), so it takes about 90 expected goals before a player's own record counts as much as
the league's. The page draws the line in the browser from the running totals saved here.

Inspired by Post Hockey's implied shooter talent chart; our own shots and chance model only.
"""
import polars as pl

from pipeline.config import TABLES
from pipeline.metrics import rapm

K = 90.0
REGULAR = 2


def _season(s: int) -> pl.DataFrame | None:
    pg, gm = TABLES / str(s) / "player_game.parquet", TABLES / str(s) / "games.parquet"
    if not pg.exists() or not gm.exists():
        return None
    g = pl.read_parquet(gm, columns=["game_id", "game_type", "date", "state"]).filter(
        (pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    d = pl.read_parquet(pg, columns=["game_id", "player_id", "g_net", "ixg_net"]).join(g.select("game_id", "date"), on="game_id")
    lg = d["g_net"].sum() / max(d["ixg_net"].sum(), 1e-9)  # league goals per expected goal that season
    return d.with_columns(x=pl.col("ixg_net") * lg, season=pl.lit(s))


def run(season: int, pids: set[int]) -> dict[int, dict]:
    """{player: {k, pts: [[date, goals so far, scaled expected goals so far], ...]}}, one point per game he played."""
    parts = [x for s in range(rapm.FIRST_SEASON, season + 1) if (x := _season(s)) is not None]
    if not parts:
        return {}
    d = (pl.concat(parts).filter(pl.col("player_id").is_in(list(pids)))
         .sort("player_id", "date", "game_id")
         .with_columns(G=pl.col("g_net").cum_sum().over("player_id"), X=pl.col("x").cum_sum().over("player_id")))
    out: dict[int, dict] = {}
    for (pid,), grp in d.group_by("player_id"):
        pts = [[str(r[0]), int(r[1]), round(float(r[2]), 2)] for r in grp.select("date", "G", "X").iter_rows()]
        if pts and pts[-1][2] > 0:
            out[int(pid)] = {"k": K, "pts": pts}
    return out


def estimate(goals: float, expected: float) -> tuple[float, float]:
    """Best guess and its standard deviation (goals per expected goal, 1 = average)."""
    return (goals + K) / (expected + K), (goals + K) ** 0.5 / (expected + K)
