"""Firsts, debuts and returns for the post-game story: the lines wire recaps lead with and ours used to miss (eye test,
2026-10-13: 11 first NHL goals and 22 debuts in the first 89 games, none told).

Career totals before a game come from the NHL player landing saved for every player (`ingest/careers.py`, NHL regular
seasons before this one, whether or not the game-by-game career is complete) plus this season's games before it from
our own tables. A player with no landing on file gets no first, rather than a wrong one.

Kinds, most important first (`WEIGHT`):
  first_goal      his first NHL goal ("in his NHL debut" when it was also his first game)
  milestone       a round hundred reached tonight: his 500th, 600th... game, 100th+ goal, 200th+ assist, 300th+ point
                  (the same marks previews count down to, so a preview's "one point from 900" is closed after the game)
  debut           his first NHL game (goalies: only when he started)
  return          a regular (14:00 a game for forwards, 18:00 for defensemen, over his last ten) back after missing 10+
                  of his team's games ("his first NHL game since ..." after a long time away); never why he was out
  first_point     his first NHL point, when it wasn't a goal
  club_goal       his first goal for a new club, for a player with NHL goals elsewhere
"""
import gzip
import json
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, SITE_DATA, TABLES

WEIGHT = {"first_goal": 90, "milestone": 85, "debut": 80, "return": 70, "first_point": 65, "club_goal": 60}
RETURN_GAMES = 10  # team games missed before a return is told
LONG_AWAY_DAYS = 300  # past this, "his first NHL game since <date>" instead of a count
GOALIE_RETURN_GAMES = 30  # a goalie back in net: told by the date of his last game, after this many team games
REGULAR_F, REGULAR_D = 14 * 60, 18 * 60  # a return is told for a regular: this much ice time a game in his last ten


def _season_id(season: int) -> int:
    return int(f"{season}{season + 1}")


@lru_cache(maxsize=1)
def prior_totals(season: int = CURRENT_SEASON) -> dict[int, dict]:
    """Per player: NHL regular-season games, goals, assists before `season`, and the clubs (full names) he played for."""
    out = {}
    d = RAW / "careers"
    if not d.exists():
        return out
    cut = _season_id(season)
    for f in d.glob("*.json.gz"):
        land = (json.loads(gzip.open(f).read()).get("landing") or {})
        if not land.get("seasonTotals") and "seasonTotals" not in land:
            continue
        rows = [s for s in land["seasonTotals"] if s.get("gameTypeId") == REGULAR and s.get("leagueAbbrev") == "NHL" and s["season"] < cut]
        clubs = {(s.get("teamName") or {}).get("default") if isinstance(s.get("teamName"), dict) else s.get("teamName") for s in rows}
        out[int(f.name.split(".")[0])] = {"gp": sum(s.get("gamesPlayed") or 0 for s in rows), "g": sum(s.get("goals") or 0 for s in rows),
                                          "a": sum(s.get("assists") or 0 for s in rows), "clubs": {c for c in clubs if c},
                                          "pos": land.get("position")}
    return out


@lru_cache(maxsize=1)
def _club_names() -> dict[str, str]:
    p = SITE_DATA / "teams.json"
    return {t["abbr"]: t["name"] for t in json.loads(p.read_text())} if p.exists() else {}


@lru_cache(maxsize=4)
def _goalie_starts(season: int) -> dict[int, list[tuple[int, int]]]:
    """game_id -> [(goalie, team_id)] for every goalie who started."""
    from pipeline.metrics import goalies
    out = {}
    for r in goalies.per_game(season).filter(pl.col("started")).iter_rows(named=True):
        out.setdefault(r["game_id"], []).append((r["goalie_id"], r["team_id"]))
    return out


@lru_cache(maxsize=1)
def _goalie_games() -> dict[int, list[str]]:
    """Every goalie's game dates since 2023-24 (any time in net), oldest first."""
    from pipeline.config import FULL_SEASONS
    from pipeline.metrics import goalies
    out = {}
    for s in [*FULL_SEASONS, CURRENT_SEASON]:
        if not (TABLES / str(s) / "events.parquet").exists():
            continue
        for gid_, d in goalies.per_game(s).filter(pl.col("sec") > 0).select("goalie_id", "date").iter_rows():
            out.setdefault(gid_, []).append(d)
    return {k: sorted(v) for k, v in out.items()}


def _goalie_last(pid: int, day: str) -> str | None:
    past = [d for d in _goalie_games().get(pid, []) if d < day]
    return past[-1] if past else None


def for_game(gid: int, season: int, G: pl.DataFrame, P: pl.DataFrame) -> list[dict]:
    """Every first, debut and return in one game: {kind, pid, team, weight, ...}, most important first. `G` and `P` are
    stories.book()'s games and skater lines (all seasons since 2023-24)."""
    prior = prior_totals(season)
    if not prior:
        return []
    game = G.filter(pl.col("game_id") == gid)
    if game.is_empty():
        return []
    day = game["date"][0]
    tonight = P.filter(pl.col("game_id") == gid)
    before = P.filter((pl.col("season") == season) & (pl.col("date") < day))
    club = _club_names()
    out = []
    for r in tonight.iter_rows(named=True):
        pid, t = r["player_id"], r["team"]
        base = prior.get(pid)
        if base is None:
            continue
        mine = before.filter(pl.col("player_id") == pid)
        gp, g, pts = base["gp"] + mine.height, base["g"] + int(mine["g"].sum()), base["g"] + base["a"] + int(mine["pts"].sum())
        g_now, pts_now = int(r["g"]), int(r["pts"])
        item = {"pid": pid, "team": t, "goals": g_now}
        a_now = pts_now - g_now
        # a round hundred crossed tonight; one per player, goals first, games last
        for what, have, now, low in (("goal", g, g_now, 100), ("point", pts, pts_now, 300), ("assist", pts - g, a_now, 200), ("game", gp, 1, 500)):
            mark = (have + now) // 100 * 100
            if now and mark >= low and have < mark:
                out.append({**item, "kind": "milestone", "what": what, "mark": mark})
                break
        if g == 0 and g_now:
            out.append({**item, "kind": "first_goal", "debut": gp == 0})
        elif gp == 0:
            out.append({**item, "kind": "debut", "points": pts_now})
        elif pts == 0 and pts_now:
            out.append({**item, "kind": "first_point"})
        elif g_now and base["g"] + int(mine["g"].sum()) > 0:
            name = club.get(t)
            for_club = mine.filter(pl.col("team") == t)
            past_here = P.filter((pl.col("player_id") == pid) & (pl.col("team") == t) & (pl.col("date") < day))
            if name and name not in base["clubs"] and not past_here["g"].sum() and not for_club["g"].sum():
                out.append({**item, "kind": "club_goal"})
        if gp:  # a return: his last game, and how many of his team's games went by since
            hist = P.filter((pl.col("player_id") == pid) & (pl.col("date") < day)).tail(10)
            prev = hist.tail(1)
            regular = hist.height and hist["sec"].mean() >= (REGULAR_D if r["pos"] == "D" else REGULAR_F)
            if prev.height and regular:
                last_day = prev["date"][0]
                missed = G.filter((pl.col("date") > last_day) & (pl.col("date") < day) & ((pl.col("home") == t) | (pl.col("away") == t))).height
                if missed >= RETURN_GAMES:
                    from datetime import date as Date
                    days = (Date.fromisoformat(day) - Date.fromisoformat(last_day)).days
                    out.append({**item, "kind": "return", "missed": missed, "since": last_day if days >= LONG_AWAY_DAYS else None})
    starts = _goalie_starts(season).get(gid, [])
    team_of = {game["home_id"][0]: game["home"][0], game["away_id"][0]: game["away"][0]}
    for pid, tid in starts:
        base = prior.get(pid)
        if base is None:
            continue
        t = team_of.get(tid)
        if base["gp"] or any(x[0] == pid for k, v in _goalie_starts(season).items() if k < gid for x in v):
            prev = _goalie_last(pid, day)  # a goalie back in net after a long time away
            if prev:
                missed = G.filter((pl.col("date") > prev) & (pl.col("date") < day) & ((pl.col("home") == t) | (pl.col("away") == t))).height
                if missed >= GOALIE_RETURN_GAMES:  # backups sit for weeks, so a goalie's return needs a long gap
                    out.append({"pid": pid, "team": t, "kind": "return", "goalie": True, "missed": missed, "since": prev})
            continue
        earlier = [x for k, v in _goalie_starts(season).items() if k < gid for x in v if x[0] == pid]
        if not earlier:
            out.append({"pid": pid, "team": team_of.get(tid), "kind": "debut", "goalie": True})
    for x in out:
        x["weight"] = WEIGHT[x["kind"]]
    return sorted(out, key=lambda x: -x["weight"])
