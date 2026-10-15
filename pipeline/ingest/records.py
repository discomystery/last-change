"""Franchise scoring lists from the NHL's records site (records.nhl.com, allowed by Sora 2026-10-15).

Wire recaps tell a player moving up his club's all-time list ("moved past Eric Staal for second on the Hurricanes'
all-time points list"). The records site has each franchise's career regular-season scoring, which gives the retired
names a player is chasing. The player's own total, and any other player with a complete career on file, comes from our
own game logs instead (careers.load_all, which matched the records site exactly for Aho: 724 points with Carolina),
so a story never depends on when the records site last updated.

Saved at raw/records/{franchises.json, leaders/{franchiseId}.json}: franchises.json maps each season's team code to
its NHL franchise (the old Jets' WPG and today's WPG are different franchises, so codes are matched by season);
leaders are the top LEADERS by points (with goals and assists) per franchise. Refreshed weekly, one request per
franchise; skipped offline or when the site cannot be reached.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache

from pipeline.config import OFFLINE, RAW
from pipeline.ingest.client import get

DIR = RAW / "records"
BASE = "https://records.nhl.com/site/api"
LEADERS = 100  # deep enough that the top ten in goals and in assists are almost always inside the top 100 in points
REFRESH = 7 * 86400


def _read(name: str):
    p = DIR / name
    return json.loads(p.read_text()) if p.exists() else None


def _save(name: str, body) -> None:
    p = DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, separators=(",", ":"), ensure_ascii=False))


def _query(path: str, **params) -> list[dict]:
    from urllib.parse import urlencode
    return get(f"{BASE}/{path}?{urlencode(params)}").json()["data"]


def update() -> dict:
    """Refreshes the franchise map and every active franchise's leaders once a week."""
    if OFFLINE:
        return {"skipped": "offline"}
    saved = _read("franchises.json")
    if saved and time.time() - saved.get("fetched", 0) < REFRESH:
        return {"skipped": "fresh"}
    try:
        rows = _query("franchise-season-results", cayenneExp="gameTypeId=2", limit=-1)
        active = {r["franchiseId"] for r in _query("franchise-team-totals", cayenneExp="gameTypeId=2 and activeTeam=true", limit=-1)}
        by_season = {}
        for r in rows:
            by_season.setdefault(str(r["seasonId"]), {})[r["triCode"]] = r["franchiseId"]
        for fid in sorted(active):
            lead = _query("skater-career-scoring-regular-season", cayenneExp=f"franchiseId={fid}",
                          sort=json.dumps([{"property": "points", "direction": "DESC"}]), limit=LEADERS)
            _save(f"leaders/{fid}.json", [{k: r.get(k) for k in ("playerId", "firstName", "lastName", "goals", "assists", "points",
                                                                  "gamesPlayed", "activePlayer")} for r in lead])
    except Exception as e:  # a records outage must never stop the update
        print(f"records unavailable: {e!r}")
        return {"error": repr(e)[:200]}
    _save("franchises.json", {"fetched": time.time(), "seasons": by_season})
    _seasons.cache_clear()
    franchise_of.cache_clear()
    leaders.cache_clear()
    return {"franchises": len(active)}


@lru_cache(maxsize=None)
def _seasons() -> dict:
    return (_read("franchises.json") or {}).get("seasons", {})


@lru_cache(maxsize=4096)
def franchise_of(season: str, abbr: str) -> int | None:
    """The NHL franchise a team code played for in a season ("20262027", "CAR"); a season the map has not seen yet
    uses the latest season's codes."""
    s = _seasons()
    if not s:
        return None
    if season in s:
        return s[season].get(abbr)
    return s[max(s)].get(abbr)


def seasons_of(fid: int) -> int:
    """How many regular seasons a franchise has played, this one included."""
    return sum(1 for codes in _seasons().values() if fid in codes.values())


@lru_cache(maxsize=None)
def leaders(fid: int) -> list[dict]:
    return _read(f"leaders/{fid}.json") or []
