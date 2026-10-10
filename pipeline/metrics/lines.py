"""Forward lines and defense pairs actually used in a game, from 5-on-5 stints."""
from collections import defaultdict

import polars as pl

from pipeline.config import TABLES

UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
ATTEMPTS = UNBLOCKED | {"blocked-shot"}


def _blank():
    return {"sec": 0, "cf": 0, "ca": 0, "xgf": 0.0, "xga": 0.0, "gf": 0, "ga": 0}


def game_units(season: int, game_id: int) -> dict:
    """Per team: every forward trio and defense pair with 5v5 ice time and on-ice shot results."""
    d = TABLES / str(season)
    players = pl.read_parquet(d / "players.parquet").filter(pl.col("game_id") == game_id)
    pos = dict(zip(players["player_id"], players["pos"]))
    stints = pl.read_parquet(d / "stints.parquet").filter(pl.col("game_id") == game_id)
    events = pl.read_parquet(d / "events.parquet").filter((pl.col("game_id") == game_id) & pl.col("type").is_in(list(ATTEMPTS)))
    # Our own expected goals, like the rest of the site (MoneyPuck's per-shot file is only a cross-check and is not kept).
    xg = pl.read_parquet(d / "shots_xg_own.parquet").filter(pl.col("game_id") == game_id).select("event_id", pl.col("xg").alias("xGoal"))
    events = events.join(xg, on="event_id", how="left")

    def split(ids):
        fwd = tuple(sorted(i for i in ids if pos.get(i) in ("C", "L", "R")))
        dmen = tuple(sorted(i for i in ids if pos.get(i) == "D"))
        return (fwd, dmen) if len(fwd) == 3 and len(dmen) == 2 else (None, None)

    units = {True: defaultdict(_blank), False: defaultdict(_blank)}
    team_sec = 0
    for s in stints.iter_rows(named=True):
        if not (s["n_home"] == 5 and s["n_away"] == 5 and s["home_goalie"] and s["away_goalie"]):
            continue
        team_sec += s["duration"]
        for home, ids in ((True, s["home_skaters"]), (False, s["away_skaters"])):
            for key in split(ids):
                if key:
                    units[home][key]["sec"] += s["duration"]
    for e in events.iter_rows(named=True):
        if not (len(e["home_on"]) == 5 and len(e["away_on"]) == 5 and e["home_goalie"] and e["away_goalie"]) or e["is_home"] is None:
            continue
        for home, ids in ((True, e["home_on"]), (False, e["away_on"])):
            mine = e["is_home"] == home
            for key in split(ids):
                if not key:
                    continue
                u = units[home][key]
                u["cf" if mine else "ca"] += 1
                if e["type"] in UNBLOCKED:
                    u["xgf" if mine else "xga"] += e["xGoal"] or 0.0
                if e["type"] == "goal":
                    u["gf" if mine else "ga"] += 1
    return {"five_on_five_seconds": team_sec, "home": dict(units[True]), "away": dict(units[False])}
