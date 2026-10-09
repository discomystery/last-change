"""Attach MoneyPuck expected goals to NHL shot events."""
import polars as pl

from pipeline.config import TABLES
from pipeline.ingest import moneypuck

EVENT_MAP = {"SHOT": "shot-on-goal", "MISS": "missed-shot", "GOAL": "goal"}
KEEP = ["xGoal", "xRebound", "xFroze", "xPlayContinuedInZone", "shotRush", "shotRebound", "shotGeneratedRebound", "shotGoalieFroze",
        "shotOnEmptyNet", "shotWasOnGoal", "timeSinceLastEvent", "lastEventCategory", "speedFromLastEvent",
        "arenaAdjustedShotDistance", "shotAngleAdjusted", "xCordAdjusted", "yCordAdjusted"]
TOLERANCE_SECONDS = 2


def run(season: int) -> dict:
    out_dir = TABLES / str(season)
    events = pl.read_parquet(out_dir / "events.parquet").filter(pl.col("type").is_in(list(EVENT_MAP.values())))
    mp = moneypuck.read_shots(season).with_row_index("mp_row").with_columns(
        game_id=season * 1_000_000 + pl.col("game_id"),
        type=pl.col("event").replace_strict(EVENT_MAP),
        p1=pl.col("shooterPlayerId").cast(pl.Int64),
    ).filter(pl.col("game_id").is_in(events["game_id"].unique().to_list()))
    # Pass 1 is strict. Later passes pick up shots the league re-scored after MoneyPuck pulled them
    # (a shot changed to a miss, or credited to a different shooter).
    passes = [(["game_id", "period", "type", "p1"], 2), (["game_id", "period", "p1"], 2), (["game_id", "period", "type"], 1)]
    left_mp = mp.select("mp_row", "game_id", "period", "type", "p1", "time")
    left_ev = events.select("game_id", "event_id", "period", "type", "p1", "sec")
    found, by_pass = [], []
    for keys, tol in passes:
        hit = (
            left_mp.join(left_ev, on=keys, suffix="_ev")
            .with_columns(gap=(pl.col("time") - pl.col("sec")).abs())
            .filter(pl.col("gap") <= tol)
            .sort("gap")
            .unique("mp_row", keep="first", maintain_order=True)
            .unique(["game_id", "event_id"], keep="first", maintain_order=True)
            .select("mp_row", "game_id", "event_id")
        )
        found.append(hit)
        by_pass.append(hit.height)
        left_mp = left_mp.join(hit, on="mp_row", how="anti")
        left_ev = left_ev.join(hit, on=["game_id", "event_id"], how="anti")
    pairs = pl.concat(found)
    joined = pairs.select("mp_row", "game_id", "event_id").join(mp.select(["mp_row", *KEEP]), on="mp_row").drop("mp_row")
    joined.write_parquet(out_dir / "shots_xg.parquet")
    per_game = (
        mp.group_by("game_id").agg(pl.len().alias("mp_shots"))
        .join(pairs.group_by("game_id").agg(pl.len().alias("matched")), on="game_id", how="left")
        .with_columns(rate=pl.col("matched").fill_null(0) / pl.col("mp_shots"))
    )
    nhl_games = events["game_id"].n_unique()
    return {
        "season": season,
        "moneypuck_shots": mp.height,
        "matched": pairs.height,
        "matched_by_pass": by_pass,
        "match_rate_pct": round(100 * pairs.height / max(mp.height, 1), 2),
        "games_below_99pct": per_game.filter(pl.col("rate") < 0.99).height,
        "worst_games": per_game.sort("rate").head(3).select("game_id", "mp_shots", "matched").to_dicts(),
        "games_xg_pending": nhl_games - per_game.height,
        "nhl_unblocked_without_xg": events.height - pairs.height,
    }
