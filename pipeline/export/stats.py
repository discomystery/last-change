"""Plain counting stats, as the league records them: every skater's, goalie's and team's season line for this season
and last (ingest/official_stats.py). Player pages show a player's lines, the Leaders page lists everyone sortable by any
column, and team pages show their line with the league rank of each figure.

Written to site/public/data/stats/{skaters,goalies,teams}.json as column lists plus one row per player (or club) per
season, to keep the files small. Names are our own spellings (accents, ingest/names.py) where we have the player.
"""
import json
from datetime import datetime, timezone

import polars as pl

from pipeline.config import SITE_DATA, TABLES
from pipeline.ingest import official_stats

OUT = SITE_DATA / "stats"

SKATER = ["id", "name", "team", "pos", "gp", "g", "a", "p", "pm", "pim", "ppg", "ppp", "shg", "shp", "evg", "evp", "gwg", "otg",
          "s", "spct", "toi", "pptoi", "shtoi", "fow", "fol", "fopct", "hit", "blk", "tk", "gv"]
GOALIE = ["id", "name", "team", "gp", "gs", "w", "l", "otl", "sa", "sv", "ga", "svpct", "gaa", "so", "toi"]
TEAM = ["team", "gp", "w", "l", "otl", "pts", "ptspct", "rw", "gf", "ga", "gfpg", "gapg", "diff", "ppg", "ppo", "pppct", "ppga",
        "tsh", "pkpct", "shgf", "shga", "sfpg", "sapg", "fopct", "hit", "blk", "tk", "gv"]


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _names(seasons: list[int]) -> dict[int, str]:
    out = {}
    for s in seasons:
        p = TABLES / str(s) / "players.parquet"
        if p.exists():
            t = pl.read_parquet(p, columns=["player_id", "first", "last"]).unique("player_id", keep="last")
            out |= {r["player_id"]: f'{r["first"]} {r["last"]}' for r in t.iter_rows(named=True)}  # later seasons win
    return out


def _team_abbr(seasons: list[int]) -> dict[int, str]:
    out = {}
    for s in seasons:
        p = TABLES / str(s) / "games.parquet"
        if p.exists():
            g = pl.read_parquet(p, columns=["home_id", "home", "away_id", "away"])
            out |= {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
    return out


def _teams(abbrevs: str | None) -> str | None:
    return abbrevs.replace(",", ", ") if abbrevs else None


def skaters(season: int, names: dict[int, str]) -> list[list]:
    by = lambda report: {r["playerId"]: r for r in official_stats.read(season, report)}
    real, ice, fo = by("skater/realtime"), by("skater/timeonice"), by("skater/faceoffwins")
    rows = []
    for r in official_stats.read(season, "skater/summary"):
        pid = r["playerId"]
        rt, ti, f = real.get(pid, {}), ice.get(pid, {}), fo.get(pid, {})
        fow, fol = f.get("totalFaceoffWins") or 0, f.get("totalFaceoffLosses") or 0
        rec = {"id": pid, "name": names.get(pid) or r["skaterFullName"], "team": _teams(r.get("teamAbbrevs")), "pos": r["positionCode"],
               "gp": r["gamesPlayed"], "g": r["goals"], "a": r["assists"], "p": r["points"], "pm": r["plusMinus"], "pim": r["penaltyMinutes"],
               "ppg": r["ppGoals"], "ppp": r["ppPoints"], "shg": r["shGoals"], "shp": r["shPoints"], "evg": r["evGoals"], "evp": r["evPoints"],
               "gwg": r["gameWinningGoals"], "otg": r["otGoals"], "s": r["shots"], "spct": _r(r.get("shootingPct")),
               "toi": round(r["timeOnIcePerGame"] or 0), "pptoi": round(ti.get("ppTimeOnIcePerGame") or 0), "shtoi": round(ti.get("shTimeOnIcePerGame") or 0),
               "fow": fow, "fol": fol, "fopct": _r(fow / (fow + fol)) if fow + fol else None,
               "hit": rt.get("hits"), "blk": rt.get("blockedShots"), "tk": rt.get("takeaways"), "gv": rt.get("giveaways")}
        rows.append([rec[k] for k in SKATER])
    return rows


def goalies(season: int, names: dict[int, str]) -> list[list]:
    rows = []
    for r in official_stats.read(season, "goalie/summary"):
        pid = r["playerId"]
        rec = {"id": pid, "name": names.get(pid) or r["goalieFullName"], "team": _teams(r.get("teamAbbrevs")), "gp": r["gamesPlayed"],
               "gs": r["gamesStarted"], "w": r["wins"], "l": r["losses"], "otl": r["otLosses"], "sa": r["shotsAgainst"], "sv": r["saves"],
               "ga": r["goalsAgainst"], "svpct": _r(r.get("savePct")), "gaa": _r(r.get("goalsAgainstAverage"), 2), "so": r["shutouts"],
               "toi": round(r["timeOnIce"] or 0)}
        rows.append([rec[k] for k in GOALIE])
    return rows


def teams(season: int, abbr: dict[int, str]) -> list[list]:
    by = lambda report: {r["teamId"]: r for r in official_stats.read(season, report)}
    pp, pk, real = by("team/powerplay"), by("team/penaltykill"), by("team/realtime")
    rows = []
    for r in official_stats.read(season, "team/summary"):
        tid = r["teamId"]
        if tid not in abbr:
            continue
        a, b, c = pp.get(tid, {}), pk.get(tid, {}), real.get(tid, {})
        rec = {"team": abbr[tid], "gp": r["gamesPlayed"], "w": r["wins"], "l": r["losses"], "otl": r["otLosses"], "pts": r["points"],
               "ptspct": _r(r.get("pointPct")), "rw": r.get("winsInRegulation"), "gf": r["goalsFor"], "ga": r["goalsAgainst"],
               "gfpg": _r(r.get("goalsForPerGame"), 2), "gapg": _r(r.get("goalsAgainstPerGame"), 2), "diff": r["goalsFor"] - r["goalsAgainst"],
               "ppg": a.get("powerPlayGoalsFor"), "ppo": a.get("ppOpportunities"), "pppct": _r(r.get("powerPlayPct")),
               "ppga": b.get("ppGoalsAgainst"), "tsh": b.get("timesShorthanded"), "pkpct": _r(r.get("penaltyKillPct")),
               "shgf": b.get("shGoalsFor"), "shga": a.get("shGoalsAgainst"), "sfpg": _r(r.get("shotsForPerGame"), 1),
               "sapg": _r(r.get("shotsAgainstPerGame"), 1), "fopct": _r(r.get("faceoffWinPct")),
               "hit": c.get("hits"), "blk": c.get("blockedShots"), "tk": c.get("takeaways"), "gv": c.get("giveaways")}
        rows.append([rec[k] for k in TEAM])
    return rows


def run(season: int) -> dict:
    seasons = [season, season - 1]
    names, abbr = _names(sorted(seasons)), _team_abbr(sorted(seasons))
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {}
    for kind, cols, fn, key in (("skaters", SKATER, skaters, names), ("goalies", GOALIE, goalies, names), ("teams", TEAM, teams, abbr)):
        data = {str(s): fn(s, key) for s in seasons}
        data = {s: rows for s, rows in data.items() if rows}
        (OUT / f"{kind}.json").write_text(json.dumps({"generated_at": now, "cols": cols, "seasons": data}, separators=(",", ":"), ensure_ascii=False))
        out[kind] = {s: len(rows) for s, rows in data.items()}
    return out
