"""Finished games whose shift charts the NHL is still filling in.

Right after the horn the score, goals, shots and goalie numbers are final, but the shift charts often stop short for a
while, so who was on the ice is unknown for the missing stretch. Until a chart covers the game, its post-game page
shows what is already certain and says the rest is still coming in: the graded calls, Swing, report cards and the
shift-by-shift replay wait (user decision 2026-10-11, "Post early"). Only recent games are checked: a chart still
short after a few days is used as it is, as before.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, FINAL_STATES, TABLES

DAYS = 3  # games older than this are never held back


@lru_cache(maxsize=4)
def games(season: int = CURRENT_SEASON) -> frozenset[int]:
    from pipeline.ingest import nhl

    path = TABLES / str(season) / "games.parquet"
    if not path.exists():
        return frozenset()
    since = (datetime.now(timezone.utc).date() - timedelta(days=DAYS)).isoformat()
    recent = pl.read_parquet(path).filter(pl.col("state").is_in(list(FINAL_STATES)) & (pl.col("date") >= since))
    return frozenset(g for g in recent["game_id"].to_list() if not nhl.shifts_complete(season, g))
