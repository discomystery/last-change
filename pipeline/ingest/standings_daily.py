"""The league standings at the end of every game day, for the playoff race on preview and post-game pages (eye test on
2025-26, 2026-10-13: none of the March and April games we compared with outside coverage mentioned the race, and
every outside recap led with it: "lifts the Kings into playoff position", "the Capitals stay alive").

`/v1/standings/{date}` gives the table after that day's games (checked: 2026-04-01 had Colorado at 74 games including
that night's). A day is saved once every regular-season game on it is final, since a finished day never changes:
data/raw/standings/{season}/{date}.json.gz, with only what the race needs. Offline runs read what is saved.

`race(season, day, team)` reads a club's place in its conference: in (top three of the division, or one of the two
wild cards) or out, the line it is chasing or holding, games left, and the league's clinch mark (x playoff spot,
y division, z conference, p Presidents' Trophy, e eliminated).
"""
import gzip
import json
from datetime import date as Date, timedelta
from functools import lru_cache

import httpx
import polars as pl

from pipeline.config import FINAL_STATES, NHL_WEB, OFFLINE, RAW, REGULAR, TABLES
from pipeline.ingest.client import get

GAMES = 82
KEEP = ("teamAbbrev", "conferenceAbbrev", "conferenceName", "divisionAbbrev", "divisionName", "divisionSequence",
        "conferenceSequence", "wildcardSequence", "gamesPlayed", "points", "regulationWins", "clinchIndicator")


def _path(season: int, day: str):
    return RAW / "standings" / str(season) / f"{day}.json.gz"


def _finished_days(season: int) -> list[str]:
    """Regular-season game days on which every game is final."""
    g = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["game_type", "date", "state"]).filter(pl.col("game_type") == REGULAR)
    per = g.group_by("date").agg(done=pl.col("state").is_in(list(FINAL_STATES)).all())
    return sorted(per.filter(pl.col("done"))["date"].to_list())


def update(season: int, limit: int = 250) -> dict:
    """Save the standings after every finished game day not saved yet (a capped batch per run)."""
    if OFFLINE:
        return {"skipped": "offline"}
    todo = [d for d in _finished_days(season) if not _path(season, d).exists()][:limit]
    saved = 0
    for d in todo:
        try:
            rows = get(f"{NHL_WEB}/v1/standings/{d}").json().get("standings") or []
        except (httpx.HTTPError, RuntimeError):
            continue  # tried again next run
        keep = [{k: (r.get(k) or {}).get("default") if isinstance(r.get(k), dict) else r.get(k) for k in KEEP} for r in rows]
        if len(keep) < 30:
            continue
        _path(season, d).parent.mkdir(parents=True, exist_ok=True)
        _path(season, d).write_bytes(gzip.compress(json.dumps(keep).encode()))
        saved += 1
    return {"saved": saved, "left": max(0, len(todo) - saved)}


@lru_cache(maxsize=512)
def table(season: int, day: str) -> tuple | None:
    """The standings after `day`'s games, or None if they are not saved."""
    p = _path(season, day)
    if not p.exists():
        return None
    return tuple(json.loads(gzip.open(p).read()))


def before(season: int, day: str) -> tuple | None:
    """The standings going into `day`: the latest saved game day before it (None before the season's first game)."""
    d = Date.fromisoformat(day[:10])
    for _ in range(10):  # the All-Star or Olympic break can leave a gap of a week or more
        d -= timedelta(days=1)
        t = table(season, d.isoformat())
        if t is not None:
            return t
    return None


def race(rows: tuple | None, team: str) -> dict | None:
    """A club's place in the playoff race from one day's standings."""
    if not rows:
        return None
    me = next((r for r in rows if r["teamAbbrev"] == team), None)
    if me is None:
        return None
    conf = [r for r in rows if r["conferenceAbbrev"] == me["conferenceAbbrev"]]
    wild = sorted([r for r in conf if (r.get("wildcardSequence") or 0) > 0], key=lambda r: r["wildcardSequence"])
    in_div = (me.get("divisionSequence") or 99) <= 3
    wc = me.get("wildcardSequence") or 0
    spot = ("div", me["divisionSequence"]) if in_div else ("wc", wc) if wc in (1, 2) else ("out", wc)
    last_in = wild[1] if len(wild) > 1 else None   # the second wild card
    first_out = wild[2] if len(wild) > 2 else None
    if spot[0] == "out":
        rival, gap = last_in, (me["points"] - last_in["points"]) if last_in else None
    else:
        rival, gap = first_out, (me["points"] - first_out["points"]) if first_out else None
    return {"team": team, "spot": spot, "points": me["points"], "gp": me["gamesPlayed"], "left": GAMES - me["gamesPlayed"],
            "rival": rival["teamAbbrev"] if rival else None, "rival_gp": rival["gamesPlayed"] if rival else None, "gap": gap,
            "clinch": me.get("clinchIndicator"), "division": me.get("divisionName"), "conference": me.get("conferenceName")}
