"""The league's own season totals: goals, assists, power-play and shorthanded goals, plus-minus, penalty minutes, wins,
save percentage, power-play and penalty-kill percentages (Sora, 2026-10-14: fans fact-check against NHL.com, so the
plain counting stats should be the league's official ones, not rebuilt from the play-by-play).

One request per report per season from the NHL stats API (`api.nhle.com/stats/rest/en/{report}`, every row with
`limit=-1`): the current season on every update, a finished season once. Saved as returned, gzipped, in
data/raw/official/{season}/{report}.json.gz; offline runs read what is saved.
"""
import gzip
import json

import httpx

from pipeline.config import NHL_STATS, OFFLINE, RAW, REGULAR, season_id
from pipeline.ingest.client import get

REPORTS = ("skater/summary", "skater/realtime", "skater/timeonice", "skater/faceoffwins", "goalie/summary",
           "team/summary", "team/powerplay", "team/penaltykill", "team/realtime")


def path(season: int, report: str):
    return RAW / "official" / str(season) / f"{report.replace('/', '_')}.json.gz"


def update(season: int, current: bool = True) -> dict:
    """Fetch every report for `season` (again when `current`, else only the ones not saved yet)."""
    if OFFLINE:
        return {"skipped": "offline"}
    saved = 0
    for report in REPORTS:
        p = path(season, report)
        if p.exists() and not current:
            continue
        url = f"{NHL_STATS}/{report}?limit=-1&cayenneExp=seasonId={season_id(season)}%20and%20gameTypeId={REGULAR}"
        try:
            rows = get(url).json().get("data")
        except (httpx.HTTPError, RuntimeError, ValueError):
            continue  # the saved copy stays; tried again next run
        if rows is None:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(json.dumps(rows).encode()))
        saved += 1
    return {"saved": saved}


def read(season: int, report: str) -> list[dict]:
    p = path(season, report)
    return json.loads(gzip.open(p).read()) if p.exists() else []
