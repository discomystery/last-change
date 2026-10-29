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
  club_debut      his first game for a club he had never played for, after NHL games elsewhere: told when he moved
                  during the season, or had a point ("made his Wild debut after four seasons with the Canucks"); never
                  "traded" or "signed", since no feed says how he moved
  club_goal       his first goal for a new club, for a player with NHL goals elsewhere
  first_win       a goalie's first NHL win (with how long he waited: starts, seasons since his debut), and his first
                  shutout with it when it was one
  first_shutout   a goalie's first NHL shutout, when it wasn't also his first win
  first_start     a goalie's first NHL start, after relief appearances only
  win_since       a goalie's first win in 300+ days ("his first NHL win since Mar 3, 2025")

Goalie decisions: the NHL gives the win to the goalie in net when his team scored the goal that put it ahead for good.
Saved boxscores carry the decision only once refetched after the game, so it is worked out the same way from the
play-by-play (`_wins`), the box's own "W" taking precedence (eye test, 2026-10-16: DiPietro's first NHL win, eight
seasons after his debut, was the story in Boston and ours never said it).

Skater first goals carry `undrafted` (the landing's draftDetails is empty) and `nth` (his NHL game tonight, counting
this one) for "an undrafted rookie in his eighth NHL game" (Hughes, same night).

A return's "since" date counts playoff games too (eye test on 2025-26: Tkachuk's "first NHL game since Feb 8, 2025"
skipped his whole playoff run); a player back after the summer is told as his first game of the season.
"""
import gzip
import json
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, PLAYOFFS, RAW, REGULAR, SITE_DATA, TABLES

WEIGHT = {"first_goal": 90, "first_win": 88, "first_shutout": 86, "milestone": 85, "debut": 80, "first_start": 75, "win_since": 72,
          "return": 70, "first_point": 65, "club_debut": 62, "club_goal": 60}
WIN_SINCE_DAYS = 300  # a goalie's win after this long without one is told with the date of the last
RETURN_GAMES = 5  # team games a regular missed before a mid-season return is told (10 until 2026-10-15; a regular rarely sits)
SEASON_DEBUT_GAMES = 2  # ... and before a regular's first game of the season is told
DEPTH_DEBUT_GAMES = 3  # a depth player's season debut: only when he scored, after this many team games
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
        seasons = {}
        for s in rows:
            c = (s.get("teamName") or {}).get("default") if isinstance(s.get("teamName"), dict) else s.get("teamName")
            if c:
                seasons.setdefault(c, set()).add(s["season"])
        out[int(f.name.split(".")[0])] = {"gp": sum(s.get("gamesPlayed") or 0 for s in rows), "g": sum(s.get("goals") or 0 for s in rows),
                                          "a": sum(s.get("assists") or 0 for s in rows), "clubs": {c for c in clubs if c},
                                          "seasons": {c: len(v) for c, v in seasons.items()},
                                          "last_club": max(rows, key=lambda s: s["season"]).get("teamName", {}).get("default") if rows else None,
                                          "pos": land.get("position"),
                                          # goalies: starts, wins, shutouts, his first NHL season and the last with a win
                                          "gs": sum(s.get("gamesStarted") or 0 for s in rows), "w": sum(s.get("wins") or 0 for s in rows),
                                          "so": sum(s.get("shutouts") or 0 for s in rows),
                                          "first_season": min((s["season"] for s in rows if s.get("gamesPlayed")), default=None),
                                          "last_win_season": max((s["season"] for s in rows if s.get("wins")), default=None),
                                          # the key is there for every saved landing; empty means he was never drafted
                                          "undrafted": "draftDetails" in land and not land["draftDetails"],
                                          "round": (land.get("draftDetails") or {}).get("round")}
    return out


@lru_cache(maxsize=1)
def _playoff_dates() -> dict[int, list[str]]:
    """Every skater's and goalie's playoff game dates since 2023-24, oldest first."""
    out = {}
    for d in sorted(TABLES.glob("20*")):
        if not (d / "games.parquet").exists() or not (d / "players.parquet").exists():
            continue
        g = pl.read_parquet(d / "games.parquet", columns=["game_id", "game_type", "date"]).filter(pl.col("game_type") == PLAYOFFS)
        if g.is_empty():
            continue
        p = pl.read_parquet(d / "players.parquet", columns=["game_id", "player_id"]).join(g, on="game_id")
        for pid, day in p.select("player_id", "date").iter_rows():
            out.setdefault(pid, []).append(day)
    return {k: sorted(v) for k, v in out.items()}


def _last_any(pid: int, last_regular: str | None, day: str) -> str | None:
    """His last NHL game before `day`, playoffs included."""
    po = [d for d in _playoff_dates().get(pid, []) if d < day]
    return max([x for x in (last_regular, po[-1] if po else None) if x], default=None)


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


@lru_cache(maxsize=8)
def _wins(season: int) -> dict[int, int]:
    """game_id -> the winning goalie, for every finished regular-season game: the box's decision when saved, else the
    goalie in net for the winners when they scored the goal that put them ahead for good (the last goal in a
    shootout game goes to whoever finished it)."""
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return {}
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    ev = (pl.read_parquet(d / "events.parquet", columns=["game_id", "sort", "period_type", "type", "team_id", "home_goalie", "away_goalie"])
          .filter(pl.col("period_type") != "SO").sort("game_id", "sort"))
    by_game = ev.partition_by("game_id", as_dict=True)
    out = {}
    for g in games.iter_rows(named=True):
        gid = g["game_id"]
        box = RAW / str(season) / "box" / f"{gid}.json.gz"
        if box.exists():
            stats = json.loads(gzip.open(box).read()).get("playerByGameStats", {})
            w = next((x["playerId"] for side in ("homeTeam", "awayTeam") for x in stats.get(side, {}).get("goalies", []) if x.get("decision") == "W"), None)
            if w:
                out[gid] = w
                continue
        e = by_game.get((gid,))
        if e is None or g["home_score"] == g["away_score"]:
            continue
        home_won = g["home_score"] > g["away_score"]
        wid = g["home_id"] if home_won else g["away_id"]
        col = "home_goalie" if home_won else "away_goalie"
        lose = min(g["home_score"], g["away_score"])
        goals = e.filter((pl.col("type") == "goal") & (pl.col("team_id") == wid))
        if g["last_period"] == "SO" or goals.height <= lose:
            seen = e.filter(pl.col(col).is_not_null())
            w = seen[col][-1] if seen.height else None
        else:
            w = goals[col][lose]
        if w is None:  # the winners' net was empty for the goal (an extra attacker): their last goalie in before it
            seen = e.filter(pl.col(col).is_not_null())
            w = seen[col][-1] if seen.height else None
        if w is not None:
            out[gid] = int(w)
    return out


@lru_cache(maxsize=8)
def _shutouts(season: int) -> dict[int, list[int]]:
    """game_id -> goalies credited with a shutout: alone in net all game for a team that allowed no goal (shootouts
    aside)."""
    from pipeline.metrics import goalies
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return {}
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "period_type", "type", "team_id"]).filter(
        (pl.col("type") == "goal") & (pl.col("period_type") != "SO"))
    allowed = {}  # (game, team) -> goals against
    for gid, tid in ev.select("game_id", "team_id").iter_rows():
        allowed[(gid, tid)] = allowed.get((gid, tid), 0) + 1
    other = {r["game_id"]: {r["home_id"]: r["away_id"], r["away_id"]: r["home_id"]} for r in games.iter_rows(named=True)}
    pg = goalies.per_game(season).filter(pl.col("sec") > 0)
    n_in = {(a, b): c for a, b, c in pg.group_by("game_id", "team_id").len().iter_rows()}  # goalies who played
    out = {}
    for gid, pid, tid in pg.select("game_id", "goalie_id", "team_id").iter_rows():
        if gid in other and n_in.get((gid, tid)) == 1 and not allowed.get((gid, other[gid].get(tid))):
            out.setdefault(gid, []).append(pid)
    return out


def _goalie_record(pid: int, season: int, gid: int) -> dict:
    """His starts, wins and shutouts this season before game `gid`, and the date of his last win since 2023-24."""
    starts = sum(1 for k, v in _goalie_starts(season).items() if k < gid for x in v if x[0] == pid)
    wins = sum(1 for k, w in _wins(season).items() if k < gid and w == pid)
    sos = sum(1 for k, v in _shutouts(season).items() if k < gid and pid in v)
    return {"gs": starts, "w": wins, "so": sos}


@lru_cache(maxsize=1)
def _win_dates() -> dict[int, list[str]]:
    """Every goalie's win dates since 2023-24, oldest first."""
    from pipeline.config import FULL_SEASONS
    out = {}
    for s in [*FULL_SEASONS, CURRENT_SEASON]:
        d = TABLES / str(s) / "games.parquet"
        if not d.exists():
            continue
        day = dict(pl.read_parquet(d, columns=["game_id", "date"]).iter_rows())
        for k, w in _wins(s).items():
            out.setdefault(w, []).append(day[k])
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
                out.append({**item, "kind": "milestone", "what": what, "mark": mark, "have": have})
                break
        if g == 0 and g_now:
            out.append({**item, "kind": "first_goal", "debut": gp == 0, "nth": gp + 1, "undrafted": base["undrafted"]})
        elif gp == 0:
            out.append({**item, "kind": "debut", "points": pts_now, "first_round": base.get("round") == 1})
        elif pts == 0 and pts_now:
            out.append({**item, "kind": "first_point"})
        elif g_now and base["g"] + int(mine["g"].sum()) > 0:
            name = club.get(t)
            for_club = mine.filter(pl.col("team") == t)
            past_here = P.filter((pl.col("player_id") == pid) & (pl.col("team") == t) & (pl.col("date") < day))
            if name and name not in base["clubs"] and not past_here["g"].sum() and not for_club["g"].sum():
                out.append({**item, "kind": "club_goal"})
        if gp:  # his first game for a club he had never played for
            name = club.get(t)
            here = P.filter((pl.col("player_id") == pid) & (pl.col("team") == t) & (pl.col("date") < day))
            elsewhere = P.filter((pl.col("player_id") == pid) & (pl.col("team") != t) & (pl.col("date") < day))
            if name and name not in base["clubs"] and here.is_empty():
                moved = mine.filter(pl.col("team") != t).height > 0  # played for someone else earlier this season
                if moved or pts_now:
                    old = elsewhere["team"][-1] if elsewhere.height else None
                    old_name = club.get(old) if old else base.get("last_club")
                    n = base["seasons"].get(old_name, 0) + (1 if old and elsewhere.filter((pl.col("team") == old) & (pl.col("season") == season)).height else 0)
                    out[:] = [x for x in out if not (x["pid"] == pid and x["kind"] == "club_goal")]  # told in the debut
                    out.append({**item, "kind": "club_debut", "from": old, "from_name": old_name, "seasons": n, "moved": moved, "points": pts_now})
        if gp:  # a return: his last game, and how many of his team's games went by since
            hist = P.filter((pl.col("player_id") == pid) & (pl.col("date") < day)).tail(10)
            prev = hist.tail(1)
            regular = hist.height and hist["sec"].mean() >= (REGULAR_D if r["pos"] == "D" else REGULAR_F)
            if prev.height and regular:
                last_day = _last_any(pid, prev["date"][0], day)
                opened = G.filter(pl.col("season") == season)["date"].min()
                fresh = opened is not None and last_day < opened  # back after the summer: count this season's games only
                span = G.filter((pl.col("season") == season) if fresh else (pl.col("date") > last_day))
                missed = span.filter((pl.col("date") < day) & ((pl.col("home") == t) | (pl.col("away") == t))).height
                # a regular's first game of the season is news after any wait (eye test, 2026-10-14: Bedard's and
                # McAvoy's returns after six games led NHL.com and went untold); mid-season, after RETURN_GAMES
                if missed >= (SEASON_DEBUT_GAMES if fresh else RETURN_GAMES):
                    from datetime import date as Date
                    days = (Date.fromisoformat(day) - Date.fromisoformat(last_day)).days
                    out.append({**item, "kind": "return", "missed": missed, "since": last_day if days >= LONG_AWAY_DAYS else None,
                                "season_debut": fresh, "points": pts_now})
            elif prev.height and g_now and mine.is_empty():  # a depth player's season debut, told when he scored
                opened = G.filter(pl.col("season") == season)["date"].min()  # (Miroshnichenko, 2026-10-14)
                missed = G.filter((pl.col("season") == season) & (pl.col("date") < day) & ((pl.col("home") == t) | (pl.col("away") == t))).height
                if opened is not None and missed >= DEPTH_DEBUT_GAMES:
                    out.append({**item, "kind": "return", "missed": missed, "since": None, "season_debut": True, "points": pts_now})
    starts = _goalie_starts(season).get(gid, [])
    team_of = {game["home_id"][0]: game["home"][0], game["away_id"][0]: game["away"][0]}
    win = _wins(season).get(gid)
    shut = _shutouts(season).get(gid, [])
    cands = [(pid, tid, True) for pid, tid in starts]
    if win and win not in {p_ for p_, _ in starts}:  # a win in relief
        hs, as_ = game["home_score"][0], game["away_score"][0]
        cands.append((win, game["home_id"][0] if hs > as_ else game["away_id"][0], False))
    opened = G.filter(pl.col("season") == season)["date"].min()
    for pid, tid, started in cands:
        base = prior.get(pid)
        if base is None:
            continue
        t = team_of.get(tid)
        rec = _goalie_record(pid, season, gid)
        before = _goalie_last(pid, day)
        played_here = before is not None and opened is not None and before >= opened  # an earlier game this season
        won, so = win == pid, pid in shut
        if started and not base["gp"] and not played_here:
            out.append({"pid": pid, "team": t, "kind": "debut", "goalie": True, "won": won, "shutout": so})
            continue
        n_starts, n_wins, n_so = base["gs"] + rec["gs"], base["w"] + rec["w"], base["so"] + rec["so"]
        first_season = base["first_season"] or (_season_id(season) if played_here else None)
        seasons = season - first_season // 10000 if first_season else 0
        item = {"pid": pid, "team": t, "goalie": True, "won": won, "shutout": so, "started": started}
        if won and not n_wins:
            out.append({**item, "kind": "first_win", "start_n": n_starts + 1 if started else None, "seasons": seasons})
        elif so and not n_so:
            out.append({**item, "kind": "first_shutout"})
        elif started and not n_starts:
            out.append({**item, "kind": "first_start"})
        elif won:
            past = [d for d in _win_dates().get(pid, []) if d < day]
            from datetime import date as Date
            if past and (Date.fromisoformat(day) - Date.fromisoformat(past[-1])).days >= WIN_SINCE_DAYS:
                out.append({**item, "kind": "win_since", "since": past[-1]})
            elif not past and base["last_win_season"] and base["last_win_season"] < 20232024:  # before our game logs
                out.append({**item, "kind": "win_since", "since": None, "season": base["last_win_season"]})
        if started and before:  # a goalie back in net after a long time away
            missed = G.filter((pl.col("date") > before) & (pl.col("date") < day) & ((pl.col("home") == t) | (pl.col("away") == t))).height
            if missed >= GOALIE_RETURN_GAMES:  # backups sit for weeks, so a goalie's return needs a long gap
                out[:] = [x for x in out if not (x["pid"] == pid and x["kind"] == "win_since")]  # "back ..., and won"
                out.append({**item, "kind": "return", "missed": missed, "since": before})
    debuts = {x["pid"] for x in out if x["kind"] in ("club_debut", "debut")}  # a new club's debut says it already
    out = [x for x in out if not (x["kind"] == "return" and x["pid"] in debuts)]
    stars = {}
    if any(x["kind"] == "return" for x in out):
        from pipeline.metrics import relevancy
        stars = relevancy.standouts(season, day)
    for x in out:
        x["weight"] = WEIGHT[x["kind"]]
        if x["kind"] == "return" and not x.get("goalie"):  # a star back leads (Bedard, McAvoy); a depth player's season
            x["weight"] = 88 if x["pid"] in stars else 70 if not x.get("season_debut") else 68 if x.get("goals") else 55
            # (a depth player's season debut comes after club firsts)
    return sorted(out, key=lambda x: -x["weight"])
