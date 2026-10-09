"""Automated data checks. Targets come from the project brief, Section 8."""
import polars as pl

from pipeline.config import TABLES


def run(season: int) -> dict:
    d = TABLES / str(season)
    toi = pl.read_parquet(d / "toi_check.parquet").with_columns(diff=(pl.col("shift_toi") - pl.col("box_toi")).abs())
    skaters = toi.filter(~pl.col("goalie"))
    bad = skaters.filter(pl.col("diff") > 5)
    stints = pl.read_parquet(d / "stints.parquet")
    events = pl.read_parquet(d / "events.parquet")
    shots = events.filter(pl.col("type").is_in(["shot-on-goal", "missed-shot", "goal"]) & pl.col("x_norm").is_not_null())
    med = shots.group_by("game_id", "period", "is_home").agg(pl.col("x_norm").median().alias("m"), pl.len().alias("n")).filter(pl.col("n") >= 3)
    out = {
        "season": season,
        "games": events["game_id"].n_unique(),
        "toi_within_5s_pct": round(100 * (1 - bad.height / max(skaters.height, 1)), 2),
        "toi_target_pct": 99.0,
        "toi_failures": bad.height,
        "toi_failure_games": bad["game_id"].n_unique(),
        "goalie_toi_within_5s_pct": round(100 * toi.filter(pl.col("goalie") & (pl.col("diff") <= 5)).height / max(toi.filter(pl.col("goalie")).height, 1), 2),
        "flagged_stint_seconds_pct": round(100 * stints.filter(pl.col("flag"))["duration"].sum() / stints["duration"].sum(), 3),
        "team_periods_attacking_positive_pct": round(100 * med.filter(pl.col("m") > 0).height / max(med.height, 1), 2),
        "events_missing_coordinates_pct": round(100 * events.filter(pl.col("x").is_null() & pl.col("type").is_in(["shot-on-goal", "missed-shot", "goal", "blocked-shot", "hit", "giveaway", "takeaway", "faceoff"])).height / events.height, 3),
    }
    out["worst_toi_games"] = bad.group_by("game_id").agg(pl.len().alias("players"), pl.col("diff").max().alias("max_diff")).sort("players", descending=True).head(5).to_dicts()
    return out
