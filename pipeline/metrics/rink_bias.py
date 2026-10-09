"""Arena-scorer factors for hits, giveaways, takeaways and blocks.

Each arena's off-ice scorers count these a little differently. For every home rink, compare how often an
event is recorded in that team's home games with how often it is recorded in the same team's road games
(both teams' events combined, per hour). A factor of 1.20 means that building records 20% more.
"""
import polars as pl

from pipeline.config import FULL_SEASONS, REGULAR, TABLES

EVENTS = ["hits", "gives", "takes", "blocks"]
SHRINK_GAMES = 20  # pull each factor part of the way back toward 1.00


def factors(seasons: list[int] = FULL_SEASONS) -> pl.DataFrame:
    frames, venues = [], []
    for s in seasons:
        d = TABLES / str(s)
        g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "home_id", "away_id", "home", "venue")
        t = pl.read_parquet(d / "team_game.parquet").group_by("game_id").agg(pl.col(EVENTS).sum(), (pl.col("sec").sum() / 2).alias("sec"))
        frames.append(g.join(t, on="game_id"))
    games = pl.concat(frames)
    rows = []
    for (tid, abbr), home in games.group_by("home_id", "home"):
        road = games.filter(pl.col("away_id") == tid)
        venue = home.group_by("venue").len().sort("len", descending=True)["venue"][0]
        row = {"team_id": tid, "team": abbr, "venue": venue, "home_games": home.height, "road_games": road.height}
        for e in EVENTS:
            here = home[e].sum() / home["sec"].sum() * 3600
            away = road[e].sum() / road["sec"].sum() * 3600
            raw = here / away
            row |= {f"{e}_here": round(here, 2), f"{e}_elsewhere": round(away, 2), f"{e}_raw": round(raw, 3),
                    e: round(1 + (raw - 1) * home.height / (home.height + SHRINK_GAMES), 3)}
        rows.append(row)
    return pl.DataFrame(rows).sort("team")
