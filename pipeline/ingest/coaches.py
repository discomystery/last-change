"""Each club's head coach in every finished game, from the game center's right rail (gameInfo.headCoach).

Previews name the home coach in matchup calls ("Rod Brind'Amour is chasing an easier matchup"), so the coach must be
the one behind the bench before that game, not today's. One request per finished game, never repeated; offline runs
skip fetching and use what is saved.

Table: data/tables/{season}/coaches.parquet with game_id, date, team, coach.
"""
import httpx
import polars as pl

from pipeline.config import FINAL_STATES, NHL_WEB, OFFLINE, PLAYOFFS, REGULAR, TABLES
from pipeline.ingest.client import get

SCHEMA = {"game_id": pl.Int64, "date": pl.Utf8, "team": pl.Utf8, "coach": pl.Utf8}


def path(season: int):
    return TABLES / str(season) / "coaches.parquet"


def _rows(game: dict) -> list[dict]:
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


def as_of(season: int, before: str) -> dict[str, str]:
    """Each club's coach in its latest finished game before `before` (an ISO date or time). Empty if nothing is saved."""
    if not path(season).exists():
        return {}
    t = pl.read_parquet(path(season)).filter(pl.col("date") < before[:10]).sort("date", "game_id")
    return {r["team"]: r["coach"] for r in t.iter_rows(named=True)}
