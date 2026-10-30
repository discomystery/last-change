"""Key players on previews (Sora picked it 2026-10-30, thread "Improve game previews and automate tuning"): for each
club, the three skaters it leans on most who are expected to play, plus one more who is on a hot run, each with his
season line, his last ten games and his Swing lately.

Picking: the hidden relevancy score (metrics/relevancy.py, as of the game’s day): the top three, then any star or franchise player
below them (a star is never left off), then one skater on a hot run, four at most; only skaters who dressed in the club's last game or are in
NHL.com's projected lineup (a player reported out is told in Who to watch). The score itself is never written out.

`form` is "hot" or "cold" only when his last ten games are far from his usual scoring (points a game this season and
last before them, shrunk toward a typical regular): a Poisson tail under 5% either way, with at least three points
expected for "cold". Ten-game runs come and go; the page says so and never turns one into a lean on the game."""
from __future__ import annotations

import json
import math
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, SITE_DATA, TABLES

N = 10
PICK = 3
MAX = 4
TAIL = 0.05
PRIOR_GAMES = 20  # games of a typical regular's pace (0.5 points a game) added to his own
PRIOR_PPG = 0.5
MIN_COLD = 3.0


@lru_cache(maxsize=4)
def _games(season: int) -> pl.DataFrame:
    """One row per skater-game with the club's abbreviation, date, goals and assists."""
    g = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["game_id", "date", "home", "away", "home_id", "away_id", "game_type"])
    g = g.filter(pl.col("game_type") == 2)
    ids = pl.concat([g.select("game_id", "date", pl.col("home").alias("team"), pl.col("home_id").alias("team_id")),
                     g.select("game_id", "date", pl.col("away").alias("team"), pl.col("away_id").alias("team_id"))])
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet", columns=["game_id", "player_id", "team_id", "pos", "g", "a1", "a2"])
    return (pg.filter(pl.col("pos") != "G").join(ids, on=["game_id", "team_id"])
            .with_columns((pl.col("a1") + pl.col("a2")).cast(pl.Int64).alias("a"), pl.col("g").cast(pl.Int64)))


def _tail_hi(k: int, mu: float) -> float:
    return 1.0 - sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(k))


def _tail_lo(k: int, mu: float) -> float:
    return sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(k + 1))


def form(last_points: int, n: int, before_points: int, before_games: int) -> tuple[str | None, float]:
    """("hot" / "cold" / None, his usual points over `n` games)."""
    rate = (before_points + PRIOR_PPG * PRIOR_GAMES) / (before_games + PRIOR_GAMES)
    mu = rate * n
    if _tail_hi(last_points, mu) < TAIL and last_points > mu:
        return "hot", mu
    if mu >= MIN_COLD and _tail_lo(last_points, mu) < TAIL:
        return "cold", mu
    return None, mu


def _swing(game_ids: list[int], pid: int) -> list[float]:
    out = []
    for gid in game_ids:
        p = SITE_DATA / "scores" / f"{gid}.json"
        if not p.exists():
            continue
        for r in _scores(p):
            if r["id"] == pid:
                out.append(r["score"])
    return out


@lru_cache(maxsize=2048)
def _scores_cached(path: str, mtime: float) -> tuple:
    return tuple(json.loads(open(path).read())["players"])


def _scores(p) -> tuple:
    return _scores_cached(str(p), p.stat().st_mtime)


def team(season: int, abbr: str, day: str, expected: set[int] | None = None) -> list[dict]:
    from pipeline.metrics import relevancy
    pg = _games(season).filter(pl.col("date") < day)
    mine = pg.filter(pl.col("team") == abbr)
    if mine.is_empty():
        return []
    club_games = mine.select("game_id", "date").unique().sort("game_id")
    last_gid = club_games["game_id"][-1]
    dressed = set(mine.filter(pl.col("game_id") == last_gid)["player_id"].to_list()) | (expected or set())
    team_id = int(mine["team_id"][-1])
    rel = relevancy.run(season, day).filter((pl.col("team_id") == team_id) & pl.col("player_id").is_in(list(dressed)))
    if rel.is_empty():
        return []
    rel = rel.with_columns(pl.col("tier").is_in(["star", "franchise"]).alias("star")).sort("score", descending=True)
    last_ten = club_games.tail(N)["game_id"].to_list()
    try:
        prev = _games(season - 1)
    except Exception:
        prev = None
    rows = []
    for r in rel.iter_rows(named=True):
        pid = r["player_id"]
        his = pg.filter(pl.col("player_id") == pid).sort("game_id")
        ten = his.filter(pl.col("game_id").is_in(last_ten))
        earlier = his.filter(~pl.col("game_id").is_in(last_ten))
        bp, bg = int((earlier["g"] + earlier["a"]).sum()), earlier.height
        if prev is not None:
            last = prev.filter(pl.col("player_id") == pid)
            bp, bg = bp + int((last["g"] + last["a"]).sum()), bg + last.height
        lp = int((ten["g"] + ten["a"]).sum())
        hot, mu = form(lp, ten.height, bp, bg) if ten.height >= 5 else (None, 0.0)
        sw = _swing(ten["game_id"].to_list(), pid)
        rows.append({"id": pid, "pos": r["pos"], "star": r["star"],
                     "season": {"gp": his.height, "g": int(his["g"].sum()), "a": int(his["a"].sum())},
                     "last": {"gp": ten.height, "g": int(ten["g"].sum()), "a": int(ten["a"].sum()), "usual": round(mu, 1)},
                     "form": hot, "swing": {"n": len(sw), "avg": round(sum(sw) / len(sw), 2)} if sw else None})
    picked = rows[:PICK]
    picked += [x for x in rows[PICK:] if x["star"]][:MAX - len(picked)]  # a star is never left off
    extra = next((x for x in rows[PICK:] if x["form"] == "hot" and x not in picked), None)
    if extra and len(picked) < MAX:
        picked.append(extra)
    # Swing rank among the club's skaters over the same games, for "3rd on the team lately"
    pool = sorted((x["swing"]["avg"] for x in rows if x["swing"] and x["swing"]["n"] >= 5), reverse=True)
    for x in picked:
        x.pop("star")
        if x["swing"] and x["swing"]["n"] >= 5:
            x["swing"]["rank"] = 1 + sum(v > x["swing"]["avg"] for v in pool)
            x["swing"]["of"] = len(pool)
    return picked


def run(away: str, home: str, day: str, expected: set[int] | None = None, season: int = CURRENT_SEASON) -> dict | None:
    out = {t: team(season, t, day[:10], expected) for t in (away, home)}
    return out if all(out.values()) else None
