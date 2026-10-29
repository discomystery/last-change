"""Wins above replacement for skaters: what his season has been worth in the standings, against a fill-in.

Built from the ratings on player pages, not by adding up Swing (Sora, 2026-10-25, thread "Investigate WAR for the
site"; study in project files `war-study/findings.md`). Three parts, in goals, each against an average minute at his
position (forwards, defensemen) this season:
  5-on-5       his creating plus preventing ratings (rapm.chained, so teammates and opponents are accounted for)
               times his 5-on-5 ice time;
  power play   his power-play impact (rapm.power_play, every season since 2023-24) times his 5-on-4 ice time;
  penalties    power plays drawn minus taken, at PENALTY goals each.
Then the fill-in level (REPLACEMENT goals per 60 times his ice time) is added back, and goals become wins at
2 x goals per game / PYTH_X (the Pythagorean form of points and goals; shootout goals out).

Tested over 2023-24 to 2025-26 (last season's figure per minute times this season's ice time, summed per club):
explains 33% of the next season's goal difference per game, against 28% for points and 25% for summed Swing
(hockeystats.com reports 34% for its Box WAR on the same seasons). Penalties helped; the power play did not add to
that test but stays in because power-play goals are real goals and its rating held up on unseen games; finishing
(goals above expected) made it worse and is left out. Goalies are left out: a season of saves above expected
barely repeats (0.08 to 0.09), so a goalie WAR would be mostly bounces.

Range: the ratings' 80% ranges, carried through the same sums (5-on-5 and power play taken as independent).
"""
import json
import math

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, SITE_DATA, TABLES
from pipeline.metrics import players as P
from pipeline.metrics import rapm

PENALTY = 0.16  # net expected goals of a minor (measured 0.156-0.171 over 2023-25; game_scores.PENALTY)
# Fill-in level, goals per 60 against an average minute at the position: players outside each club's top 13
# forwards / top 7 defensemen by ice time, pooled over 2023-24 to 2025-26 (measure() recomputes it).
REPLACEMENT = {"F": -0.11, "D": -0.08}
TOP = {"F": 13, "D": 7}
Z80 = 1.2816
OUT = SITE_DATA / "war.json"


def _games(season: int) -> pl.DataFrame:
    g = pl.read_parquet(TABLES / str(season) / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    so = pl.col("last_period") == "SO"
    return g.with_columns(  # a shootout winner's extra goal is not a goal
        hs=pl.when(so & (pl.col("home_score") > pl.col("away_score"))).then(pl.col("home_score") - 1).otherwise(pl.col("home_score")),
        as_=pl.when(so & (pl.col("away_score") > pl.col("home_score"))).then(pl.col("away_score") - 1).otherwise(pl.col("away_score")))


def _team_seasons(season: int) -> pl.DataFrame:
    g = _games(season)
    sides = [g.select(team_id=pl.col(f"{s}_id"), gf=pl.col(a), ga=pl.col(b)) for s, a, b in (("home", "hs", "as_"), ("away", "as_", "hs"))]
    win = pl.when(pl.col("gf") > pl.col("ga")).then(1.0).when(pl.col("gf") < pl.col("ga")).then(0.0).otherwise(0.5)  # shootouts count as ties
    return pl.concat(sides).with_columns(w=win).group_by("team_id").agg(pl.col("gf").sum(), pl.col("ga").sum(), pl.len().alias("gp"), pl.col("w").mean())


def pyth_x() -> float:
    """The exponent in win share = GF^x / (GF^x + GA^x), fitted on every team-season of FULL_SEASONS (about 2.03)."""
    t = pl.concat([_team_seasons(s) for s in FULL_SEASONS])
    gf, ga, w = (t[c].cast(pl.Float64).to_numpy() for c in ("gf", "ga", "w"))
    xs = np.arange(1.5, 3.0, 0.001)
    err = [(((gf ** x) / (gf ** x + ga ** x) - w) ** 2).sum() for x in xs]
    return float(xs[int(np.argmin(err))])


def goals_per_win(season: int, x: float) -> float:
    t = _team_seasons(season)
    return 2 * (2 * t["gf"].sum() / t["gp"].sum()) / x if t["gp"].sum() else 2 * 6.0 / x


def parts(season: int) -> pl.DataFrame:
    """One row per skater and club this season: ice time and the three parts in goals, each centered on an average
    minute at his position, with their sds."""
    games = _games(season).select("game_id")
    pg = P.load(season).join(games, on="game_id").filter(pl.col("pos") != "G")
    t = pg.group_by("player_id", "team_id").agg(pl.col("sec", "sec5", "sec_pp", "pen_drawn", "pen_taken").sum(), pl.len().alias("gp"), pl.col("pos").mode().first())
    t = t.with_columns(grp=pl.when(pl.col("pos") == "D").then(pl.lit("D")).otherwise(pl.lit("F")))
    five = rapm.chained(season)
    pp = rapm.power_play(list(range(rapm.FIRST_SEASON, season + 1)), five).select("player_id", "pp", "pp_sd")
    t = t.join(five.select("player_id", "off", "def", "off_sd", "def_sd"), on="player_id", how="left").join(pp, on="player_id", how="left").with_columns(
        pl.col("off", "def", "pp", "off_sd", "def_sd", "pp_sd").fill_null(0.0))
    t = t.with_columns(ev=(pl.col("off") + pl.col("def")) * pl.col("sec5") / 3600, ppv=pl.col("pp") * pl.col("sec_pp") / 3600,
                       pen=PENALTY * (pl.col("pen_drawn") - pl.col("pen_taken")).cast(pl.Float64),
                       var=((pl.col("off_sd") ** 2 + pl.col("def_sd") ** 2) * (pl.col("sec5") / 3600) ** 2 + (pl.col("pp_sd") * pl.col("sec_pp") / 3600) ** 2))
    avg = lambda c: pl.col(c).sum().over("grp") / pl.col("sec").sum().over("grp") * pl.col("sec")
    return t.with_columns(*[(pl.col(c) - avg(c)).alias(c) for c in ("ev", "ppv", "pen")])


def measure() -> dict[str, float]:
    """REPLACEMENT recomputed: goals per 60 (above average) of players outside each club's top 13 forwards / 7
    defensemen by ice time, pooled over FULL_SEASONS."""
    t = pl.concat([parts(s).with_columns(season=pl.lit(s)) for s in FULL_SEASONS]).with_columns(gaa=pl.col("ev") + pl.col("ppv") + pl.col("pen"))
    t = t.with_columns(rk=pl.col("sec").rank("ordinal", descending=True).over("season", "team_id", "grp"))
    fill = t.filter(pl.col("rk") > pl.col("grp").replace_strict(TOP))
    r = fill.group_by("grp").agg((pl.col("gaa").sum() / pl.col("sec").sum() * 3600).alias("r"))
    return {g: round(v, 3) for g, v in zip(r["grp"], r["r"])}


def season_war(season: int, x: float) -> dict[int, dict]:
    """Every skater's WAR this season, in wins, with its parts, 80% range and rank at his position, and whether he is
    one of his club's regulars (top 13 forwards / 7 defensemen by ice time)."""
    gpw = goals_per_win(season, x)
    t = parts(season)
    t = t.with_columns(reg=pl.col("sec").rank("ordinal", descending=True).over("team_id", "grp") <= pl.col("grp").replace_strict(TOP))
    by = t.group_by("player_id", "grp").agg(pl.col("sec", "gp", "ev", "ppv", "pen", "var").sum(), pl.col("reg").any())
    by = by.with_columns(fill=-pl.col("grp").replace_strict(REPLACEMENT) * pl.col("sec") / 3600)
    by = by.with_columns(war=(pl.col("ev") + pl.col("ppv") + pl.col("pen") + pl.col("fill")) / gpw, sd=pl.col("var").sqrt() / gpw)
    by = by.with_columns(rank=pl.col("war").rank("min", descending=True).over("grp").cast(pl.Int64), of=pl.len().over("grp"))
    r = lambda v: round(v, 2)
    return {row["player_id"]: {"war": r(row["war"]), "lo": r(row["war"] - Z80 * row["sd"]), "hi": r(row["war"] + Z80 * row["sd"]),
                               "ev": r(row["ev"] / gpw), "pp": r(row["ppv"] / gpw), "pen": r(row["pen"] / gpw), "fill": r(row["fill"] / gpw),
                               "gp": row["gp"], "rank": row["rank"], "of": row["of"], "group": row["grp"], "reg": row["reg"]}
            for row in by.iter_rows(named=True)}


def scale(players: dict[int, dict]) -> dict[str, dict]:
    """The lane on player pages, per position: from the lowest (1st percentile, so 0 is always on it) to the highest WAR, with
    a typical regular (median of each club's top 13 forwards / 7 defensemen by ice time) marked."""
    out = {}
    for g in ("F", "D"):
        v = np.array([p["war"] for p in players.values() if p["group"] == g])
        reg = [p["war"] for p in players.values() if p["group"] == g and p["reg"]]
        if len(v):
            out[g] = {"lo": round(min(-0.1, float(np.percentile(v, 1))), 2), "hi": round(float(v.max()), 2), "reg": round(float(np.median(reg)), 2) if reg else None}
    return out


def run(season: int = CURRENT_SEASON) -> dict:
    """war.json: this season's and last season's WAR for every skater who played, plus the constants used."""
    x = pyth_x()
    out = {"pyth_x": round(x, 3), "replacement": REPLACEMENT, "seasons": {}}
    for s in (season, season - 1):
        if (TABLES / str(s) / "player_game.parquet").exists():
            players = season_war(s, x)
            out["seasons"][str(s)] = {"goals_per_win": round(goals_per_win(s, x), 2), "scale": scale(players), "players": {str(k): v for k, v in players.items()}}
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    return {s: len(v["players"]) for s, v in out["seasons"].items()}


def load() -> dict:
    """war.json as written by run(), or empty."""
    return json.loads(OUT.read_text()) if OUT.exists() else {"seasons": {}}


if __name__ == "__main__":
    print(measure())
