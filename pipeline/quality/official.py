"""Our own totals, built from the play-by-play and boxscores, against the NHL's official season reports
(ingest/official_stats.py saves them every update into raw/official/{season}/).

The site's stat tables quote the official reports directly, so they always match NHL.com; this check is about
everything else, which is built from our own tables (stories, records, report cards, ratings). A club is compared
only when its games played agree with the report's (the report and our tables can be a game apart during a night),
and so is a skater.
  teams    wins, regulation/overtime losses, goals for and against (without the goal a shootout winner is credited with, as the report counts them)
  skaters  goals and assists
When a skater's totals differ, the NHL's game-by-game report for him (one request) says which games, and those games
are fetched again on the next update (`nhl.resettle`): the NHL changes a goal's credit days or weeks after a game,
later than our one refetch after it settles. That is the only thing a check changes, and only when online.
"""
import gzip
import json

import polars as pl

from pipeline.config import FINAL_STATES, NHL_STATS, OFFLINE, RAW, REGULAR, TABLES, season_id

MAX_LISTED = 25
MAX_LOOKUPS = 15  # skaters whose games are looked up per run (one request each)


def changed_games(season: int, pid: int, ours: dict[int, tuple[int, int]]) -> list[int]:
    """Games where the NHL's game-by-game goals and assists for `pid` differ from ours ({game: (g, a)})."""
    from pipeline.ingest.client import get
    url = (f"{NHL_STATS}/skater/summary?isAggregate=false&isGame=true"
           f"&cayenneExp=playerId={pid} and seasonId={season_id(season)} and gameTypeId={REGULAR}")
    theirs = {r["gameId"]: (r["goals"], r["assists"]) for r in get(url).json().get("data") or []}
    return sorted(g for g in set(theirs) | set(ours) if theirs.get(g, (0, 0)) != ours.get(g, (0, 0)))


def _report(season: int, name: str) -> list[dict] | None:
    p = RAW / "official" / str(season) / f"{name}.json.gz"
    if not p.exists():
        return None
    j = json.loads(gzip.open(p).read())
    return j.get("data") if isinstance(j, dict) else j


def team_lines(season: int) -> dict:
    """Our record and goals per club from the games table: {abbr: {gp, w, l, otl, gf, ga}}."""
    g = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(
        (pl.col("game_type") == REGULAR) & pl.col("state").is_in(list(FINAL_STATES)))
    out: dict = {}
    for r in g.iter_rows(named=True):
        for us, them, gf, ga in ((r["home"], r["away"], r["home_score"], r["away_score"]),
                                 (r["away"], r["home"], r["away_score"], r["home_score"])):
            t = out.setdefault(us, {"gp": 0, "w": 0, "l": 0, "otl": 0, "gf": 0, "ga": 0})
            t["gp"] += 1
            so = 1 if r["last_period"] == "SO" else 0  # the NHL's reports leave out the shootout's deciding goal
            t["gf"] += gf - (so if gf > ga else 0)
            t["ga"] += ga - (so if ga > gf else 0)
            t["w" if gf > ga else ("l" if r["last_period"] == "REG" else "otl")] += 1
    return out


def run(season: int) -> dict:
    teams_rep, skaters_rep = _report(season, "team_summary"), _report(season, "skater_summary")
    if not teams_rep or not (TABLES / str(season) / "games.parquet").exists():
        return {"level": "skipped", "why": "no official reports or games table saved yet"}
    names = {r["teamId"]: r for r in teams_rep}
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    abbr = dict(zip(g["home_id"], g["home"])) | dict(zip(g["away_id"], g["away"]))
    ours = team_lines(season)
    problems, compared = [], {"teams": 0, "skaters": 0}
    for tid, r in names.items():
        t = ours.get(abbr.get(tid))
        if not t or t["gp"] != r["gamesPlayed"]:
            continue
        compared["teams"] += 1
        theirs = {"w": r["wins"], "l": r["losses"], "otl": r["otLosses"], "gf": r["goalsFor"], "ga": r["goalsAgainst"]}
        diff = {k: (t[k], v) for k, v in theirs.items() if t[k] != v}
        if diff:
            problems.append({"what": f"{abbr[tid]}: our totals differ from the NHL's report",
                             "detail": {k: {"ours": a, "nhl": b} for k, (a, b) in diff.items()}})
    pg = TABLES / str(season) / "player_game.parquet"
    if skaters_rep and pg.exists():
        games = pl.read_parquet(pg).filter((pl.col("game_id") // 10000 % 100) == REGULAR)
        mine = games.group_by("player_id").agg(pl.len().alias("gp"), pl.col("g").sum().cast(int),
                                                (pl.col("a1") + pl.col("a2")).sum().cast(int).alias("a"))
        mine = {r["player_id"]: r for r in mine.iter_rows(named=True)}
        for r in skaters_rep:
            m = mine.get(r["playerId"])
            if not m or m["gp"] != r["gamesPlayed"]:
                continue
            compared["skaters"] += 1
            if (m["g"], m["a"]) != (r["goals"], r["assists"]):
                problems.append({"what": f"{r['skaterFullName']}: our goals and assists differ from the NHL's report",
                                 "detail": {"ours": [m["g"], m["a"]], "nhl": [r["goals"], r["assists"]],
                                            "player": r["playerId"]}})
    refetch = []
    if not OFFLINE:
        from pipeline.ingest import nhl
        for p in [p for p in problems if "player" in p.get("detail", {})][:MAX_LOOKUPS]:
            pid = p["detail"]["player"]
            mine_g = games.filter(pl.col("player_id") == pid)
            ours = {r["game_id"]: (int(r["g"]), int(r["a1"] + r["a2"])) for r in mine_g.iter_rows(named=True)}
            try:
                p["detail"]["games"] = changed_games(season, pid, ours)
            except Exception as e:  # the lookup is a help, not the check
                p["detail"]["games_error"] = repr(e)
                continue
            refetch += p["detail"]["games"]
        refetch = nhl.resettle(season, refetch)
    return {"level": "problem" if problems else "ok", "compared": compared, "count": len(problems),
            "problems": problems[:MAX_LISTED], "refetch": refetch}
