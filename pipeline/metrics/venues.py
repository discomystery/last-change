"""A goalie's record on one club's ice, for preview storylines ("hasn't won in Toronto in his last six starts there").

Starts come from the NHL career game logs (ingest/careers.py) for seasons before 2023-24 and from the saved boxscores
(the "starter" flag and decision) from 2023-24 on. Regular season only. A place is the home club's abbreviation at
the time, so games in Arizona never count as games in Utah. Only goalies whose career history is complete are used,
so "never won" is never said from half a career.

These are storylines, not predictions: a goalie's record in one rink tells us little about the next game there.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, TABLES, types_for
from pipeline.ingest import careers

OWN_FROM = careers.OWN_FROM
NEVER_WON = 4  # starts on that ice without a win before "has never won there"
WINLESS = 5    # straight starts there without a win before "hasn't won there in his last ..."
GOOD = (6, 0.75)  # starts and win share before "is 7-1-1 there"


def _box_starts(season: int) -> list[dict]:
    """Every start in our saved boxscores for one season (regular season; playoffs too in a playoff build)."""
    d = TABLES / str(season) / "games.parquet"
    if not d.exists():
        return []
    games = pl.read_parquet(d).filter(pl.col("game_type").is_in(types_for(season)) & pl.col("state").is_in(["OFF", "FINAL"]))
    out = []
    for g in games.iter_rows(named=True):
        p = RAW / str(season) / "box" / f"{g['game_id']}.json.gz"
        if not p.exists():
            continue
        box = json.loads(gzip.open(p).read()).get("playerByGameStats", {})
        for side, own, opp in (("homeTeam", g["home"], g["away"]), ("awayTeam", g["away"], g["home"])):
            for x in box.get(side, {}).get("goalies", []):
                if x.get("starter"):
                    out.append({"player_id": x["playerId"], "game_id": g["game_id"], "date": g["date"], "team": own, "opp": opp,
                                "home": side == "homeTeam", "decision": x.get("decision")})
    return out


@lru_cache(maxsize=1)
def starts() -> pl.DataFrame:
    """One row per goalie start: player_id, game_id, date, team, opp, home, decision (W, L or O), rink (the home
    club's abbreviation). Pre-2023 seasons come from complete career files only."""
    rows = []
    if careers.DIR.exists():
        for f in careers.DIR.glob("*.json.gz"):
            c = json.loads(gzip.open(f).read())
            if not c.get("complete") or c.get("version", 1) < careers.VERSION or (c.get("landing") or {}).get("position") != "G":
                continue
            pid = int(f.name.split(".")[0])
            for s, log in c["logs"].items():
                if int(str(s)[:4]) >= OWN_FROM:
                    continue
                for x in log:
                    if x.get("gamesStarted"):
                        home = x.get("homeRoadFlag") == "H"
                        rows.append({"player_id": pid, "game_id": x["gameId"], "date": x["gameDate"], "team": x["teamAbbrev"],
                                     "opp": x["opponentAbbrev"], "home": home, "decision": x.get("decision")})
    for s in range(OWN_FROM, CURRENT_SEASON + 1):
        rows += _box_starts(s)
    df = pl.DataFrame(rows, schema={"player_id": pl.Int64, "game_id": pl.Int64, "date": pl.Utf8, "team": pl.Utf8, "opp": pl.Utf8,
                                    "home": pl.Boolean, "decision": pl.Utf8})
    df = df.with_columns(rink=pl.when(pl.col("home")).then(pl.col("team")).otherwise(pl.col("opp")))
    return df.unique(["player_id", "game_id"]).sort("date", "game_id")


def record(pid: int, rink: str, before: str) -> dict | None:
    """His starts on `rink`'s ice before the date `before`, if his whole career is known."""
    s = starts().filter((pl.col("player_id") == pid) & (pl.col("date") < before[:10]))
    if s.is_empty():
        return None
    if pid not in known():
        return None
    here = s.filter(pl.col("rink") == rink)
    dec = here["decision"].to_list()
    w, l, o = dec.count("W"), dec.count("L"), sum(1 for d in dec if d not in ("W", "L") and d)
    since = 0
    for d in reversed(dec):
        if d == "W":
            break
        since += 1
    last_win = here.filter(pl.col("decision") == "W")["date"].max() if w else None
    return {"starts": len(dec), "w": w, "l": l, "o": o, "winless": since, "last_win": last_win}


@lru_cache(maxsize=1)
def known() -> set[int]:
    """Goalies whose whole NHL career we hold: a complete career file (it covers every season before 2023-24, even
    one with no starts)."""
    out = set()
    if careers.DIR.exists():
        for f in careers.DIR.glob("*.json.gz"):
            c = json.loads(gzip.open(f).read())
            if c.get("complete") and c.get("version", 1) >= careers.VERSION:
                out.add(int(f.name.split(".")[0]))
    return out


def storyline(pid: int, name: str, rink: str, where: str, before: str) -> dict | None:
    """One storyline about `name` on `rink`'s ice, or None. `where` reads after a verb: "in Toronto"."""
    r = record(pid, rink, before)
    if not r or r["starts"] == 0:
        return None
    rec = f"{r['w']}-{r['l']}-{r['o']}"
    n = r["starts"]
    if r["w"] == 0 and n >= NEVER_WON:
        return {"text": f"{name} has never won {where}: he is {rec} there in his career.", "score": 64 + n}
    if r["winless"] >= WINLESS:
        year = int(r["last_win"][:4]) if r["last_win"] else None
        tail = f", and his last win there was in {year}" if year else ""
        return {"text": f"{name} hasn’t won {where} in his last {r['winless']} starts there{tail}.", "score": 60 + r["winless"]}
    if n >= GOOD[0] and r["w"] / n >= GOOD[1]:
        return {"text": f"{name} is {rec} {where} in his career.", "score": 58 + n / 2}
    return None
