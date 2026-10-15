"""The league's own season totals: goals, assists, power-play and shorthanded goals, plus-minus, penalty minutes, wins,
save percentage, power-play and penalty-kill percentages (Sora, 2026-10-14: fans fact-check against NHL.com, so the
plain counting stats should be the league's official ones, not rebuilt from the play-by-play).

One request per report per season from the NHL stats API (`api.nhle.com/stats/rest/en/{report}`, every row with
`limit=-1`): the current season on every update, a finished season once. Saved as returned, gzipped, in
data/raw/official/{season}/{report}.json.gz; offline runs read what is saved.
"""
import gzip
import json
from datetime import date

import httpx

from pipeline.config import CURRENT_SEASON, NHL_STATS, OFFLINE, PLAYOFFS, RAW, REGULAR, season_id
from pipeline.ingest.client import get

REPORTS = ("skater/summary", "skater/realtime", "skater/timeonice", "skater/faceoffwins", "goalie/summary",
           "team/summary", "team/powerplay", "team/penaltykill", "team/realtime")


ICE_FROM = 1997  # the league's shift counts and ice time by strength start in 1997-98


def path(season: int, report: str, game_type: int = REGULAR):
    return RAW / "official" / str(season) / f"{report.replace('/', '_')}{'' if game_type == REGULAR else '_po'}.json.gz"


def _fetch(season: int, report: str, game_type: int) -> bool:
    url = f"{NHL_STATS}/{report}?limit=-1&cayenneExp=seasonId={season_id(season)}%20and%20gameTypeId={game_type}"
    try:
        rows = get(url).json().get("data")
    except (httpx.HTTPError, RuntimeError, ValueError):
        return False  # the saved copy stays; tried again next run
    if rows is None:
        return False
    p = path(season, report, game_type)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(rows).encode()))
    return True


def update(season: int, current: bool = True) -> dict:
    """Fetch every report for `season` (again when `current`, else only the ones not saved yet)."""
    if OFFLINE:
        return {"skipped": "offline"}
    saved = 0
    for report in REPORTS:
        p = path(season, report)
        if p.exists() and not current:
            continue
        saved += _fetch(season, report, REGULAR)
    return {"saved": saved}


def ice_history(today: date | None = None) -> dict:
    """Every finished season's shifts and ice time by strength (skater/timeonice), regular season and playoffs, for
    the career ledger (Sora, 2026-10-15: shift length on player pages). Fetched once each: about 60 requests the first
    time, then none, except last spring's playoffs, fetched again until July 1 so a Final in progress is not kept."""
    if OFFLINE:
        return {"skipped": "offline"}
    today = today or date.today()
    saved = 0
    for season in range(ICE_FROM, CURRENT_SEASON):
        for gt in (REGULAR, PLAYOFFS):
            done = path(season, "skater/timeonice", gt).exists()
            if done and not (gt == PLAYOFFS and today < date(season + 1, 7, 1)):
                continue
            saved += _fetch(season, "skater/timeonice", gt)
    return {"saved": saved}


def read(season: int, report: str, game_type: int = REGULAR) -> list[dict]:
    p = path(season, report, game_type)
    return json.loads(gzip.open(p).read()) if p.exists() else []
