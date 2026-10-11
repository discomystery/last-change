"""Fetch NHL schedule, play-by-play, boxscores and shift charts into the raw cache.

Final games are fetched once and never again, except a shift chart that stops before the end of the game: the NHL
fills shift charts in during and after play, so one saved soon after the final horn can be missing the last minutes
(2026-10-10: CAR at CHI stopped at 13:05 of the third, PHI at BOS at 16:58). Those are fetched again on later runs
until they cover the whole game. Raw responses are stored gzipped, untouched.
"""
import gzip
import json
from pathlib import Path

from pipeline.config import FINAL_STATES, NHL_STATS, NHL_WEB, RAW, REGULAR, PLAYOFFS, season_id
from pipeline.ingest.client import get

TEAMS_FALLBACK_SEASON_PROBE = "CAR"
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


def fetch_game(season: int, game_id: int) -> int:
    """Download whatever is missing for one final game. Returns the number of requests made."""
    made = 0
    for kind, url in KINDS.items():
        path = raw_path(season, kind, game_id)
        if path.exists() and not (kind == "shifts" and _unfinished(season, game_id)):
            continue
        _write(path, get(url(game_id)).json())
        made += 1
    # The shift-chart API sometimes returns nothing (or an unfinished chart) for a game; fall back to the official
    # HTML reports, fetched again while they too stop short.
    backup = raw_path(season, "shifts_html", game_id)
    pbp = read_raw(season, "pbp", game_id)
    api_rows = read_raw(season, "shifts", game_id).get("data") or []
    if not shifts_cover(api_rows, pbp) and not (backup.exists() and shifts_cover(read_raw(season, "shifts_html", game_id)["data"], pbp)):
        from pipeline.ingest import html_shifts
        _write(backup, html_shifts.fetch(season, game_id, pbp))
        made += 2
    return made


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


def _unfinished(season: int, game_id: int) -> bool:
    rows = read_raw(season, "shifts", game_id).get("data") or []
    return bool(rows) and not shifts_cover(rows, read_raw(season, "pbp", game_id))


def read_shifts(season: int, game_id: int) -> tuple[list[dict], str]:
    """Shift rows for a game and where they came from."""
    rows = read_raw(season, "shifts", game_id).get("data") or []
    backup = raw_path(season, "shifts_html", game_id)
    if rows and (not backup.exists() or shifts_cover(rows, read_raw(season, "pbp", game_id))):
        return rows, "api"
    if backup.exists():
        html = read_raw(season, "shifts_html", game_id)["data"]
        if html and (not rows or shifts_cover(html, read_raw(season, "pbp", game_id))):
            return html, "html"
    return rows, "api" if rows else "missing"


def ingest_season(season: int, *, refresh_schedule: bool = False, limit: int | None = None) -> dict:
    games = [g for g in season_games(season, refresh=refresh_schedule) if g["state"] in FINAL_STATES]
    if limit:
        games = games[:limit]
    requests = sum(fetch_game(season, g["game_id"]) for g in games)
    return {"season": season, "final_games": len(games), "requests": requests}
