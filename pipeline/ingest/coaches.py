"""Each club's head coach in every finished game, from the game center's right rail (gameInfo.headCoach).

Previews name the home coach in matchup calls ("Rod Brind'Amour is chasing an easier matchup"), so the coach must be
the one behind the bench before that game, not today's. One request per finished game, never repeated; offline runs
skip fetching and use what is saved.

Table: data/tables/{season}/coaches.parquet with game_id, date, team, coach.

Games not played yet are recorded too (ingest/game_rosters.py reads the same right rail for today's and tomorrow's
games and hands the coaches here), and rewritten until the game is final: the rail names the coach behind the bench
tonight, so the first game after a firing names the new coach (eye test on 2025-26, 2026-10-13: the preview of the Kings'
first game after Jim Hiller was fired still said "Jim Hiller likes to pick matchups").
"""
import httpx
import polars as pl

from pipeline.config import FINAL_STATES, NHL_WEB, OFFLINE, PLAYOFFS, REGULAR, TABLES
from pipeline.ingest.client import get

SCHEMA = {"game_id": pl.Int64, "date": pl.Utf8, "team": pl.Utf8, "coach": pl.Utf8}


def path(season: int):
    return TABLES / str(season) / "coaches.parquet"


def _rows(game: dict, info: dict | None = None) -> list[dict]:
    if info is None:
        info = get(f"{NHL_WEB}/v1/gamecenter/{game['game_id']}/right-rail").json().get("gameInfo") or {}
    out = []
    for side in ("home", "away"):
        name = ((info.get(f"{side}Team") or {}).get("headCoach") or {}).get("default")
        if name:
            out.append({"game_id": game["game_id"], "date": game["date"], "team": game[side], "coach": name})
    return out


def update(season: int) -> dict:
    """Fetch the coaches for finished games not yet recorded."""
    if OFFLINE:
        return {"skipped": "offline"}
    old = pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
    seen = set(old["game_id"].to_list())
    games = (pl.read_parquet(TABLES / str(season) / "games.parquet")
             .filter(pl.col("game_type").is_in([REGULAR, PLAYOFFS]) & pl.col("state").is_in(list(FINAL_STATES)))
             .filter(~pl.col("game_id").is_in(list(seen))))
    rows, missing = [], 0
    for g in games.iter_rows(named=True):
        try:
            rows += _rows(g)
        except (httpx.HTTPError, RuntimeError):
            missing += 1  # one game failing should not stop the nightly run; it is tried again next time
    if rows:
        pl.concat([old, pl.DataFrame(rows, schema=SCHEMA)]).sort("date", "game_id", "team").write_parquet(path(season))
    return {"games": games.height - missing, "missing": missing}


def record(season: int, rows: list[dict]) -> None:
    """Save coaches read from a game's right rail before it is final (ingest/game_rosters.py); replaces the game's rows."""
    if not rows:
        return
    old = pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
    ids = {r["game_id"] for r in rows}
    pl.concat([old.filter(~pl.col("game_id").is_in(list(ids))), pl.DataFrame(rows, schema=SCHEMA)]).sort("date", "game_id", "team").write_parquet(path(season))


def for_game(season: int, game_id: int, team: str, day: str) -> dict | None:
    """The coach behind `team`'s bench in this game (from its own right rail when saved, else his latest finished
    game before `day`), with `games` and `home` (games this season he has coached the club before `day`, all and at
    home) and `first` (his first game with the club this season after someone else had coached it: a change behind
    the bench). None if nothing is saved."""
    if not path(season).exists():
        return None
    t = pl.read_parquet(path(season)).filter(pl.col("team") == team)
    own = t.filter(pl.col("game_id") == game_id)
    before = t.filter(pl.col("date") < day[:10]).sort("date", "game_id")
    name = own["coach"][0] if own.height else (before["coach"][-1] if before.height else None)
    if not name:
        return None
    mine = before.filter(pl.col("coach") == name)
    games = TABLES / str(season) / "games.parquet"
    homes = set()
    if games.exists() and mine.height:
        g = pl.read_parquet(games, columns=["game_id", "home"])
        homes = set(g.filter(pl.col("game_id").is_in(mine["game_id"].to_list()) & (pl.col("home") == team))["game_id"].to_list())
    return {"coach": name, "games": mine.height, "home": len(homes), "first": mine.height == 0 and before.height > 0,
            "after": before["coach"][-1] if before.height and before["coach"][-1] != name else None}


def as_of(season: int, before: str) -> dict[str, str]:
    """Each club's coach in its latest finished game before `before` (an ISO date or time). Empty if nothing is saved."""
    if not path(season).exists():
        return {}
    t = pl.read_parquet(path(season)).filter(pl.col("date") < before[:10]).sort("date", "game_id")
    return {r["team"]: r["coach"] for r in t.iter_rows(named=True)}


def current(season: int) -> dict[str, dict]:
    """Each club's head coach now: the one behind its bench in its latest finished game, with `since` (that coach's
    first game this season) when he took over partway through, so an interim or new coach reads as such. A club that
    has not played yet keeps last season's last coach. Empty if nothing is saved."""
    out: dict[str, dict] = {}
    for s_ in (season - 1, season):
        if not path(s_).exists():
            continue
        t = pl.read_parquet(path(s_)).sort("date", "game_id")
        for team, g in t.group_by("team"):
            team = team[0]
            now = g["coach"][-1]
            since = None
            if s_ == season and g["coach"].n_unique() > 1:  # changed this season: his first game in the job
                changed = g.with_row_index().filter(pl.col("coach") != now)["index"].max()
                since = g["date"][changed + 1]
            out[team] = {"coach": now, "since": since}
    return out
