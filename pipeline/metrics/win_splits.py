"""A team's record under the game conditions that go with winning league-wide.

Checked over 2023-25 (7,870 team-games): how much more often teams win when a condition holds. Kept only
conditions with a clear league-wide link, plus power plays drawn, which fans expect to matter and which
league-wide does not. Team-specific splits were tested too: teams differ from the league pattern by about
as much as luck alone produces (split spread 0.10-0.13 against 0.11 from chance), so the site shows each
team's actual record beside the league's, and never presents a team's split as its own formula.
"""
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, TABLES
from pipeline.metrics import adjust, team_style

CONDITIONS = {
    "goalie": pl.col("g_saved") >= 0,
    "first": pl.col("scored_first"),
    "chances": pl.col("xgf_adj") >= pl.col("xga_adj"),
    "attempts": pl.col("cf_adj") >= pl.col("ca_adj"),
    "pp": pl.col("pp_opps") >= 3,
}


def games(season: int, w: pl.DataFrame) -> pl.DataFrame:
    """One row per team per regular-season game: the conditions and whether the team won (overtime and shootout count)."""
    d = TABLES / str(season)
    g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "home_id", "away_id", "home_score", "away_score")
    goals = pl.read_parquet(d / "goals.parquet").join(pl.read_parquet(d / "events.parquet").select("game_id", "event_id", "sort"), on=["game_id", "event_id"])
    first = goals.sort("game_id", "sort").group_by("game_id").agg(pl.col("team_id").first().alias("first_team"))
    return (
        team_style.per_game(season, w).join(g, on="game_id").join(first, on="game_id", how="left")
        .with_columns(
            win=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("home_score") > pl.col("away_score")).otherwise(pl.col("away_score") > pl.col("home_score")),
            scored_first=(pl.col("first_team") == pl.col("team_id")).fill_null(False),
        )
        .with_columns(**{k: v.fill_null(False) for k, v in CONDITIONS.items()})
        .select("game_id", "team_id", "win", *CONDITIONS)
    )


def _split(df: pl.DataFrame, cond: str) -> dict:
    yes, no = df.filter(pl.col(cond)), df.filter(~pl.col(cond))
    return {"yes": [yes.height, int(yes["win"].sum())], "no": [no.height, int(no["win"].sum())]}


def run(season: int = CURRENT_SEASON) -> dict:
    w = adjust.weights()
    league = pl.concat([games(s, w) for s in FULL_SEASONS])
    now, last = games(season, w), games(season - 1, w)
    modes = {"blend": pl.concat([last, now]), "season": now}
    teams: dict[int, dict] = {}
    for mode, df in modes.items():
        for (tid,), g in df.group_by("team_id"):
            teams.setdefault(tid, {})[mode] = {"games": g.height, "wins": int(g["win"].sum()), "splits": {c: _split(g, c) for c in CONDITIONS}}
    return {"league": {c: _split(league, c) for c in CONDITIONS}, "league_games": league.height, "teams": teams}
