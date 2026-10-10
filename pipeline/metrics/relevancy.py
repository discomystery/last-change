"""How much a team leans on each skater: one number for "who are this team's stars", used to pick the players
previews, post-game pages, team pages and Worth knowing notes talk about.

Two parts, equal weight, each as a z-score against last full season's regulars (all skaters together):
  * Scoring reliance: his share of the team's goals (goals plus assists) in the games he plays, so a star on a
    low-scoring team counts as much as one on a high-scoring team.
  * Impact: his isolated 5-on-5 effect on chances for and against (RAPM, three seasons) times his 5-on-5 ice time,
    in expected goals per game.
Percentiles and tiers are within his position group (forwards or defensemen); the score is on one scale for both.
Ice time on its own is left out: once both parts are in, it does not help predict how a team does without him.
Splitting scoring share by position was tried and dropped: it predicted no better and filled the top with defensemen.

Why equal weight (study over 2023-24 to 2025-26: 737 player-seasons with 3+ missed games, last season's numbers
predicting this season's with-and-without gap): scoring reliance predicts the gap in real goals and wins best,
impact the gap in expected goals; each adds to the other and ice time adds nothing. One point of score is worth
about 0.07-0.12 expected goals a game and 2.5 percentage points of win rate when he sits.

This season is blended with last (last season counts as at most 30 games, at 0.8, and at 0.6 of that for a
player on a new team), then shrunk toward the position average with 10 games. Players listed on a roster who
have not dressed yet keep last season's standing on the club that lists them, so a hurt star still counts.

With and without him: the team's results in games he missed are recorded as facts, with an estimate of how much
of the gap is really him. Those gaps are mostly bounces (true spread between players about 0.12 expected goals a
game, against about 0.38 from chance for a ten-game absence; 7% of gaps clear the 5% chance bar), so the estimate
leans hard on the player's relevancy and only moves far when the absence is long. It never feeds the number.
"""
from functools import lru_cache

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, TABLES
from pipeline.metrics import rapm

RAPM_LAMBDA = 40000.0  # same ridge penalty as player pages (metrics/players.py)
RAPM_SEASONS = 3  # impact from three seasons: steadier than two, and closer to expert rankings (ESPN rho 0.54 -> 0.66)
LAST_SEASON_GAMES = 30  # last season counts as at most this many games beside this season's
LAST_SEASON_WEIGHT = 0.8
MOVED_WEIGHT = 0.6  # a player on a new team: his old scoring share carries over less (year-to-year r 0.80 vs 0.88)
SHRINK_GAMES = 10  # games of a league-average share added to every player's own, so five-game starts stay modest
REGULAR_GP = 20  # games for a season to define the league's scale and percentiles
# With-and-without: true spread of the gap between players beyond what relevancy explains, and its slope on the
# relevancy score, in expected goals per game (fitted in the study above).
GAP_SLOPE = 0.10
GAP_TRUE_SD = 0.12
GAME_SD = 1.18  # within-team spread of one game's expected-goal difference (all situations, empty nets out)
TIERS = [(98.5, "franchise"), (88, "star"), (70, "core"), (35, "regular"), (0, "depth")]


def _group(pos: str) -> str:
    return "D" if pos == "D" else "F"


@lru_cache(maxsize=16)
def _games(season: int) -> pl.DataFrame:
    return pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date")


@lru_cache(maxsize=16)
def _team_games(season: int) -> pl.DataFrame:
    """One row per team-game: goals and expected goals each way (empty nets out of expected goals)."""
    tg = pl.read_parquet(TABLES / str(season) / "team_game.parquet").join(_games(season), on="game_id")
    return tg.group_by("game_id", "team_id", "date").agg(
        pl.col("gf").sum().alias("tgf"), pl.col("ga").sum().alias("tga"),
        pl.col("xgf").filter(pl.col("strength") != "EN").sum().alias("txgf"),
        pl.col("xga").filter(pl.col("strength") != "EA").sum().alias("txga"))


def _player_games(season: int, before: str | None) -> pl.DataFrame:
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet").join(_games(season), on="game_id")
    pg = pg.join(_team_games(season).select("game_id", "team_id", "tgf"), on=["game_id", "team_id"])
    return pg.filter(pl.col("date") < before) if before else pg


def season_lines(season: int, before: str | None = None) -> pl.DataFrame:
    """Per player and season: his latest team, games, points, team goals in his games, 5-on-5 seconds a game."""
    pg = _player_games(season, before)
    latest = pg.sort("date").group_by("player_id").agg(pl.col("team_id").last().alias("team_id"), pl.col("pos").last().alias("pos"))
    return pg.group_by("player_id").agg(
        pl.len().alias("gp"), pl.col("g").sum(), (pl.col("g") + pl.col("a1") + pl.col("a2")).sum().alias("pts"),
        pl.col("tgf").sum(), pl.col("sec5").mean().alias("sec5")).join(latest, on="player_id")


def _scale(season: int) -> dict:
    """League scale from the last full season's regulars: means and spreads by position group, and the score list
    every player is ranked against (so the scale does not wobble with five games of this season)."""
    lines = season_lines(season)
    r = rapm.run(_rapm_seasons(season), RAPM_LAMBDA)
    d = lines.filter(pl.col("gp") >= REGULAR_GP).join(r.select("player_id", "off", "def"), on="player_id", how="left")
    d = d.with_columns(share=pl.col("pts") / pl.col("tgf"), impact=(pl.col("off").fill_null(0) + pl.col("def").fill_null(0)) * pl.col("sec5") / 3600,
                       grp=pl.col("pos").map_elements(_group, return_dtype=pl.Utf8))
    out = {"share": (float(d["share"].mean()), float(d["share"].std())), "means": {}}
    for g in ("F", "D"):
        out["means"][g] = float(d.filter(pl.col("grp") == g)["share"].mean())
    out["impact"] = (float(d["impact"].mean()), float(d["impact"].std()))
    z = _score(d, out)
    out["pool"] = {g: np.sort(z[(d["grp"] == g).to_numpy()]) for g in ("F", "D")}
    return out


def _score(d: pl.DataFrame, sc: dict) -> np.ndarray:
    zs = (d["share"].to_numpy() - sc["share"][0]) / sc["share"][1]
    zi = (d["impact"].to_numpy() - sc["impact"][0]) / sc["impact"][1]
    return 0.5 * zs + 0.5 * zi


def run(season: int = CURRENT_SEASON, before: str | None = None) -> pl.DataFrame:
    """Relevancy for every skater who has played this season (before a date, if given), blending in last season."""
    sc = _scale(season - 1)
    now = season_lines(season, before)
    last = _last_full(season)
    # Players who have not dressed yet this season but are listed on a club's roster (hurt, scratched, held out)
    # still matter: they keep last season's standing on the club that lists them.
    listed = _listed(season, before).join(last.select("player_id", "pos_l"), on="player_id").join(now.select("player_id"), on="player_id", how="anti")
    now = pl.concat([now, listed.select("player_id", pl.lit(0, pl.UInt32).alias("gp"), pl.lit(0.0).alias("g"), pl.lit(0.0).alias("pts"),
                                        pl.lit(0.0).alias("tgf"), pl.lit(None, pl.Float64).alias("sec5"), "team_id", pl.col("pos_l").alias("pos"))], how="vertical_relaxed")
    d = now.join(last, on="player_id", how="left").with_columns(pl.col("gp_l", "g_l", "pts_l", "tgf_l").fill_null(0))
    moved = (pl.col("team_id_l").is_not_null() & (pl.col("team_id_l") != pl.col("team_id")))
    c = (pl.min_horizontal(pl.lit(1.0), LAST_SEASON_GAMES / pl.col("gp_l").cast(pl.Float64).clip(1)) * LAST_SEASON_WEIGHT
         * pl.when(moved).then(MOVED_WEIGHT).otherwise(1.0))
    d = d.with_columns(c=c, moved=moved.fill_null(False), grp=pl.col("pos").map_elements(_group, return_dtype=pl.Utf8))
    d = d.with_columns(eff_gp=pl.col("gp") + pl.col("c") * pl.col("gp_l"), pts_b=pl.col("pts") + pl.col("c") * pl.col("pts_l"),
                       tgf_b=pl.col("tgf") + pl.col("c") * pl.col("tgf_l"))
    # Shrink toward the position group's average share with SHRINK_GAMES games of a typical team's scoring.
    gpg = float(d["tgf"].sum() / max(d["gp"].sum(), 1)) or 3.0
    mean = sc["means"]
    d = d.with_columns(share=(pl.col("pts_b") + pl.col("grp").replace_strict(mean) * SHRINK_GAMES * gpg) / (pl.col("tgf_b") + SHRINK_GAMES * gpg))
    sec5 = (pl.col("sec5").fill_null(0) * pl.col("gp") + pl.col("sec5_l").fill_null(0) * pl.col("c") * pl.col("gp_l")) / pl.col("eff_gp")
    r = rapm.run(_rapm_seasons(season), RAPM_LAMBDA) if before is None else _rapm_before(season, before)
    d = d.join(r.select("player_id", "off", "def"), on="player_id", how="left").with_columns(
        sec5_b=sec5, impact=(pl.col("off").fill_null(0) + pl.col("def").fill_null(0)) * sec5 / 3600)
    z = _score(d, sc)
    # Percentile and tier among his own position group (a star defenseman next to star defensemen); the score itself
    # stays on one scale for both, so a team's ranking mixes forwards and defensemen fairly.
    pct = np.array([100.0 * np.searchsorted(sc["pool"][g], v, side="right") / len(sc["pool"][g]) for v, g in zip(z, d["grp"].to_list())])
    d = d.with_columns(score=pl.Series(z), pct=pl.Series(np.round(pct, 1)))
    d = d.with_columns(tier=pl.col("pct").map_elements(lambda p: next(t for cut, t in TIERS if p >= cut), return_dtype=pl.Utf8),
                       team_rank=pl.col("score").rank("ordinal", descending=True).over("team_id"))
    return d.select("player_id", "team_id", "pos", "gp", "gp_l", "moved", "share", "impact", "off", "def", "sec5_b", "score", "pct", "tier", "team_rank")


def _last_full(season: int) -> pl.DataFrame:
    """Last season's lines, reaching back one more season for a player who missed most of last season
    (Barkov sat out all of 2025-26 and would otherwise start this season as an unknown)."""
    cols = ("gp", "g", "pts", "tgf", "sec5", "team_id", "pos")
    last = season_lines(season - 1)
    if (TABLES / str(season - 2) / "player_game.parquet").exists():
        older = season_lines(season - 2).filter(pl.col("gp") >= REGULAR_GP)
        swap = older.join(last.filter(pl.col("gp") >= REGULAR_GP).select("player_id"), on="player_id", how="anti")
        last = pl.concat([last.join(swap.select("player_id"), on="player_id", how="anti"), swap.select(last.columns)])
    return last.rename({c: f"{c}_l" for c in cols})


def _rapm_seasons(season: int) -> list[int]:
    # Never before 2023-24: event timing was recorded differently earlier, which our expected goals cannot bridge.
    return [s for s in range(max(season - RAPM_SEASONS + 1, FULL_SEASONS[0]), season + 1) if (TABLES / str(s) / "stints.parquet").exists()]


def _listed(season: int, before: str | None) -> pl.DataFrame:
    """Latest roster listing (on or before the day before `before`) as player_id, team_id."""
    path = TABLES / str(season) / "rosters.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"player_id": pl.Int64, "team_id": pl.Int64})
    r = pl.read_parquet(path)
    r = r.filter(pl.col("date") < before) if before else r
    if r.is_empty():
        return pl.DataFrame(schema={"player_id": pl.Int64, "team_id": pl.Int64})
    r = r.filter(pl.col("date") == r["date"].max())
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    ids = pl.concat([g.select(pl.col("home").alias("team"), pl.col("home_id").alias("team_id")), g.select(pl.col("away").alias("team"), pl.col("away_id").alias("team_id"))]).unique()
    return r.join(ids, on="team").select("player_id", "team_id").unique("player_id")


def _rapm_before(season: int, before: str) -> pl.DataFrame:
    """Two-season ratings using only games before a date (for pages frozen before puck drop)."""
    keep = _games(season).filter(pl.col("date") < before)["game_id"]
    st = rapm.stretches(season)
    full = pl.concat([rapm.stretches(s) for s in _rapm_seasons(season) if s != season] + [st.filter(pl.col("game_id").is_in(keep))])
    X, y, w, players, _ = rapm.design(full)
    m = rapm.fit(X, y, w, RAPM_LAMBDA, len(players), intervals=False)
    n = len(players)
    return pl.DataFrame({"player_id": players, "off": m["beta"][:n], "def": -m["beta"][n: 2 * n]})


def with_without(season: int, rel: pl.DataFrame, before: str | None = None) -> pl.DataFrame:
    """Team results in games each player missed while on the team (this season and last, current team only):
    games, record and expected-goal difference a game with and without him, the raw gap and how much of it is
    likely him (shrunk toward what his relevancy predicts)."""
    rows = []
    score = dict(zip(rel["player_id"].to_list(), rel["score"].to_list()))
    team_now = dict(zip(rel["player_id"].to_list(), rel["team_id"].to_list()))
    for s in (season - 1, season):
        b = before if s == season else None
        tg = _team_games(s)
        tg = tg.filter(pl.col("date") < b) if b else tg
        pg = _player_games(s, b).select("player_id", "team_id", "game_id", "date")
        span = pg.group_by("player_id", "team_id").agg(pl.col("date").min().alias("d0"), pl.col("date").max().alias("d1"), pl.col("game_id"))
        for pid, tid, d0, d1, mine in span.iter_rows():
            if team_now.get(pid) != tid:
                continue
            # His time with the club: from his first game to the team's latest game if he is still there this season.
            end = tg.filter(pl.col("team_id") == tid)["date"].max() if s == season else d1
            t = tg.filter((pl.col("team_id") == tid) & (pl.col("date") >= d0) & (pl.col("date") <= end))
            w = t.filter(pl.col("game_id").is_in(mine))
            wo = t.filter(~pl.col("game_id").is_in(mine))
            if wo.height == 0 or w.height == 0:
                continue
            rows.append({"player_id": pid, "season": s, "with_gp": w.height, "without_gp": wo.height,
                         "with_w": int((w["tgf"] > w["tga"]).sum()), "without_w": int((wo["tgf"] > wo["tga"]).sum()),
                         "with_xgd": float((w["txgf"] - w["txga"]).sum()), "without_xgd": float((wo["txgf"] - wo["txga"]).sum())})
    if not rows:
        return pl.DataFrame()
    d = pl.DataFrame(rows).group_by("player_id").agg(pl.all().exclude("player_id", "season").sum(), pl.col("season").alias("seasons"))
    d = d.with_columns(gap=pl.col("with_xgd") / pl.col("with_gp") - pl.col("without_xgd") / pl.col("without_gp"))
    prior = np.array([GAP_SLOPE * score.get(p, 0.0) for p in d["player_id"].to_list()])
    se2 = GAME_SD ** 2 * (1 / d["without_gp"].to_numpy() + 1 / d["with_gp"].to_numpy())
    k = GAP_TRUE_SD ** 2 / (GAP_TRUE_SD ** 2 + se2)
    est = prior + k * (d["gap"].to_numpy() - prior)
    z = d["gap"].to_numpy() / np.sqrt(se2)
    return d.with_columns(likely_him=pl.Series(np.round(est, 3)), weight_on_gap=pl.Series(np.round(k, 2)), gap_z=pl.Series(np.round(z, 2)))


if __name__ == "__main__":  # python -m pipeline.metrics.relevancy: the league's top 60 and each club's top four
    r = run()
    names = pl.read_parquet(TABLES / str(CURRENT_SEASON) / "players.parquet").unique("player_id", keep="last").select("player_id", "last")
    r = r.join(names, on="player_id", how="left").sort("score", descending=True)
    print(r.head(60).select("last", "team_id", "pos", "gp", "pct", "tier"))
