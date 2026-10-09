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


def home_road(seasons: list[int] = FULL_SEASONS) -> dict[str, dict[str, float]]:
    """League-wide gap between what home teams and visitors are credited with, per event type.

    Home teams are credited with more hits, giveaways and takeaways everywhere. Whether that is scorers
    watching the home side more closely or teams really playing differently at home cannot be told apart,
    and it does not need to be: the weights simply put home and road games on the same footing.
    (A building-by-building version was tested and rejected: it does not repeat from year to year.)
    """
    home = {e: 0.0 for e in EVENTS} | {"sec": 0.0}
    away = dict(home)
    for s in seasons:
        d = TABLES / str(s)
        g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)["game_id"].to_list()
        t = pl.read_parquet(d / "team_game.parquet").filter(pl.col("game_id").is_in(g))
        for side, is_home in ((home, True), (away, False)):
            part = t.filter(pl.col("is_home") == is_home)
            for c in side:
                side[c] += part[c].sum()
    out = {}
    for e in EVENTS:
        h, a = home[e] / home["sec"] * 3600, away[e] / away["sec"] * 3600
        mid = (h + a) / 2
        out[e] = {"home_rate": round(h, 2), "away_rate": round(a, 2), "w_home": round(mid / h, 4), "w_away": round(mid / a, 4)}
    return out
