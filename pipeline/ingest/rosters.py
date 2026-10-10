"""A dated record of every club's roster: one row per player per club per day, kept for the whole season.

The league's player listings only say where a player is now, so without this the site cannot tell where someone was
on a past date (rebuilt previews, players who changed teams mid-season, call-ups). One request per club per day
(32 requests, about 16 seconds); a day already recorded is not fetched again, and offline runs skip it.

Table: data/tables/{season}/rosters.parquet with date (Eastern), team, player_id, first, last, pos (C, L, R, D, G),
shoots, number, birth_date, headshot (the URL of the NHL's portrait photo; null in rows recorded before it was added).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from pipeline.config import NHL_WEB, OFFLINE, PLAYOFFS, REGULAR, TABLES, season_id
from pipeline.ingest.client import get

SCHEMA = {"date": pl.Utf8, "team": pl.Utf8, "player_id": pl.Int64, "first": pl.Utf8, "last": pl.Utf8, "pos": pl.Utf8,
          "shoots": pl.Utf8, "number": pl.Int64, "birth_date": pl.Utf8, "headshot": pl.Utf8}


def path(season: int):
    return TABLES / str(season) / "rosters.parquet"


def today() -> str:
    """The hockey day: Eastern time, so a late West Coast game still belongs to the day it started."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _rows(team: str, season: int, day: str) -> list[dict]:
    data = get(f"{NHL_WEB}/v1/roster/{team}/{season_id(season)}").json()
    out = []
    for group in ("forwards", "defensemen", "goalies"):
        for p in data.get(group, []):
            out.append({"date": day, "team": team, "player_id": p["id"], "first": (p.get("firstName") or {}).get("default"),
                        "last": (p.get("lastName") or {}).get("default"), "pos": p.get("positionCode"), "shoots": p.get("shootsCatches"),
                        "number": p.get("sweaterNumber"), "birth_date": p.get("birthDate"), "headshot": p.get("headshot")})
    return out


def snapshot(season: int, day: str | None = None) -> dict:
    """Record today's rosters for every club playing this season, once per day."""
    day = day or today()
    if OFFLINE:
        return {"skipped": "offline"}
    old = _read(season) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
    if day in set(old["date"].to_list()):
        return {"skipped": f"{day} already recorded"}
    games = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type").is_in([REGULAR, PLAYOFFS]))
    teams = sorted(set(games["home"].to_list()) | set(games["away"].to_list()))
    rows, missing = [], []
    for t in teams:
        try:
            rows += _rows(t, season, day)
        except (httpx.HTTPError, RuntimeError):
            missing.append(t)  # one club's listing failing should not stop the nightly run
    if not rows:
        return {"date": day, "players": 0, "missing": missing}
    new = pl.DataFrame(rows, schema=SCHEMA)
    pl.concat([old, new]).sort("date", "team", "player_id").write_parquet(path(season))
    return {"date": day, "players": new.height, "teams": len(teams) - len(missing), "missing": missing}


def _read(season: int) -> pl.DataFrame:
    """The saved table, with any column added since it was written filled with nulls."""
    t = pl.read_parquet(path(season))
    return t.with_columns(*[pl.lit(None, dtype=d).alias(c) for c, d in SCHEMA.items() if c not in t.columns]).select(list(SCHEMA))


def headshots(season: int) -> dict[int, str]:
    """Each player's portrait URL from the latest day a club listed him with one."""
    if not path(season).exists():
        return {}
    t = _read(season).filter(pl.col("headshot").is_not_null()).sort("date").unique("player_id", keep="last")
    return dict(zip(t["player_id"].to_list(), t["headshot"].to_list()))


def team_on(season: int, player_id: int, day: str) -> str | None:
    """The club listing the player on the latest recorded day up to `day`; None if no club listed him that day
    (sent down, released) or nothing was recorded by then."""
    if not path(season).exists():
        return None
    t = pl.read_parquet(path(season)).filter(pl.col("date") <= day)
    if t.is_empty():
        return None
    r = t.filter((pl.col("date") == t["date"].max()) & (pl.col("player_id") == player_id))
    return r["team"][0] if r.height else None
