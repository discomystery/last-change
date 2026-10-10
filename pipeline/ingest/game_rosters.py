"""Each club's game-day roster for every game: who dressed and who was listed as a scratch.

Dressed players come from the game's saved player list. Scratches come from the game center's right rail (gameInfo.{side}Team.
scratches), which the league posts before puck drop and keeps for finished games, so past games can be filled in too.
The league never says why a player was scratched (healthy, minor injury, illness); the NHL.com Status Report
(ingest/status_report.py) supplies reasons where it has them.

A player on neither list was off the game roster entirely: for someone the club still lists, almost always injured
reserve (see metrics/availability.py).

One request per game. Finished games are fetched once; games still to be played today are fetched again on every run
until they are final, because scratches can change up to warm-ups. Offline runs skip fetching.

Table: data/tables/{season}/game_rosters.parquet with game_id, date, team, player_id, first, last, pos, status
("dressed" or "scratched"), final (True once the game is over and the row will not change).
"""
from datetime import date as Date, timedelta

import httpx
import polars as pl

from pipeline.config import FINAL_STATES, NHL_WEB, OFFLINE, PLAYOFFS, REGULAR, TABLES
from pipeline.ingest import nhl
from pipeline.ingest.client import get
from pipeline.ingest.rosters import today

SCHEMA = {"game_id": pl.Int64, "date": pl.Utf8, "team": pl.Utf8, "player_id": pl.Int64, "first": pl.Utf8, "last": pl.Utf8,
          "pos": pl.Utf8, "status": pl.Utf8, "final": pl.Boolean}


def path(season: int):
    return TABLES / str(season) / "game_rosters.parquet"


def _name(n) -> str | None:
    return (n or {}).get("default") if isinstance(n, dict) else n


def _dressed(season: int, game: dict) -> list[dict]:
    """Everyone who played, from the game's player list (players.parquet, built from the play-by-play roster)."""
    t = TABLES / str(season) / "players.parquet"
    if not t.exists():
        return []
    side = {game["home_id"]: game["home"], game["away_id"]: game["away"]}
    ps = pl.read_parquet(t).filter(pl.col("game_id") == game["game_id"])
    return [{"team": side.get(p["team_id"]), "player_id": p["player_id"], "first": p["first"], "last": p["last"], "pos": p["pos"],
             "status": "dressed"} for p in ps.iter_rows(named=True) if p["team_id"] in side]


def _scratches(game: dict) -> list[dict]:
    info = get(f"{NHL_WEB}/v1/gamecenter/{game['game_id']}/right-rail").json().get("gameInfo") or {}
    out = []
    for side in ("home", "away"):
        for p in (info.get(f"{side}Team") or {}).get("scratches") or []:
            out.append({"team": game[side], "player_id": p["id"], "first": _name(p.get("firstName")),
                        "last": _name(p.get("lastName")), "pos": None, "status": "scratched"})
    return out


def rows_for(season: int, game: dict, scratches: list[dict]) -> list[dict]:
    dressed = _dressed(season, game) if game["state"] in FINAL_STATES else []
    final = bool(dressed)  # a finished game whose player list is not built yet is fetched again next run
    rows = dressed + scratches
    seen, out = set(), []
    for r in rows:  # a player in the boxscore dressed, whatever the scratch list said earlier
        key = (r["team"], r["player_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"game_id": game["game_id"], "date": game["date"], **r, "final": final})
    return out


def update(season: int, day: str | None = None) -> dict:
    """Record finished games not yet saved as final, and refresh today's and tomorrow's games that have not finished."""
    if OFFLINE:
        return {"skipped": "offline"}
    day = day or today()
    soon = (Date.fromisoformat(day) + timedelta(days=1)).isoformat()
    old = pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
    done = set(old.filter(pl.col("final"))["game_id"].to_list())
    # The saved schedule, not games.parquet: it also holds games not played yet.
    games = (pl.DataFrame(nhl.season_games(season))
             .filter(pl.col("game_type").is_in([REGULAR, PLAYOFFS]) & ~pl.col("game_id").is_in(list(done)))
             .filter(pl.col("state").is_in(list(FINAL_STATES)) | ((pl.col("date") >= day) & (pl.col("date") <= soon))))
    rows, fetched, missing = [], [], 0
    for g in games.sort("date", "game_id").iter_rows(named=True):
        try:
            rows += rows_for(season, g, _scratches(g))
            fetched.append(g["game_id"])
        except (httpx.HTTPError, RuntimeError):
            missing += 1  # tried again next run
    if fetched:
        keep = old.filter(~pl.col("game_id").is_in(fetched))
        pl.concat([keep, pl.DataFrame(rows, schema=SCHEMA)]).sort("date", "game_id", "team", "player_id").write_parquet(path(season))
    return {"games": len(fetched), "missing": missing}


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
