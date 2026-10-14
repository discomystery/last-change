"""Team history back to 1997-98, from the NHL: each season's final standings and playoff bracket.

Our own tables start in 2022-23, so on their own they would say "no playoffs in four seasons" about a team that has
missed for ten. These two small files per season fix that. Finished seasons never change, so each is fetched once
(about 60 requests in all); only the latest spring is refetched until its Final has a winner.

Teams are matched by NHL franchise, so the Winnipeg Jets carry the Atlanta Thrashers' seasons and the Coyotes keep
their Phoenix years. Utah is its own franchise in the NHL's records, so its history starts in 2024-25. History starts
in 1997-98 because the NHL files the 1979-96 Winnipeg Jets under today's Jets franchise, which would be wrong.

Also the last five finished seasons' team summaries (power-play and penalty-kill conversion, goals and shots a game),
which say what a team is known for: the post-game surprises use them so a famously weak power play having another bad
night reads as "bad to worse", not as out of character.

Saved at raw/history/{teams.json, seasons.json, standings/{year}.json, bracket/{year}.json, summary/{year}.json}, where
{year} is the spring the season ended in.
"""
from __future__ import annotations

import json
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, NHL_STATS, NHL_WEB, OFFLINE, RAW
from pipeline.ingest.client import get

DIR = RAW / "history"
FIRST = 1998  # the 1997-98 season
SUMMARY_SEASONS = 5  # finished seasons kept for a team's reputation
SUMMARY_KEYS = ("teamId", "teamFullName", "gamesPlayed", "powerPlayPct", "penaltyKillPct", "goalsForPerGame", "goalsAgainstPerGame",
                "shotsForPerGame", "shotsAgainstPerGame")


def _read(name: str):
    p = DIR / name
    return json.loads(p.read_text()) if p.exists() else None


def _save(name: str, body) -> None:
    p = DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, separators=(",", ":")))


def _done(bracket: dict | None) -> bool:
    """A spring is finished once the Final has a winner."""
    series = (bracket or {}).get("series") or []
    return any(s.get("playoffRound") == 4 and s.get("winningTeamId") for s in series)


def update() -> dict:
    """Fetches whatever is missing. The latest spring is retried until its Final has a winner."""
    if OFFLINE:
        return {"skipped": "offline"}
    used = 0
    if _read("teams.json") is None:
        _save("teams.json", get(f"{NHL_STATS}/team").json()["data"])
        used += 1
    seasons = _read("seasons.json")
    if seasons is None or not any(s["id"] // 10000 == CURRENT_SEASON - 1 for s in seasons):
        seasons = get(f"{NHL_WEB}/v1/standings-season").json()["seasons"]
        _save("seasons.json", seasons)
        used += 1
    ends = {s["id"] % 10000: s["standingsEnd"] for s in seasons}
    for year in range(FIRST, CURRENT_SEASON + 1):
        if year not in ends:  # 2004-05, the lockout
            continue
        if _read(f"standings/{year}.json") is None:
            body = get(f"{NHL_WEB}/v1/standings/{ends[year]}").json()
            _save(f"standings/{year}.json", [{k: r.get(k) for k in ("teamAbbrev", "gamesPlayed", "wins", "losses", "otLosses", "ties", "points")}
                                              for r in body.get("standings", [])])
            used += 1
        if not _done(_read(f"bracket/{year}.json")):
            body = get(f"{NHL_WEB}/v1/playoff-bracket/{year}").json()
            _save(f"bracket/{year}.json", {"series": [{k: s.get(k) for k in ("playoffRound", "winningTeamId", "losingTeamId")}
                                                      | {"teams": [(s.get(t) or {}).get("id") for t in ("topSeedTeam", "bottomSeedTeam")]}
                                                      for s in body.get("series", [])]})
            used += 1
    for year in range(CURRENT_SEASON - SUMMARY_SEASONS + 1, CURRENT_SEASON + 1):
        if year in ends and _read(f"summary/{year}.json") is None:
            body = get(f"{NHL_STATS}/team/summary?cayenneExp=seasonId={(year - 1) * 10000 + year}%20and%20gameTypeId=2").json()
            _save(f"summary/{year}.json", [{k: r.get(k) for k in SUMMARY_KEYS} for r in body.get("data", [])])
            used += 1
    return {"requests": used}


@lru_cache(maxsize=1)
def team_seasons() -> pl.DataFrame:
    """One row per franchise per season it played since 1997-98: year (the spring it ended in), abbrev that season,
    franchise, record and how far it went in the playoffs (0 missed ... 4 lost the Final, 5 won the Cup). Empty when
    nothing has been fetched."""
    teams = _read("teams.json")
    if not teams:
        return pl.DataFrame()
    fr_by_abbr = {t["triCode"]: t["franchiseId"] for t in teams}
    fr_by_id = {t["id"]: t["franchiseId"] for t in teams}
    rows = []
    for year in range(FIRST, CURRENT_SEASON + 1):
        st, br = _read(f"standings/{year}.json"), _read(f"bracket/{year}.json")
        if not st:
            continue
        reached: dict[int, int] = {}
        for s in (br or {}).get("series", []):
            for tid in s["teams"]:
                if tid is not None:
                    reached[fr_by_id.get(tid)] = max(reached.get(fr_by_id.get(tid), 0), s["playoffRound"])
            if s["playoffRound"] == 4 and s.get("winningTeamId"):
                reached[fr_by_id.get(s["winningTeamId"])] = 5
        for r in st:
            abbr = (r.get("teamAbbrev") or {}).get("default")
            fr = fr_by_abbr.get(abbr)
            if fr is None:
                continue
            rows.append({"year": year, "abbrev": abbr, "franchise": fr, "gp": r["gamesPlayed"], "w": r["wins"], "l": r["losses"],
                         "otl": (r.get("otLosses") or 0) + (r.get("ties") or 0), "pts": r["points"], "playoffs": reached.get(fr, 0)})
    return pl.DataFrame(rows)


@lru_cache(maxsize=4)
def summaries(before: int = CURRENT_SEASON + 1) -> pl.DataFrame:
    """Finished regular seasons ending before spring `before` (the last SUMMARY_SEASONS of them): year, abbrev (today's
    abbreviation for the franchise), pp and pk (conversion, 0-1), gf, ga, sf, sa (a game) and each one's league rank
    that season (1 = best: most goals, fewest against, best conversion and kill). Empty when nothing was fetched."""
    teams = _read("teams.json") or []
    fr_by_id = {t["id"]: t["franchiseId"] for t in teams}
    ts = team_seasons()
    now = {} if ts.is_empty() else {r["franchise"]: r["abbrev"] for r in ts.sort("year").iter_rows(named=True)}
    rows = []
    for year in range(before - SUMMARY_SEASONS, before):
        for r in _read(f"summary/{year}.json") or []:
            abbr = now.get(fr_by_id.get(r["teamId"]))
            if abbr and r.get("powerPlayPct") is not None:
                rows.append({"year": year, "abbrev": abbr, "pp": r["powerPlayPct"], "pk": r["penaltyKillPct"], "gf": r["goalsForPerGame"],
                             "ga": r["goalsAgainstPerGame"], "sf": r["shotsForPerGame"], "sa": r["shotsAgainstPerGame"]})
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows)
    for k, best_high in (("pp", True), ("pk", True), ("gf", True), ("ga", False), ("sf", True), ("sa", False)):
        df = df.with_columns(pl.col(k).rank("min", descending=best_high).over("year").cast(pl.Int32).alias(f"{k}_rank"))
    return df.sort("abbrev", "year")


def franchise_history(abbr: str) -> list[dict]:
    """Every season since 1997-98 for the franchise `abbr` plays for today, oldest first, in the shape
    intros.team_history uses: season (the year it started), playoffs (0-5), pts, gp."""
    ts = team_seasons()
    if ts.is_empty():
        return []
    fr = ts.filter(pl.col("abbrev") == abbr)["franchise"]
    if fr.is_empty():
        return []
    rows = ts.filter(pl.col("franchise") == fr[0]).sort("year")
    return [{"season": r["year"] - 1, "playoffs": r["playoffs"], "pts": r["pts"], "gp": r["gp"], "abbrev": r["abbrev"]} for r in rows.iter_rows(named=True)]


if __name__ == "__main__":
    print(json.dumps(update()))
