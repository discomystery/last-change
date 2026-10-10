"""Who was available to each club, day by day: one row per player per club per date, plus a table of roster moves.

Built from three saved sources, plus the league's player page (current club) for anyone no listing has today, cached
daily in data/raw/players/ (offline runs use the cache):
  rosters.parquet         every club's roster listing, once a day (history starts 2026-10-10)
  game_rosters.parquet    each game's dressed players and listed scratches
  status_reports.parquet  injury and lineup news read from the NHL.com Status Report

Table data/tables/{season}/availability.parquet, one row per (date, team, player_id), from the season's first game
day to the run day (Eastern). Columns:
  date, team, player_id, first, last, pos
  game_id         the club's game that day, if any
  listed          on the club's roster listing that day (null before listings were recorded)
  lineup          that day's game: "dressed", "scratched", "off_roster" (not on the game-day roster at all), or null
                  (no game, or the game roster is not posted yet)
  report_status   the NHL.com status in effect (see ingest/status_report.py: season, long, ir, week, personal,
                  suspended, day, out, playing, assigned, recalled), with report_date, report_injury, report_timeline,
                  report_url. A report stays in effect until a newer one about him, or until he dresses for a game
                  after it.
  status          one label, first that applies:
                    playing     dressed in that day's game
                    injured     an injury report in effect (season, long, ir, week, day, out)
                    suspended   suspension reported
                    personal    out for personal or family reasons
                    scratched   on the game-day roster, did not dress, nothing reported (healthy or minor, unknown)
                    no_club     a regular for this club last season whom the league lists with no club now and who
                                has not played this season (unsigned, retired, abroad: the data cannot say which)
                    not_listed  the league says the club holds him, but its roster listing leaves him off (in the
                                minors, or some long-term injuries), nothing reported
                    off_roster  left off the game-day roster, nothing reported (for a listed player almost always
                                injured reserve)
                    available   anything else (no game that day, nothing reported)
  last_played     his last date dressed for this club this season (null if none)
  games_missed    the club's games in a row, up to and including this date, that he did not dress for (counted from
                  his last game for the club this season, or from opening night)

Who counts as with a club on a date: on that day's listing when one was recorded; otherwise anyone who dressed or was
scratched for the club by then and has not since appeared for another club, plus last season's regulars (20+ games
for the club) whose rights the club still holds or no club holds, plus players the club holds but does not list.
Players who move to another club drop off.

Table data/tables/{season}/moves.parquet: date, player_id, first, last, from_team, to_team (either may be null),
source ("listing" when two days' listings differ, "games" when he first appears for a new club before listings
were recorded).
"""
from datetime import date as Date, timedelta

import httpx
import polars as pl

from pipeline.config import OFFLINE, PLAYOFFS, RAW, REGULAR, TABLES
from pipeline.ingest import game_rosters, nhl, rosters, status_report
from pipeline.ingest.rosters import today
from pipeline.metrics import patterns

INJURED = {"season", "long", "ir", "week", "day", "out"}
REGULAR_GAMES = 20  # last season's games for a club to count as one of its regulars
SCHEMA = {"date": pl.Utf8, "team": pl.Utf8, "player_id": pl.Int64, "first": pl.Utf8, "last": pl.Utf8, "pos": pl.Utf8,
          "game_id": pl.Int64, "listed": pl.Boolean, "lineup": pl.Utf8, "report_status": pl.Utf8, "report_date": pl.Utf8,
          "report_injury": pl.Utf8, "report_timeline": pl.Utf8, "report_url": pl.Utf8, "status": pl.Utf8,
          "last_played": pl.Utf8, "games_missed": pl.Int64}
MOVES = {"date": pl.Utf8, "player_id": pl.Int64, "first": pl.Utf8, "last": pl.Utf8, "from_team": pl.Utf8,
         "to_team": pl.Utf8, "source": pl.Utf8}


def path(season: int):
    return TABLES / str(season) / "availability.parquet"


def moves_path(season: int):
    return TABLES / str(season) / "moves.parquet"


def _last_season_regulars(season: int) -> dict[int, tuple[str, dict]]:
    """player_id -> (club, name info) for last season's regulars, by the club he played most for (ties: latest)."""
    d = TABLES / str(season - 1)
    if not (d / "players.parquet").exists():
        return {}
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    side = pl.concat([games.select("game_id", team_id="home_id", team="home"), games.select("game_id", team_id="away_id", team="away")])
    ps = pl.read_parquet(d / "players.parquet").join(side, on=["game_id", "team_id"])
    counts = (ps.group_by("player_id", "team").agg(pl.len().alias("n"), pl.col("game_id").max().alias("last_game"),
                                                    pl.col("first").last(), pl.col("last").last(), pl.col("pos").last())
              .sort("n", "last_game", descending=True).unique("player_id", keep="first")
              .filter(pl.col("n") >= REGULAR_GAMES))
    return {r["player_id"]: (r["team"], {"first": r["first"], "last": r["last"], "pos": r["pos"]}) for r in counts.iter_rows(named=True)}


def build(season: int, day: str | None = None) -> dict:
    day = day or today()
    games = (pl.DataFrame(nhl.season_games(season))
             .filter(pl.col("game_type").is_in([REGULAR, PLAYOFFS])))
    team_games: dict[str, dict[str, int]] = {}
    for g in games.iter_rows(named=True):
        for t in (g["home"], g["away"]):
            team_games.setdefault(t, {})[g["date"]] = g["game_id"]
    start = games["date"].min()
    days = [(Date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range((Date.fromisoformat(day) - Date.fromisoformat(start)).days + 1)]

    gr = game_rosters.load(season)
    lst = pl.read_parquet(rosters.path(season)) if rosters.path(season).exists() else None
    rep = status_report.load(season)
    prev = _last_season_regulars(season)

    names: dict[int, dict] = {pid: info for pid, (_, info) in prev.items()}
    for df in (gr, lst):
        if df is not None and df.height:
            for r in df.select("player_id", "first", "last", "pos").iter_rows(named=True):
                cur = names.setdefault(r["player_id"], {"first": None, "last": None, "pos": None})
                for k in ("first", "last", "pos"):
                    cur[k] = r[k] or cur[k]

    # Game rosters by (team, date) and each player's appearances in date order.
    lineup: dict[tuple[str, str], dict[int, str]] = {}
    posted: set[tuple[str, str]] = set()
    seen_with: dict[int, list[tuple[str, str]]] = {}
    for r in gr.sort("date").iter_rows(named=True):
        lineup.setdefault((r["team"], r["date"]), {})[r["player_id"]] = r["status"]
        if r["final"]:
            posted.add((r["team"], r["date"]))  # before the game only scratches are known, so nobody is "off" yet
        seen_with.setdefault(r["player_id"], []).append((r["date"], r["team"]))

    listing: dict[str, dict[str, set[int]]] = {}
    if lst is not None:
        for r in lst.iter_rows(named=True):
            listing.setdefault(r["date"], {}).setdefault(r["team"], set()).add(r["player_id"])
    listing_days = sorted(listing)
    latest_listing = listing[listing_days[-1]] if listing_days else {}
    listed_anywhere_now = {p for ps in latest_listing.values() for p in ps}
    team_now = {p: t for t, ps in latest_listing.items() for p in ps}
    # Players no listing has today: ask the league which club holds his rights (cached daily). A club can hold a
    # player it does not list (sent to the minors, some long-term injuries); None means no club at all.
    contract: dict[int, str | None] = {}
    if listing_days:
        for pid in sorted((set(prev) | set(seen_with)) - listed_anywhere_now):
            if OFFLINE and not (RAW / "players" / f"{pid}.json.gz").exists():
                continue  # never looked up: unknown, not "no club"
            try:
                contract[pid] = patterns._current_team(pid, listing_days[-1])
            except (httpx.HTTPError, RuntimeError):
                pass  # unknown today; tried again next run
    for pid, t in contract.items():
        if t:
            team_now[pid] = t
    clubs_this_season = {pid: {t for _, t in apps} for pid, apps in seen_with.items()}

    # Reports per player, by date; the club a report belongs to is his club at the time (filled in below).
    reports: dict[int, list[dict]] = {}
    for r in rep.sort("date").iter_rows(named=True):
        reports.setdefault(r["player_id"], []).append(r)

    def team_before(pid: int, d: str) -> str | None:
        """The club he last appeared for (dressed or scratched) on or before d."""
        last = None
        for dd, t in seen_with.get(pid, []):
            if dd > d:
                break
            last = t
        return last

    rows = []
    for d in days:
        listing_today = listing.get(d)
        members: dict[str, set[int]] = {}
        if listing_today is not None:
            for t, ps in listing_today.items():
                members.setdefault(t, set()).update(ps)
        else:
            for pid, apps in seen_with.items():
                t = team_before(pid, d)
                if t:
                    members.setdefault(t, set()).add(pid)
            for pid, (t, _) in prev.items():
                if team_before(pid, d) is None and team_now.get(pid, t) == t:
                    members.setdefault(t, set()).add(pid)
            for pid, rs in reports.items():
                if team_before(pid, d) is None and pid not in prev and any(x["date"] <= d for x in rs) and pid in team_now:
                    members.setdefault(team_now[pid], set()).add(pid)
        for pid, t in contract.items():
            if t is None and pid in prev and pid not in seen_with:
                members.setdefault(prev[pid][0], set()).add(pid)  # last season's regular, with no club now
            elif t and (prev.get(pid, (None,))[0] == t or t in clubs_this_season.get(pid, ())) and team_before(pid, d) in (None, t):
                members.setdefault(t, set()).add(pid)  # the club holds him but does not list him
        for t, ps in members.items():  # anyone who dressed that day belongs, listing or not
            ps.update(p for p, s in lineup.get((t, d), {}).items())
        for t, ps in members.items():
            gid = team_games.get(t, {}).get(d)
            on_game = lineup.get((t, d), {})
            for pid in ps:
                lu = on_game.get(pid)
                if lu is None and (t, d) in posted and gid is not None:
                    lu = "off_roster"
                rows.append({"date": d, "team": t, "player_id": pid, "game_id": gid, "lu": lu,
                             "listed": (pid in listing_today.get(t, set())) if listing_today is not None else None})

    if not rows:
        pl.DataFrame(schema=SCHEMA).write_parquet(path(season))
        pl.DataFrame(schema=MOVES).write_parquet(moves_path(season))
        return {"rows": 0}

    # Walk each (team, player) through the days to carry reports, last game and games missed.
    rows.sort(key=lambda r: (r["team"], r["player_id"], r["date"]))
    out = []
    key, last_played, missed, report = None, None, 0, None
    for r in rows:
        k = (r["team"], r["player_id"])
        if k != key:
            key, last_played, missed, report = k, None, 0, None
        pid, d = r["player_id"], r["date"]
        for x in reports.get(pid, []):  # newest report on or before today replaces the old one
            if x["date"] <= d and (report is None or x["date"] > report["date"]):
                report = x
        if report and last_played and last_played > report["date"]:
            report = None  # he has dressed since
        if r["lu"] == "dressed":
            last_played, missed = d, 0
            if report and report["date"] <= d:
                report = None
        elif r["game_id"] is not None and r["lu"] is not None:
            missed += 1
        rs = report["status"] if report else None
        if r["lu"] == "dressed":
            status = "playing"
        elif rs in INJURED:
            status = "injured"
        elif rs in ("suspended", "personal"):
            status = rs
        elif r["lu"] == "scratched":
            status = "scratched"
        elif contract.get(pid, "?") is None and pid not in seen_with:
            status = "no_club"
        elif r["listed"] is False:
            status = "not_listed"
        elif r["lu"] == "off_roster":
            status = "off_roster"
        else:
            status = "available"
        n = names.get(pid, {})
        out.append({"date": d, "team": r["team"], "player_id": pid, "first": n.get("first"), "last": n.get("last"),
                    "pos": n.get("pos"), "game_id": r["game_id"], "listed": r["listed"], "lineup": r["lu"],
                    "report_status": rs, "report_date": report["date"] if report else None,
                    "report_injury": report["injury"] if report else None,
                    "report_timeline": report["timeline"] if report else None, "report_url": report["url"] if report else None,
                    "status": status, "last_played": last_played, "games_missed": missed})
    table = pl.DataFrame(out, schema=SCHEMA).sort("date", "team", "player_id")
    table.write_parquet(path(season))

    moves = _moves(listing, listing_days, seen_with, names)
    pl.DataFrame(moves, schema=MOVES).sort("date", "player_id").write_parquet(moves_path(season))
    now = table.filter(pl.col("date") == day)
    return {"rows": table.height, "today": dict(now.group_by("status").len().iter_rows()), "moves": len(moves)}


def _moves(listing, listing_days, seen_with, names) -> list[dict]:
    out = []
    first_listing = listing_days[0] if listing_days else None
    for pid, apps in seen_with.items():  # before listings: a first appearance for a new club
        prev_team = None
        for d, t in apps:
            if prev_team and t != prev_team and (first_listing is None or d <= first_listing):
                out.append({"date": d, "player_id": pid, "from_team": prev_team, "to_team": t, "source": "games"})
            prev_team = t
    for a, b in zip(listing_days, listing_days[1:]):
        before = {p: t for t, ps in listing[a].items() for p in ps}
        after = {p: t for t, ps in listing[b].items() for p in ps}
        teams_b = set(listing[b])
        for p in set(before) | set(after):
            f, t = before.get(p), after.get(p)
            if f == t or (f and f not in teams_b):
                continue  # unchanged, or that club's listing failed to load that day
            out.append({"date": b, "player_id": p, "from_team": f, "to_team": t, "source": "listing"})
    for m in out:
        n = names.get(m["player_id"], {})
        m["first"], m["last"] = n.get("first"), n.get("last")
    return out


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)


def on(season: int, day: str, team: str | None = None) -> pl.DataFrame:
    """Everyone with a club on a date (all clubs, or one)."""
    t = load(season).filter(pl.col("date") == day)
    return t.filter(pl.col("team") == team) if team else t
