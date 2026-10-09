"""NHL EDGE puck-and-player tracking summaries, one file per player per season.

One request per player gives his top skating and shot speeds, fast bursts, distance skated, zone time and
shots by rink area (skaters), or save percentage by rink area (goalies). A finished season is fetched once.
The current season is refetched only for players who have played since the last fetch, so a nightly run
costs a few minutes.
"""
import gzip
import json
from pathlib import Path

import httpx
import polars as pl

from pipeline.config import CURRENT_SEASON, NHL_WEB, RAW, REGULAR, TABLES, season_id
from pipeline.ingest.client import get


def path(season: int, player_id: int) -> Path:
    return RAW / "edge" / str(season) / f"{player_id}.json.gz"


def read(season: int, player_id: int) -> dict | None:
    p = path(season, player_id)
    if not p.exists():
        return None
    with gzip.open(p, "rt") as f:
        return json.load(f)


def _games_played(season: int) -> dict[int, tuple[int, bool]]:
    """Regular-season games each player has dressed for in our tables, and whether he is a goalie."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)["game_id"]
    toi = pl.read_parquet(d / "toi_check.parquet").filter((pl.col("shift_toi") > 0) & pl.col("game_id").is_in(games.to_list()))
    return {r["player_id"]: (r["n"], r["goalie"]) for r in toi.group_by("player_id").agg(pl.len().alias("n"), pl.col("goalie").first()).iter_rows(named=True)}


def fetch(season: int, players: dict[int, tuple[int, bool]]) -> dict:
    """Download what is missing or stale for the given players: {player_id: (games we have, is goalie)}."""
    made, failed = 0, []
    for pid, (gp, goalie) in sorted(players.items()):
        have = read(season, pid)
        if have is not None and (season < CURRENT_SEASON or have.get("_games", 0) >= gp):
            continue
        kind = "goalie-detail" if goalie else "skater-detail"
        try:
            body = get(f"{NHL_WEB}/v1/edge/{kind}/{pid}/{season_id(season)}/{REGULAR}").json()
        except (httpx.HTTPStatusError, RuntimeError):
            failed.append(pid)
            continue
        body["_games"] = gp  # how many games we had seen when this was fetched
        p = path(season, pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        with gzip.open(tmp, "wt") as f:
            json.dump(body, f, separators=(",", ":"))
        tmp.replace(p)
        made += 1
    return {"season": season, "players": len(players), "requests": made, "failed": len(failed)}


def update(season: int = CURRENT_SEASON) -> list[dict]:
    """This season for everyone who has played, and last season for the same players (fetched once)."""
    now = _games_played(season)
    before = _games_played(season - 1)
    return [fetch(season, now), fetch(season - 1, {p: before[p] for p in now if p in before})]


if __name__ == "__main__":
    print(json.dumps(update()))
