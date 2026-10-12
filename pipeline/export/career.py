"""Career history for player pages: every NHL regular season, club by club, with the basic box score.

Skaters: games, goals, assists, points. Goalies: games, wins, losses, overtime losses, save % and goals-against average.
A player traded during a season gets a row per club under that season's total. Seasons from 2023-24 on come from our own
tables (skaters) and saved boxscores (goalies), so they match the rest of the site and this season is current; earlier
seasons come from the NHL's career totals saved by `ingest/careers.py`. Before his first NHL season, one line names the
club he came from ("Regina Pats, WHL").

Career highs are marked (goals and points; wins and save % for goalies), as plain facts. Trend notes were tested and
left out: over 2,000 player-seasons, three straight seasons of rising points a game were followed by a fall more often
than an average season (37% rose again, against 48%), a breakout season kept about half its jump the next year, and a
new team changed a player's points a game no more than staying put did. A "trending up" note would mostly mislead.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, TABLES
from pipeline.ingest import careers
from pipeline.ingest.history import DIR as HISTORY

OWN_FROM = careers.OWN_FROM
GOALIE_SV_GP = 20  # games in a season before his save % can be his career high


@lru_cache(maxsize=1)
def _team_codes() -> dict[str, str]:
    """Full club name, as the NHL's career totals spell it, to its abbreviation (old clubs included: ATL, PHX...)."""
    p = HISTORY / "teams.json"
    return {t["fullName"]: t["triCode"] for t in json.loads(p.read_text())} if p.exists() else {}


@lru_cache(maxsize=1)
def _team_names() -> dict[str, str]:
    p = HISTORY / "teams.json"
    out: dict[str, str] = {}
    if p.exists():
        for t in sorted(json.loads(p.read_text()), key=lambda t: t["id"]):  # the newest club keeps a shared code (UTA)
            out[t["triCode"]] = t["fullName"]
    return out


def _name(v) -> str | None:
    return v.get("default") if isinstance(v, dict) else v


def _sec(toi: str | None) -> int:
    if not toi or ":" not in toi:
        return 0
    m, s = toi.split(":")
    return int(m) * 60 + int(s)


def _skater_own(season: int) -> dict[int, list[dict]]:
    """Per player, one row per club in one season from player_game, in the order he played for them."""
    d = TABLES / str(season)
    if not (d / "player_game.parquet").exists():
        return {}
    g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date", "home_id", "away_id", "home", "away")
    pg = pl.read_parquet(d / "player_game.parquet").select("game_id", "player_id", "team_id", "g", "a1", "a2").join(g, on="game_id")
    pg = pg.with_columns(team=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("home")).otherwise(pl.col("away")))
    agg = pg.group_by("player_id", "team").agg(pl.len().alias("gp"), pl.col("g").sum(), (pl.col("a1") + pl.col("a2")).sum().alias("a"),
                                                pl.col("date").min().alias("first")).sort("first")
    out: dict[int, list[dict]] = {}
    for r in agg.iter_rows(named=True):
        out.setdefault(r["player_id"], []).append({"team": r["team"], "gp": r["gp"], "g": int(r["g"]), "a": int(r["a"]), "p": int(r["g"] + r["a"])})
    return out


def _goalie_own(season: int) -> dict[int, list[dict]]:
    """Per goalie, one row per club in one season from the saved boxscores: games he played in, decisions, shots and
    goals against and time in net."""
    d = TABLES / str(season) / "games.parquet"
    if not d.exists():
        return {}
    games = pl.read_parquet(d).filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"])).sort("date", "game_id")
    acc: dict[tuple[int, str], dict] = {}
    for gm in games.iter_rows(named=True):
        p = RAW / str(season) / "box" / f"{gm['game_id']}.json.gz"
        if not p.exists():
            continue
        box = json.loads(gzip.open(p).read()).get("playerByGameStats", {})
        for side, team in (("homeTeam", gm["home"]), ("awayTeam", gm["away"])):
            for x in box.get(side, {}).get("goalies", []):
                sec = _sec(x.get("toi"))
                if sec <= 0:
                    continue
                r = acc.setdefault((x["playerId"], team), {"team": team, "gp": 0, "w": 0, "l": 0, "ot": 0, "sa": 0, "ga": 0, "sec": 0})
                r["gp"] += 1
                dec = x.get("decision")
                r["w"] += dec == "W"
                r["l"] += dec == "L"
                r["ot"] += dec == "O"
                r["sa"] += x.get("shotsAgainst") or 0
                r["ga"] += x.get("goalsAgainst") or 0
                r["sec"] += sec
    out: dict[int, list[dict]] = {}
    for (pid, _), r in acc.items():  # insertion order is the order he first played for each club
        out.setdefault(pid, []).append(r)
    return out


@lru_cache(maxsize=1)
def _own() -> dict[str, dict[int, dict[int, list[dict]]]]:
    return {"skater": {s: _skater_own(s) for s in range(OWN_FROM, CURRENT_SEASON + 1)},
            "goalie": {s: _goalie_own(s) for s in range(OWN_FROM, CURRENT_SEASON + 1)}}


def _from_landing(row: dict, goalie: bool) -> dict:
    full = _name(row.get("teamName"))
    team = _team_codes().get(full or "", full or "")
    gp = row.get("gamesPlayed") or 0
    if not goalie:
        g, a = row.get("goals") or 0, row.get("assists") or 0
        return {"team": team, "gp": gp, "g": g, "a": a, "p": row.get("points") if row.get("points") is not None else g + a}
    out = {"team": team, "gp": gp, "w": row.get("wins"), "l": row.get("losses"), "ot": (row.get("otLosses") or 0) + (row.get("ties") or 0) if row.get("wins") is not None else None,
           "sa": row.get("shotsAgainst"), "ga": row.get("goalsAgainst"), "sec": _sec(row.get("timeOnIce")) or None}
    # Older saves (before the career table) hold only games and points; the newer fields fill in on the next update.
    if out["sa"] is None and row.get("savePctg") is not None:
        out["sv"] = row["savePctg"]
    if out["sec"] is None and row.get("goalsAgainstAvg") is not None:
        out["gaa"] = row["goalsAgainstAvg"]
    return out


def _rates(r: dict) -> dict:
    """Save % and goals-against average from the counts, where we have them."""
    out = {k: v for k, v in r.items() if k not in ("sa", "ga", "sec")}
    if r.get("sa"):
        out["sv"] = round(1 - r["ga"] / r["sa"], 3)
    elif r.get("sv") is not None:
        out["sv"] = round(r["sv"], 3)
    if r.get("sec"):
        out["gaa"] = round(r["ga"] * 3600 / r["sec"], 2)
    elif r.get("gaa") is not None:
        out["gaa"] = round(r["gaa"], 2)
    return out


def _total(parts: list[dict], goalie: bool) -> dict:
    keys = ("gp", "w", "l", "ot", "sa", "ga", "sec") if goalie else ("gp", "g", "a", "p")
    out = {}
    for k in keys:
        vals = [p.get(k) for p in parts]
        out[k] = None if any(v is None for v in vals) else sum(vals)
    if goalie and (out["sa"] is None or out["sec"] is None) and len(parts) == 1:  # one old-style row: keep its own rates
        out |= {k: parts[0][k] for k in ("sv", "gaa") if parts[0].get(k) is not None}
    return out


def _before_nhl(landing: dict, debut: int | None) -> dict | None:
    """The club he played the most for in his last season before the NHL, and how long he was there: "Regina Pats, WHL,
    2020-21 to 2022-23". For a player with no NHL games yet, his latest club."""
    rows = [r for r in landing.get("otherTotals", []) if debut is None or r["season"] < debut]
    rows = [r for r in rows if r["season"] // 10000 < CURRENT_SEASON]
    if not rows:
        return None
    last = max(r["season"] for r in rows)
    top = max((r for r in rows if r["season"] == last), key=lambda r: r.get("gamesPlayed") or 0)
    team, league = _name(top.get("teamName")), top.get("leagueAbbrev")
    first = last
    while any(r["season"] == first - 10001 and _name(r.get("teamName")) == team for r in rows):
        first -= 10001
    return {"team": team, "league": league, "from": first // 10000, "to": last // 10000}


def build(pid: int, goalie: bool) -> dict | None:
    """His career table, or None when we know of no NHL season and no earlier club."""
    kind = "goalie" if goalie else "skater"
    c = careers.read(pid) or {}
    landing = c.get("landing") or {}
    by: dict[int, list[dict]] = {}
    for row in landing.get("seasonTotals", []):
        if row.get("leagueAbbrev", "NHL") != "NHL" or row.get("gameTypeId") != REGULAR or row["season"] // 10000 >= OWN_FROM:
            continue
        by.setdefault(row["season"] // 10000, []).append((row.get("sequence") or 0, _from_landing(row, goalie)))
    seasons: dict[int, list[dict]] = {s: [r for _, r in sorted(rows, key=lambda x: x[0])] for s, rows in by.items()}
    for s, per in _own()[kind].items():
        if pid in per:
            seasons[s] = [dict(r) for r in per[pid]]
    names = _team_names()
    out_rows = []
    for s in sorted(seasons):
        parts = seasons[s]
        tot = _total(parts, goalie)
        row = {"s": s, **(_rates(tot) if goalie else tot)}
        if len(parts) == 1:
            row["team"] = parts[0]["team"]
        else:
            row["teams"] = [{**(_rates(p) if goalie else p)} for p in parts]
        out_rows.append(row)
    for row in [row for row in out_rows if "team" in row] + [t for row in out_rows for t in row.get("teams", [])]:
        row["name"] = names.get(row["team"], row["team"])
    before = _before_nhl(landing, min(seasons) * 10000 + min(seasons) + 1 if seasons else None) if landing else None
    if not out_rows and not before:
        return None
    out: dict = {"seasons": out_rows, "before": before, "draft": _draft(landing) if landing else None}
    if not landing:  # no career file yet: his seasons before 2023-24 (if any) arrive with a later update, and the page says so
        out["pending"] = True
    if out_rows:
        car = _total([p for s in seasons for p in seasons[s]], goalie)
        out["career"] = _rates(car) if goalie else car
        out["highs"] = _highs(out_rows, goalie)
    return out


def _draft(landing: dict) -> dict:
    """Where he was drafted ({year, team, name, round, pick}), or {"undrafted": True} when the NHL lists no draft."""
    d = landing.get("draftDetails") or {}
    if not d.get("year") or not d.get("overallPick"):
        return {"undrafted": True}
    team = d.get("teamAbbrev")
    return {"year": d["year"], "team": team, "name": _team_names().get(team, team), "round": d.get("round"), "pick": d["overallPick"]}


def _highs(rows: list[dict], goalie: bool) -> dict[str, list[int]]:
    """Seasons holding a career high, per stat, once he has two or more NHL seasons. Ties all count."""
    if len(rows) < 2:
        return {}
    out = {}
    for k in (("w", "sv") if goalie else ("g", "p")):
        pool = [r for r in rows if r.get(k) is not None and (k != "sv" or (r.get("gp") or 0) >= GOALIE_SV_GP)]
        if len(pool) < 2:
            continue
        best = max(r[k] for r in pool)
        if best > 0:
            out[k] = [r["s"] for r in pool if r[k] == best]
    return out
