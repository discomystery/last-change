"""NHL career history per player, for the plain-English intros and the career ledger on player pages.

From the NHL: the player landing page (season totals by club, draft club) and the regular-season game log of every
NHL season he played before 2023-24. Finished seasons never change, so each player costs one landing request plus one per
past season, once. 2023-24 onward comes from our own tables (`games_this_season`), so nothing is refetched nightly.

Saved per player at raw/careers/{id}.json.gz as {"fetched", "complete", "landing": {...trimmed}, "logs": {"20242025": [games]}}.
A first run would need several thousand requests, so each run fetches at most `BUDGET` and starts with the players who
play the most; the rest follow on later runs. Intros use a player's history only once it is complete.
"""
from __future__ import annotations

import gzip
import json
from datetime import date

import httpx
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, NHL_WEB, OFFLINE, RAW, REGULAR, TABLES
from pipeline.ingest.client import get

DIR = RAW / "careers"
BUDGET = 1200  # requests per run (about ten minutes at two a second)
OWN_FROM = FULL_SEASONS[0]  # seasons from here on come from our own tables, not the NHL game log
GAME_KEYS = ("gameId", "gameDate", "teamAbbrev", "opponentAbbrev", "homeRoadFlag", "goals", "points",
             "gamesStarted", "decision", "shotsAgainst", "goalsAgainst")  # the last four are goalies' only
VERSION = 2  # 2: home or road and goalie decisions, for records in a building; older saves are fetched again


def _path(pid: int):
    return DIR / f"{pid}.json.gz"


def read(pid: int) -> dict | None:
    p = _path(pid)
    return json.loads(gzip.open(p).read()) if p.exists() else None


def _write(pid: int, body: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(pid).with_suffix(".part")
    with gzip.open(tmp, "wt") as f:
        json.dump(body, f, separators=(",", ":"))
    tmp.replace(_path(pid))


# Every NHL season line the landing page carries, for the career ledger on player pages (2026-10-15).
SEASON_KEYS = ("season", "gameTypeId", "sequence", "leagueAbbrev", "teamName", "gamesPlayed", "goals", "assists", "points",
               "plusMinus", "pim", "powerPlayGoals", "powerPlayPoints", "shorthandedGoals", "shorthandedPoints",
               "gameWinningGoals", "otGoals", "shots", "avgToi", "faceoffWinningPctg",
               "gamesStarted", "wins", "losses", "otLosses", "shotsAgainst", "goalsAgainst", "shutouts", "timeOnIce")


# His basics for the player page's bio line (2026-10-23): height, weight, where he was born and which hand he shoots with.
BIO_KEYS = ("heightInInches", "heightInCentimeters", "weightInPounds", "weightInKilograms", "birthCity", "birthStateProvince",
            "birthCountry", "shootsCatches")


def _trim_landing(land: dict) -> dict:
    keep = ("playerId", "firstName", "lastName", "position", "headshot", "birthDate", "draftDetails", *BIO_KEYS)
    out = {k: land.get(k) for k in keep}
    out["seasonTotals"] = [{k: s[k] for k in SEASON_KEYS if s.get(k) is not None}
                           for s in land.get("seasonTotals", []) if s.get("leagueAbbrev") == "NHL"]
    out["awards"] = [{"trophy": (a.get("trophy") or {}).get("default"), "seasons": sorted({x["seasonId"] for x in a.get("seasons", []) if x.get("seasonId")})}
                     for a in land.get("awards", [])]
    out["full"] = True
    out["bio"] = True
    return out


def stale(have: dict) -> bool:
    """Whether the saved landing needs fetching again: saved before it kept every stat or his bio, or before the last
    finished season's playoffs were over (July 1 of the year it ended), so its season lines are not final."""
    land = have.get("landing")
    if not land:
        return True
    return not land.get("full") or not land.get("bio") or have.get("fetched", "") < f"{CURRENT_SEASON}-07-01"


def _past_seasons(landing: dict) -> list[int]:
    """NHL regular seasons he played before the ones our own tables cover, as 20212022-style ids."""
    cur = int(f"{OWN_FROM}{OWN_FROM + 1}")
    return sorted({s["season"] for s in landing["seasonTotals"] if s.get("gameTypeId") == REGULAR and s["season"] < cur})


def _priority(season: int) -> list[int]:
    """Everyone who has dressed this season, the busiest first, then players a club holds who have not dressed yet."""
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet")
    goalies = pl.read_parquet(TABLES / str(season) / "players.parquet").filter(pl.col("pos") == "G").select("player_id").unique()
    sec = pg.group_by("player_id").agg(pl.col("sec").sum()).sort("sec", descending=True)["player_id"].to_list()
    g_ids = set(goalies["player_id"].to_list())
    # goalies first: their records in each building feed the preview storylines
    from pipeline.metrics import availability

    first = [p for p in sec if p in g_ids] + sorted(g for g in g_ids if g not in set(sec)) + [p for p in sec if p not in g_ids]
    return first + sorted(availability.held(season) - set(first))


def update(season: int = CURRENT_SEASON, budget: int = BUDGET) -> dict:
    if OFFLINE:
        return {"skipped": "offline"}
    used = done = 0
    failed = []
    for pid in _priority(season):
        have = read(pid) or {}
        if have.get("version", 1) < VERSION:
            have = {k: v for k, v in have.items() if k != "logs"} | {"logs": {}, "complete": False, "version": VERSION}
        if have.get("complete") and not stale(have):
            continue
        if used >= budget:
            break
        try:
            if stale(have):
                # game logs of finished seasons never change, so a fresh landing keeps the ones already saved
                have = {"fetched": date.today().isoformat(), "complete": False, "logs": have.get("logs", {}), "version": VERSION,
                        "landing": _trim_landing(get(f"{NHL_WEB}/v1/player/{pid}/landing").json())}
                used += 1
            for s in _past_seasons(have["landing"]):
                if str(s) in have["logs"]:
                    continue
                if used >= budget:
                    break
                log = get(f"{NHL_WEB}/v1/player/{pid}/game-log/{s}/{REGULAR}").json().get("gameLog", [])
                have["logs"][str(s)] = [{k: g.get(k) for k in GAME_KEYS} for g in log]
                used += 1
            have["complete"] = all(str(s) in have["logs"] for s in _past_seasons(have["landing"]))
            done += have["complete"]
        except (httpx.HTTPStatusError, RuntimeError):
            failed.append(pid)
            continue
        finally:
            if have.get("landing"):
                _write(pid, have)
    total = sum(1 for _ in DIR.glob("*.json.gz")) if DIR.exists() else 0
    complete = sum(1 for f in DIR.glob("*.json.gz") if json.loads(gzip.open(f).read()).get("complete")) if DIR.exists() else 0
    return {"requests": used, "completed_now": done, "failed": len(failed), "players_saved": total, "complete": complete}


def games_this_season(season: int = CURRENT_SEASON) -> dict[int, list[dict]]:
    """This season's regular-season games per player from our own tables, in the same shape as the NHL game log."""
    d = TABLES / str(season)
    g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date", "home_id", "away_id", "home", "away")
    pg = pl.read_parquet(d / "player_game.parquet").select("game_id", "player_id", "team_id", "g", "a1", "a2")
    j = pg.join(g, on="game_id").with_columns(
        teamAbbrev=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("home")).otherwise(pl.col("away")),
        opponentAbbrev=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("away")).otherwise(pl.col("home")),
        points=pl.col("g") + pl.col("a1") + pl.col("a2"))
    out: dict[int, list[dict]] = {}
    for r in j.sort("game_id").iter_rows(named=True):
        out.setdefault(r["player_id"], []).append({"gameId": r["game_id"], "gameDate": r["date"], "teamAbbrev": r["teamAbbrev"],
                                                   "opponentAbbrev": r["opponentAbbrev"], "goals": int(r["g"]), "points": int(r["points"])})
    return out


def load_all() -> dict[int, dict]:
    """Complete careers only, each with this season's games from our tables added under the current season's key."""
    own = {s: games_this_season(s) for s in range(OWN_FROM, CURRENT_SEASON + 1)}
    out = {}
    if DIR.exists():
        for f in DIR.glob("*.json.gz"):
            c = json.loads(gzip.open(f).read())
            if not c.get("complete"):
                continue
            pid = int(f.name.split(".")[0])
            for s, by in own.items():
                c["logs"][f"{s}{s + 1}"] = by.get(pid, [])
            out[pid] = c
    return out


if __name__ == "__main__":
    print(json.dumps(update()))
