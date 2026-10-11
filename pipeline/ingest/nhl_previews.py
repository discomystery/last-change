"""NHL.com's own game previews, for the starting goalies it projects.

Each preview (nhl.com/news/{away club}-{home club}-game-preview-{month}-{day}-{year}) lists both teams' projected
lineups, goalies last, with the expected starter first. Checked over the first 141 team-games of 2026-27, the first goalie
listed started 128 times (91%), against 69% for our own guess from recent usage (pipeline/metrics/starters.py). The
articles go up on game day, usually 2 to 7 hours before puck drop (most between 16:00 and 19:00 UTC) and are sometimes
edited after, so each run asks again for every game today or tomorrow that hasn't started (a few dozen requests).

Saved per game at raw/nhl_previews/{season}/{game_id}.json.gz: {"fetched", "url", "published", "starters": {team: id}}
("starters" empty when the page isn't up yet). Only the goalie names are read; nothing from the article is published.
"""
from __future__ import annotations

import gzip
import json
import re
import unicodedata
from datetime import date as Date, datetime, timedelta, timezone

import httpx
import polars as pl

from pipeline.config import CURRENT_SEASON, FINAL_STATES, OFFLINE, RAW, REGULAR, SITE_DATA, TABLES
from pipeline.ingest.client import get

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
LD = re.compile(r'<script type="application/ld(?:&#x2B;|\+)json">(.*?)</script>', re.S)
LINEUP = re.compile(r"\*\*[^*\n]*projected lineup\*\*", re.I)
DAYS_AHEAD = 1  # today's and tomorrow's games (by the schedule's date)


def fold(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z]+", " ", s).strip()


def slug(name: str) -> str:
    return fold(name).replace(" ", "-")


def url_for(away_name: str, home_name: str, day: str) -> str:
    d = Date.fromisoformat(day)
    return f"https://www.nhl.com/news/{slug(away_name)}-{slug(home_name)}-game-preview-{MONTHS[d.month - 1]}-{d.day}-{d.year}"


def raw_path(season: int, gid: int):
    return RAW / "nhl_previews" / str(season) / f"{gid}.json.gz"


def read(season: int, gid: int) -> dict | None:
    p = raw_path(season, gid)
    return json.loads(gzip.open(p).read()) if p.exists() else None


def _article(html: str) -> dict:
    for m in LD.finditer(html):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("articleBody") and LINEUP.search(d["articleBody"]):
            return d
    return {}


def goalie_names(body: str) -> list[list[str]]:
    """The lines of each projected lineup (away first, then home) that are a single name, in order: the goalies, plus
    the odd seventh defenseman listed alone. Forward lines and pairs are joined by dashes."""
    out = []
    for part in LINEUP.split(body)[1:3]:
        part = re.split(r"Scratched|Injured|Suspended|Status report", part)[0]
        names = [ln.strip(" *_ ") for ln in part.split("\n")]
        out.append([n for n in names if n and not re.search(r"--|–|—", n)])
    return out


def known_goalies(season: int) -> dict[str, dict[str, int]]:
    """{team: {folded full name: player id}} for every goalie a club has listed or dressed this season and last."""
    out: dict[str, dict[str, int]] = {}
    r = TABLES / str(season) / "rosters.parquet"
    if r.exists():
        for x in pl.read_parquet(r).filter(pl.col("pos") == "G").unique(["team", "player_id"]).iter_rows(named=True):
            out.setdefault(x["team"], {})[fold(f"{x['first']} {x['last']}")] = x["player_id"]
    anyteam: dict[str, int] = {}
    for s in (season - 1, season):
        p = TABLES / str(s) / "players.parquet"
        if p.exists():
            for x in pl.read_parquet(p).filter(pl.col("pos") == "G").unique("player_id").iter_rows(named=True):
                anyteam[fold(f"{x['first']} {x['last']}")] = x["player_id"]
    out["*"] = anyteam
    return out


def starters_from(body: str, away: str, home: str, known: dict[str, dict[str, int]]) -> dict[str, int]:
    """{team: player id} for the first listed name in each lineup that is a goalie we know."""
    out = {}
    for team, names in zip((away, home), goalie_names(body)):
        for n in names:
            pid = known.get(team, {}).get(fold(n)) or known["*"].get(fold(n))
            if pid:
                out[team] = pid
                break
    return out


def update(season: int = CURRENT_SEASON, now: datetime | None = None) -> dict:
    """Read NHL.com's preview for every game today or tomorrow that hasn't started."""
    if OFFLINE:
        return {"skipped": "offline"}
    now = now or datetime.now(timezone.utc)
    games = json.loads(gzip.open(RAW / str(season) / "schedule.json.gz").read())  # refreshed by every online ingest
    names = {t["abbr"]: t["name"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    last = (now.date() + timedelta(days=DAYS_AHEAD)).isoformat()
    known = known_goalies(season)
    asked = found = 0
    for g in games:
        if g["game_type"] != REGULAR:
            continue
        start = datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00"))
        if g["state"] in FINAL_STATES or start <= now or g["date"] > last or g["away"] not in names or g["home"] not in names:
            continue
        g["id"] = g["game_id"]
        old = read(season, g["id"]) or {}
        url = url_for(names[g["away"]], names[g["home"]], g["date"])
        asked += 1
        try:
            art = _article(get(url).text)
        except (httpx.HTTPStatusError, RuntimeError):
            art = {}
        body = {"fetched": now.isoformat(timespec="seconds"), "url": url, "published": art.get("datePublished"),
                "starters": starters_from(art.get("articleBody") or "", g["away"], g["home"], known)}
        if old.get("starters") and not body["starters"]:  # a page that failed to load doesn't undo an earlier find
            continue
        found += len(body["starters"]) == 2
        p = raw_path(season, g["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt") as f:
            json.dump(body, f)
    return {"asked": asked, "found": found}


def projected(season: int, gid: int, before: str | None = None) -> dict[str, int]:
    """NHL.com's projected starters for one game, if its preview was found before `before` (an ISO time)."""
    got = read(season, gid)
    if not got or (before and got.get("fetched", "") >= before):
        return {}
    return {t: int(p) for t, p in (got.get("starters") or {}).items()}
