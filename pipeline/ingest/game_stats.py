"""The league's own power-play line for every finished game ("3/4": goals / opportunities), from the game center's
right rail (teamGameStats, category "powerPlay").

The post-game page used to count power plays from our shorthanded stretches and goals from 5-on-4, 5-on-3 and 4-on-3
play. That missed what the league counts: Winnipeg's 3-for-4 at Detroit read "3 chances", and a 6-on-4 goal with the
goalie pulled on a power play was not a power-play goal (eye test, 2026-10-13). One request per finished game, never
repeated; offline runs skip fetching, and readers fall back to our own counts for any game not saved.

Table: data/tables/{season}/game_stats.parquet with game_id, team, pp_goals, pp_opps.
"""
import httpx
import polars as pl

from pipeline.config import FINAL_STATES, NHL_WEB, OFFLINE, PLAYOFFS, REGULAR, TABLES
from pipeline.ingest.client import get

SCHEMA = {"game_id": pl.Int64, "team": pl.Utf8, "pp_goals": pl.Int64, "pp_opps": pl.Int64}


def path(season: int):
    return TABLES / str(season) / "game_stats.parquet"


def parse(game: dict, rail: dict) -> list[dict]:
    """Rows from one right-rail response; empty when the league has not posted the line."""
    out = []
    for stat in rail.get("teamGameStats") or []:
        if stat.get("category") != "powerPlay":
            continue
        for side in ("away", "home"):
            v = str(stat.get(f"{side}Value") or "")
            if "/" in v:
                g, n = v.split("/", 1)
                if g.strip().isdigit() and n.strip().isdigit():
                    out.append({"game_id": game["game_id"], "team": game[side], "pp_goals": int(g), "pp_opps": int(n)})
    return out if len(out) == 2 else []


def update(season: int) -> dict:
    """Fetch the power-play line for finished games not yet recorded."""
    if OFFLINE:
        return {"skipped": "offline"}
    old = load(season)
    seen = set(old["game_id"].to_list())
    games = (pl.read_parquet(TABLES / str(season) / "games.parquet")
             .filter(pl.col("game_type").is_in([REGULAR, PLAYOFFS]) & pl.col("state").is_in(list(FINAL_STATES)))
             .filter(~pl.col("game_id").is_in(list(seen))))
    rows, missing = [], 0
    for g in games.iter_rows(named=True):
        try:
            got = parse(g, get(f"{NHL_WEB}/v1/gamecenter/{g['game_id']}/right-rail").json())
        except (httpx.HTTPError, RuntimeError, ValueError):
            got = []
        if got:
            rows += got
        else:
            missing += 1  # tried again next run
    if rows:
        pl.concat([old, pl.DataFrame(rows, schema=SCHEMA)]).sort("game_id", "team").write_parquet(path(season))
    return {"games": games.height - missing, "missing": missing}


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)


def lookup(season: int) -> dict[tuple[int, str], tuple[int, int]]:
    """(game_id, team) -> (power-play goals, opportunities), as the league counts them."""
    return {(r["game_id"], r["team"]): (r["pp_goals"], r["pp_opps"]) for r in load(season).iter_rows(named=True)}
