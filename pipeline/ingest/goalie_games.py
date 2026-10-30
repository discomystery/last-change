"""Every franchise's goalie decisions game by game, for goalie streak records (Sora, 2026-10-30: Kochetkov's nine
straight wins to start 2026-27, the longest season-opening run by any goalie in Hurricanes history, never made a
Carolina post-game page).

The NHL stats API's game-level goalie report (`goalie/summary?isGame=true`, one request per franchise, every regular
season since 1917-18) gives each goalie's decision in every game: a win, a loss, an overtime/shootout loss or a tie.
Finished seasons never change, so a franchise is fetched once and again only after a season ends (the file says the
last season it holds). This season's decisions come from our own tables (`export/goalie_streaks.py`), so a rebuilt
page tells the streak as it stood that night.

Coverage is checked against the franchise's games played per season (`team/summary`, one more request): a season
whose decisions don't add up to its games, or a season the franchise did not play, breaks the record book, so records
are only claimed from the first season after the last such gap ("since 1918-19" for Montréal, whose 1917-18 report
is a game short; 1999-2000 for Winnipeg, since the NHL files the 1979-96 Jets under today's club).

Every franchise is saved, the 40 the records site lists, past ones too, so league-wide "longest since" lines hold.
Saved at raw/records/goalie_games/{franchiseId}.json.gz: {through, gp, decisions (per season), rows: [[season,
game_id, date, player_id, name, decision]]} with decision W / L / O / T; franchises.json lists the ids fetched.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from urllib.parse import urlencode

from pipeline.config import CURRENT_SEASON, NHL_STATS, OFFLINE, RAW
from pipeline.ingest.records import BASE as RECORDS
from pipeline.ingest.client import get

DIR = RAW / "records" / "goalie_games"
LOCKOUT = 2004  # 2004-05 was never played, so it is no gap


def _path(fid: int):
    return DIR / f"{fid}.json.gz"


def _season_id(season: int) -> int:
    return int(f"{season}{season + 1}")


def _fetch(fid: int) -> dict:
    exp = f"franchiseId={fid} and gameTypeId=2 and seasonId<{_season_id(CURRENT_SEASON)}"
    q = {"isAggregate": "false", "isGame": "true", "limit": -1, "start": 0, "cayenneExp": exp,
         "sort": json.dumps([{"property": "gameDate", "direction": "ASC"}, {"property": "gameId", "direction": "ASC"}])}
    rows = get(f"{NHL_STATS}/goalie/summary?{urlencode(q)}").json()["data"]
    teams = get(f"{NHL_STATS}/team/summary?" + urlencode({"isAggregate": "false", "isGame": "false", "limit": -1,
                                                          "cayenneExp": exp})).json()["data"]
    out, per = [], {}
    for r in rows:
        dec = "W" if r.get("wins") else "L" if r.get("losses") else "O" if r.get("otLosses") else "T" if r.get("ties") else None
        if not dec:
            continue
        s = r["gameId"] // 1_000_000
        per[s] = per.get(s, 0) + 1
        out.append([s, r["gameId"], r["gameDate"], r["playerId"], r.get("goalieFullName"), dec])
    gp = {t["seasonId"] // 10000: t["gamesPlayed"] for t in teams}
    return {"through": CURRENT_SEASON - 1, "gp": gp, "decisions": per, "rows": out}


def update() -> dict:
    """Fetches every franchise, past ones included (league records need them), once, and again after a season ends;
    skipped offline or when unreachable."""
    if OFFLINE:
        return {"skipped": "offline"}
    done = 0
    try:
        fids = [f["id"] for f in get(f"{RECORDS}/franchise").json()["data"]]
        for fid in fids:
            saved = load(fid)
            if saved and saved.get("through", 0) >= CURRENT_SEASON - 1:
                continue
            body = _fetch(fid)
            DIR.mkdir(parents=True, exist_ok=True)
            with gzip.open(_path(fid), "wt") as f:
                json.dump(body, f, separators=(",", ":"), ensure_ascii=False)
            done += 1
        (DIR / "franchises.json").write_text(json.dumps(sorted(fids)))
    except Exception as e:  # never stops the update
        print(f"goalie game logs unavailable: {e!r}")
        return {"error": repr(e)[:200], "fetched": done}
    load.cache_clear()
    league_from.cache_clear()
    return {"fetched": done}


@lru_cache(maxsize=None)
def load(fid: int) -> dict | None:
    p = _path(fid)
    if not p.exists():
        return None
    body = json.loads(gzip.open(p).read())
    body["gp"] = {int(k): v for k, v in body["gp"].items()}
    body["decisions"] = {int(k): v for k, v in body["decisions"].items()}
    return body


def _good(body: dict, s: int) -> bool:
    return body["decisions"].get(s, 0) == body["gp"][s]


def franchise_from(fid: int) -> int | None:
    """The first season of the franchise's unbroken record book: every season since adds up and was played (the NHL
    files the 1979-96 Jets under today's Jets, who began as Atlanta in 1999-2000); None without a file."""
    body = load(fid)
    if not body or not body["gp"]:
        return None
    start = CURRENT_SEASON
    while True:
        prev = start - 2 if start - 1 == LOCKOUT else start - 1
        if prev in body["gp"] and _good(body, prev):
            start = prev
        elif start == CURRENT_SEASON and prev == CURRENT_SEASON - 1:  # not played yet (a new club)
            return CURRENT_SEASON
        else:
            return start


def first_season(fid: int) -> int | None:
    body = load(fid)
    return min(body["gp"], default=None) if body else None


@lru_cache(maxsize=1)
def league_from() -> int | None:
    """The first season since which every franchise's every season adds up, so a league-wide "longest since" holds;
    None unless every franchise is on file."""
    p = DIR / "franchises.json"
    if not p.exists():
        return None
    bodies = [load(f) for f in json.loads(p.read_text())]
    if not all(bodies):
        return None
    bad = [s for b in bodies for s in b["gp"] if not _good(b, s)]
    return max(bad) + 1 if bad else min(s for b in bodies for s in b["gp"])
