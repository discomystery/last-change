"""NHL EDGE puck-and-player tracking summaries, one file per player per season.

One request per player gives his top skating and shot speeds, fast bursts, distance skated, zone time and
shots by rink area (skaters), or save percentage by rink area (goalies); a second request adds skaters' speed
detail (bursts by speed band and ten fastest skates). A finished season is fetched once.
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


# Skaters' files also carry the skating-speed detail (`speed`: bursts at 18-20, 20-22 and 22+ mph and his ten fastest
# skates, for the player page's Top gear chart), one more request each. Files saved before it was kept get it added
# on later runs, at most this many requests a run, players with the most games first.
SPEED_BUDGET = 700


def _speed(season: int, pid: int) -> dict:
    body = get(f"{NHL_WEB}/v1/edge/skater-skating-speed-detail/{pid}/{season_id(season)}/{REGULAR}").json()
    det = body.get("skatingSpeedDetails") or {}
    return {"b18": (det.get("bursts18To20") or {}).get("value"), "b20": (det.get("bursts20To22") or {}).get("value"),
            "b22": (det.get("burstsOver22") or {}).get("value"),
            "top": [round(t["skatingSpeed"]["imperial"], 2) for t in body.get("topSkatingSpeeds") or [] if (t.get("skatingSpeed") or {}).get("imperial")]}


def _save(season: int, pid: int, body: dict) -> None:
    p = path(season, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    with gzip.open(tmp, "wt") as f:
        json.dump(body, f, separators=(",", ":"))
    tmp.replace(p)


def fetch(season: int, players: dict[int, tuple[int, bool]]) -> dict:
    """Download what is missing or stale for the given players: {player_id: (games we have, is goalie)}."""
    made, failed, speed_left = 0, [], SPEED_BUDGET
    for pid, (gp, goalie) in sorted(players.items(), key=lambda kv: (-kv[1][0], kv[0])):
        have = read(season, pid)
        if have is not None and (season < CURRENT_SEASON or have.get("_games", 0) >= gp):
            if goalie or "speed" in have or speed_left <= 0:
                continue
            speed_left -= 1  # an older file without the speed detail: add it
            try:
                have["speed"] = _speed(season, pid)
            except (httpx.HTTPStatusError, RuntimeError):
                failed.append(pid)
                continue
            _save(season, pid, have)
            made += 1
            continue
        kind = "goalie-detail" if goalie else "skater-detail"
        try:
            body = get(f"{NHL_WEB}/v1/edge/{kind}/{pid}/{season_id(season)}/{REGULAR}").json()
            if not goalie:
                body["speed"] = _speed(season, pid)
                made += 1
        except (httpx.HTTPStatusError, RuntimeError):
            failed.append(pid)
            continue
        body["_games"] = gp  # how many games we had seen when this was fetched
        _save(season, pid, body)
        made += 1
    return {"season": season, "players": len(players), "requests": made, "failed": len(failed)}


def update(season: int = CURRENT_SEASON) -> list[dict]:
    """This season for everyone who has played, and last season for the same players plus anyone a club holds who has
    not played yet (injured, in the minors: their pages lean on last season), fetched once."""
    from pipeline.metrics import availability

    now = _games_played(season)
    before = _games_played(season - 1)
    want = set(now) | availability.held(season)
    return [fetch(season, now), fetch(season - 1, {p: before[p] for p in want if p in before})]


if __name__ == "__main__":
    print(json.dumps(update()))
