"""Score-and-venue adjustment (McCurdy method).

Teams that are trailing shoot more; home teams shoot more. Each 5v5 event is weighted so that, within
every score state, the league's home and away totals come out equal.
"""
import polars as pl

from pipeline.config import FULL_SEASONS, TABLES

METRICS = ["cf", "ff", "sf", "gf", "xgf"]
AGAINST = {"cf": "ca", "ff": "fa", "sf": "sa", "gf": "ga", "xgf": "xga"}


def _five_on_five(seasons: list[int]) -> pl.DataFrame:
    frames = [pl.read_parquet(TABLES / str(s) / "team_game.parquet").filter(pl.col("strength") == "5v5") for s in seasons]
    # Score state from the HOME team's point of view, whichever team the row belongs to.
    return pl.concat(frames).with_columns(home_state=pl.when(pl.col("is_home")).then(pl.col("score_state")).otherwise(-pl.col("score_state")))


def weights(seasons: list[int] = FULL_SEASONS) -> pl.DataFrame:
    """One row per (metric, home score state) with the weight for home events and for away events."""
    t = _five_on_five(seasons)
    rows = []
    for m in METRICS:
        by = t.group_by("home_state", "is_home").agg(pl.col(m).sum().alias("n"))
        for state in range(-3, 4):
            h = by.filter((pl.col("home_state") == state) & pl.col("is_home"))["n"].sum()
            a = by.filter((pl.col("home_state") == state) & ~pl.col("is_home"))["n"].sum()
            rows.append({"metric": m, "home_state": state, "w_home": (h + a) / (2 * h), "w_away": (h + a) / (2 * a), "home_share": h / (h + a)})
    return pl.DataFrame(rows)


def apply(team_game: pl.DataFrame, w: pl.DataFrame) -> pl.DataFrame:
    """Add *_adj columns to 5v5 rows. A team's own events take its venue's weight; events against take the other's."""
    out = team_game.with_columns(home_state=pl.when(pl.col("is_home")).then(pl.col("score_state")).otherwise(-pl.col("score_state")))
    for m in METRICS:
        wm = w.filter(pl.col("metric") == m).select("home_state", pl.col("w_home").alias("_wh"), pl.col("w_away").alias("_wa"))
        out = out.join(wm, on="home_state", how="left").with_columns(
            (pl.col(m) * pl.when(pl.col("is_home")).then(pl.col("_wh")).otherwise(pl.col("_wa"))).alias(f"{m}_adj"),
            (pl.col(AGAINST[m]) * pl.when(pl.col("is_home")).then(pl.col("_wa")).otherwise(pl.col("_wh"))).alias(f"{AGAINST[m]}_adj"),
        ).drop("_wh", "_wa")
    return out
