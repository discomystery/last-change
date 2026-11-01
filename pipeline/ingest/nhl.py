"""Fetch NHL schedule, play-by-play, boxscores and shift charts into the raw cache.

Final games are fetched once and never again, except a shift chart that stops before the end of the game: the NHL
fills shift charts in during and after play, so one saved soon after the final horn can be missing the last minutes
(2026-10-10: CAR at CHI stopped at 13:05 of the third, PHI at BOS at 16:58). Those are fetched again on later runs
until they cover the whole game. The play-by-play and boxscore are fetched once more after the game has settled
(`SETTLE_HOURS` after puck drop): the NHL edits them after the horn (2026-10-14: DAL at COL's coach's challenge was
saved as a plain offside and only later marked as a challenge that took a goal off; 9 of 18 games that week changed).
A boxscore saved before the NHL added the goalies' decisions is fetched again on each run until it has them (`undecided`).
Raw responses are stored gzipped, untouched.
"""
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

from pipeline.config import FINAL_STATES, NHL_STATS, NHL_WEB, RAW, REGULAR, PLAYOFFS, season_id
from pipeline.ingest.client import get

KINDS = {
    "pbp": lambda gid: f"{NHL_WEB}/v1/gamecenter/{gid}/play-by-play",
    "box": lambda gid: f"{NHL_WEB}/v1/gamecenter/{gid}/boxscore",
    "shifts": lambda gid: f"{NHL_STATS}/shiftcharts?cayenneExp=gameId={gid}",
}


def raw_path(season: int, kind: str, game_id: int) -> Path:
    return RAW / str(season) / kind / f"{game_id}.json.gz"


def read_raw(season: int, kind: str, game_id: int) -> dict:
    with gzip.open(raw_path(season, kind, game_id), "rt") as f:
        return json.load(f)


def _write(path: Path, payload: dict) -> None:
    # Write to a temporary file and rename, so an interrupted run never leaves a half-written file.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with gzip.open(tmp, "wt") as f:
        json.dump(payload, f, separators=(",", ":"))
    tmp.replace(path)


def team_abbrevs(season: int) -> list[str]:
    """Every club that played in a season, from the standings on a mid-season date."""
    probe = f"{season + 1}-01-15" if season < 2026 else "now"
    data = get(f"{NHL_WEB}/v1/standings/{probe}").json()
    return sorted(t["teamAbbrev"]["default"] for t in data["standings"])


def season_games(season: int, *, refresh: bool = False) -> list[dict]:
    """One row per regular-season or playoff game, from the 32 club schedules."""
    path = RAW / str(season) / "schedule.json.gz"
    if path.exists() and not refresh:
        with gzip.open(path, "rt") as f:
            return json.load(f)
    games: dict[int, dict] = {}
    for abbr in team_abbrevs(season):
        sched = get(f"{NHL_WEB}/v1/club-schedule-season/{abbr}/{season_id(season)}").json()
        for g in sched["games"]:
            if g["gameType"] not in (REGULAR, PLAYOFFS):
                continue
            games[g["id"]] = {
                "game_id": g["id"],
                "season": season,
                "game_type": g["gameType"],
                "date": g["gameDate"],
                "start_utc": g.get("startTimeUTC"),
                "state": g["gameState"],
                "home": g["homeTeam"]["abbrev"],
                "away": g["awayTeam"]["abbrev"],
                "home_id": g["homeTeam"]["id"],
                "away_id": g["awayTeam"]["id"],
                "home_score": g["homeTeam"].get("score"),
                "away_score": g["awayTeam"].get("score"),
                "venue": (g.get("venue") or {}).get("default"),
                "last_period": (g.get("gameOutcome") or {}).get("lastPeriodType"),
            }
    rows = sorted(games.values(), key=lambda r: r["game_id"])
    _write(path, rows)
    return rows


SETTLE_HOURS = 6  # after puck drop, when a game's play-by-play and boxscore are fetched one last time
SETTLE_DAYS = 14  # only games this recent are settled (older ones were saved long after the edits)


def _settled_path(season: int) -> Path:
    return RAW / str(season) / "settled.json"


def _settled(season: int) -> set[int]:
    p = _settled_path(season)
    return set(json.loads(p.read_text())) if p.exists() else set()


def _resettle_path(season: int) -> Path:
    return RAW / str(season) / "resettle.json"


def _resettle(season: int) -> set[int]:
    p = _resettle_path(season)
    return set(json.loads(p.read_text())) if p.exists() else set()


def resettle(season: int, game_ids) -> list[int]:
    """Ask for saved games to be fetched once more on the next update, however old (the self-check's official totals
    found the NHL changed a goal's credit after the game had settled: Bains' goal in CAR at VAN, 2026-10-20, saved as
    Sasson's). Returns the games newly added."""
    new = sorted(set(game_ids) - _resettle(season))
    if new:
        _resettle_path(season).write_text(json.dumps(sorted(_resettle(season) | set(new))))
    return new


def settle_due(season: int, game_id: int, start_utc: str | None, now: datetime | None = None) -> bool:
    """True when a saved final game's play-by-play should be fetched once more: it started SETTLE_HOURS ago or more,
    within the last SETTLE_DAYS, and has not been settled yet; or the self-check asked for it again (`resettle`)."""
    if raw_path(season, "pbp", game_id).exists() and game_id in _resettle(season):
        return True
    if not start_utc or not raw_path(season, "pbp", game_id).exists() or game_id in _settled(season):
        return False
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    return timedelta(hours=SETTLE_HOURS) <= now - start <= timedelta(days=SETTLE_DAYS)


def undecided(season: int, game_id: int, start_utc: str | None, now: datetime | None = None) -> bool:
    """True when the saved boxscore of a recent game has no goalie decisions yet. The NHL marks a game FINAL at the horn
    and fills in the goalies' W / L / OTL and starter flags a little later, when it turns the game OFF (2026-11-01: WPG at
    CAR was saved at FINAL, so Kochetkov's win showed with no decision on his player page until the 6-hour settle). Such a
    boxscore is fetched again on every run, one request a game, until the decisions are there or SETTLE_DAYS pass."""
    path = raw_path(season, "box", game_id)
    if not start_utc or not path.exists():
        return False
    now = now or datetime.now(timezone.utc)
    if now - datetime.fromisoformat(start_utc.replace("Z", "+00:00")) > timedelta(days=SETTLE_DAYS):
        return False
    try:
        box = read_raw(season, "box", game_id)
    except (OSError, ValueError):
        return True
    if box.get("gameState") == "OFF":
        return False
    stats = box.get("playerByGameStats") or {}
    return not any(g.get("decision") for side in ("homeTeam", "awayTeam") for g in (stats.get(side) or {}).get("goalies", []))


def fetch_game(season: int, game_id: int, start_utc: str | None = None) -> int:
    """Download whatever is missing for one final game. Returns the number of requests made."""
    made = 0
    fresh = False  # the shift chart was fetched in this call
    settle = settle_due(season, game_id, start_utc)
    again = {"pbp", "box"} if settle else {"box"} if undecided(season, game_id, start_utc) else set()
    for kind, url in KINDS.items():
        path = raw_path(season, kind, game_id)
        if path.exists() and not (kind == "shifts" and _unfinished(season, game_id)) and kind not in again:
            continue
        _write(path, get(url(game_id)).json())
        made += 1
        fresh = fresh or kind == "shifts"
    if settle:
        done = _settled(season) | {game_id}
        _settled_path(season).write_text(json.dumps(sorted(done)))
        again = _resettle(season)
        if game_id in again:
            _resettle_path(season).write_text(json.dumps(sorted(again - {game_id})))
    # The shift-chart API sometimes returns nothing, an unfinished chart, or a chart that disagrees with the rest of the
    # game file; fall back to the official HTML reports, fetched again while they too stop short.
    backup = raw_path(season, "shifts_html", game_id)
    pbp = read_raw(season, "pbp", game_id)
    api_rows = read_raw(season, "shifts", game_id).get("data") or []
    if backup.exists() and shifts_cover(read_raw(season, "shifts_html", game_id)["data"], pbp):
        return made
    if not shifts_cover(api_rows, pbp):
        made += _fetch_backup(season, game_id, pbp)
    elif fresh and not backup.exists() and suspect(season, game_id, api_rows):
        try:
            made += _fetch_backup(season, game_id, pbp)
        except Exception as exc:  # the API chart covers the game, so it can stand in
            print(f"no HTML shift report for {game_id}: {exc!r}")
    return made


def _fetch_backup(season: int, game_id: int, pbp: dict) -> int:
    from pipeline.ingest import html_shifts

    _write(raw_path(season, "shifts_html", game_id), html_shifts.fetch(season, game_id, pbp))
    return 2


def game_end(pbp: dict) -> int:
    """Game seconds at the last play before any shootout."""
    from pipeline.build.stints import game_seconds

    plays = [p for p in pbp["plays"] if p["periodDescriptor"]["periodType"] != "SO"]
    return max((game_seconds(p["periodDescriptor"]["number"], p["timeInPeriod"]) for p in plays), default=0)


def shifts_cover(rows: list[dict], pbp: dict, *, slack: int = 5) -> bool:
    """True when both teams' shifts run to the end of the game. Every saved game from 2023-24 to 2025-26 passes."""
    from pipeline.build.stints import game_seconds

    end = game_end(pbp)
    for team in (pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]):
        ends = [game_seconds(r["period"], r["endTime"]) for r in rows
                if r.get("typeCode") == 517 and r.get("teamId") == team and r.get("endTime")]
        if not ends or max(ends) < end - slack:
            return False
    return True


def shifts_complete(season: int, game_id: int) -> bool:
    """True when the saved API chart or HTML report covers the whole game. Right after the horn the NHL is often still
    filling its shift charts in, and until it has, who was on the ice (strength, 5-on-5 play, lines) is unknown for
    the missing stretch."""
    if not raw_path(season, "pbp", game_id).exists():
        return False
    pbp = read_raw(season, "pbp", game_id)
    for kind in ("shifts", "shifts_html"):
        if raw_path(season, kind, game_id).exists() and shifts_cover(_game_rows(read_raw(season, kind, game_id).get("data") or [], pbp), pbp):
            return True
    return False


def _unfinished(season: int, game_id: int) -> bool:
    rows = read_raw(season, "shifts", game_id).get("data") or []
    return bool(rows) and not shifts_cover(rows, read_raw(season, "pbp", game_id))


def _game_rows(rows: list[dict], pbp: dict) -> list[dict]:
    """Only the two clubs' shifts: one saved API chart (BUF at NJD, 2025-12-21) also carries another game's."""
    teams = (pbp["homeTeam"]["id"], pbp["awayTeam"]["id"])
    return [r for r in rows if r.get("teamId") in teams]


SUSPECT = 3  # problems (see shifts_problems) at which a shift chart is checked against the HTML reports
CHECK_TYPES = ("shot-on-goal", "missed-shot", "blocked-shot", "goal", "hit", "giveaway", "takeaway", "faceoff")


def shifts_problems(rows: list[dict], pbp: dict, box: dict) -> int:
    """How far a shift chart disagrees with the rest of the game file: plays where the skaters it puts on the ice do not
    match the play-by-play situation code, plus players whose shifts add up to more than 5 seconds off the boxscore's
    ice time. Most saved games score 0; the official HTML reports scored lower than the API chart in every suspect game
    checked on 2026-10-11 (missing overtime shifts, a skater who never comes off, another game's shifts mixed in)."""
    from pipeline.build.stints import clean_shifts, clock_to_seconds, game_seconds, on_ice, penalty_shot

    roster = {r["playerId"]: r for r in pbp["rosterSpots"]}
    goalies = {pid for pid, r in roster.items() if r["positionCode"] == "G"}
    max_period = 4 if pbp.get("gameType") == REGULAR else 99
    shifts = clean_shifts(_game_rows(rows, pbp), pbp["homeTeam"]["id"], goalies, max_period=max_period)
    bad = 0
    for p in pbp["plays"]:
        code, typ = p.get("situationCode"), p["typeDescKey"]
        if typ not in CHECK_TYPES or not code or len(code) != 4 or penalty_shot(code) or p["periodDescriptor"]["periodType"] == "SO":
            continue
        on = on_ice(shifts, game_seconds(p["periodDescriptor"]["number"], p["timeInPeriod"]), faceoff=typ == "faceoff")
        home = sum(1 for x in on if x.home and not x.goalie)
        away = sum(1 for x in on if not x.home and not x.goalie)
        bad += (home, away) != (int(code[2]), int(code[1]))
    toi: dict[int, int] = {}
    for x in shifts:
        toi[x.player_id] = toi.get(x.player_id, 0) + x.end - x.start
    for side in ("homeTeam", "awayTeam"):
        for group in ("forwards", "defense", "goalies"):
            for pl in box["playerByGameStats"][side][group]:
                official = clock_to_seconds(pl.get("toi") or "0:00")  # a dressed player who never took a shift has no toi (L'Heureux, COL at DAL 2026-10-13)
                if official or pl["playerId"] in toi:
                    bad += abs(toi.get(pl["playerId"], 0) - official) > 5
    return bad


def suspect(season: int, game_id: int, rows: list[dict] | None = None) -> bool:
    if rows is None:
        rows = read_raw(season, "shifts", game_id).get("data") or []
    return bool(rows) and shifts_problems(rows, read_raw(season, "pbp", game_id), read_raw(season, "box", game_id)) >= SUSPECT


def read_shifts(season: int, game_id: int) -> tuple[list[dict], str]:
    """Shift rows for a game and where they came from: the API chart unless the HTML reports cover more of the game or
    agree better with the play-by-play and boxscore."""
    pbp = read_raw(season, "pbp", game_id)
    rows = _game_rows(read_raw(season, "shifts", game_id).get("data") or [], pbp)
    backup = raw_path(season, "shifts_html", game_id)
    html = _game_rows(read_raw(season, "shifts_html", game_id)["data"], pbp) if backup.exists() else []
    if not html:
        return rows, "api" if rows else "missing"
    if not rows:
        return html, "html"
    box = read_raw(season, "box", game_id)
    best = min(((rows, "api"), (html, "html")), key=lambda c: (not shifts_cover(c[0], pbp), shifts_problems(c[0], pbp, box)))
    return best


def check_saved(season: int) -> list[int]:
    """Once per season: compare every saved shift chart with its play-by-play and boxscore and fetch the HTML reports
    for the suspect ones (2026-10-11 audit: 119 of 4,266 games from 2023-24 on). A marker file records that the check
    ran. Returns the games whose reports were fetched; skipped offline."""
    from pipeline.config import OFFLINE

    marker = RAW / str(season) / "shifts_checked.json"
    if OFFLINE or marker.exists():
        return []
    fetched = []
    for g in season_games(season):
        gid = g["game_id"]
        if g["state"] not in FINAL_STATES or not raw_path(season, "shifts", gid).exists() or raw_path(season, "shifts_html", gid).exists():
            continue
        if suspect(season, gid):
            try:
                _fetch_backup(season, gid, read_raw(season, "pbp", gid))
                fetched.append(gid)
            except Exception as exc:  # a missing report must not stop the run; the API chart stays in use
                print(f"no HTML shift report for {gid}: {exc!r}")
    marker.write_text(json.dumps({"rule": 1, "fetched": fetched}))
    return fetched


def ingest_season(season: int, *, refresh_schedule: bool = False, limit: int | None = None) -> dict:
    games = [g for g in season_games(season, refresh=refresh_schedule) if g["state"] in FINAL_STATES]
    if limit:
        games = games[:limit]
    requests = sum(fetch_game(season, g["game_id"], g.get("start_utc")) for g in games)
    return {"season": season, "final_games": len(games), "requests": requests}
