"""Per-game building blocks for team metrics.

One row per (game, team, strength, score state): seconds played plus shot counts for and against.
Everything else (rates, score adjustment, percentiles, blending seasons) is computed from this table.
"""
import math
from collections import defaultdict

import polars as pl

from pipeline.build.stints import strength
from pipeline.config import TABLES

UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
ATTEMPTS = UNBLOCKED | {"blocked-shot"}
TURNOVER_WINDOW = 5  # seconds from a turnover to a shot for it to count as off-turnover
POINT_DISTANCE = 50  # feet
COUNTS = ["cf", "ff", "sf", "gf", "xgf", "rush_xgf", "reb_xgf", "to_xgf", "point_cf", "hits", "gives", "takes", "blocks"]


def _state(diff: int) -> int:
    return max(-3, min(3, diff))


def build(season: int) -> dict:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet")
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    pos = {(r["game_id"], r["player_id"]): r["pos"] for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    xg_cols = ["event_id", "game_id", "xGoal", "shotRush", "shotRebound"]
    events = pl.read_parquet(d / "events.parquet").join(pl.read_parquet(d / "shots_xg.parquet").select(xg_cols), on=["game_id", "event_id"], how="left").sort("game_id", "sort")
    stints = pl.read_parquet(d / "stints.parquet")

    rows: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))

    # Goal times per game, so each stint knows the score when it started.
    goals = defaultdict(list)
    for e in events.filter(pl.col("type") == "goal").iter_rows(named=True):
        goals[e["game_id"]].append((e["sec"], e["is_home"]))
    for s in stints.iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids:
            continue
        diff = sum((1 if home else -1) for sec, home in goals[gid] if sec <= s["start"] and home is not None)
        hg, ag = s["home_goalie"] is not None, s["away_goalie"] is not None
        for home in (True, False):
            own, opp = (s["n_home"], s["n_away"]) if home else (s["n_away"], s["n_home"])
            key = (gid, ids[gid][0 if home else 1], home, strength(own, opp, hg if home else ag, ag if home else hg), _state(diff if home else -diff))
            rows[key]["sec"] += s["duration"]

    last_turnover: dict[bool, int] = {}
    current = None
    for e in events.iter_rows(named=True):
        gid = e["game_id"]
        if gid != current:
            current, last_turnover = gid, {}
        typ, home = e["type"], e["is_home"]
        if typ in ("stoppage", "faceoff", "period-start", "period-end"):
            last_turnover = {}
            continue
        if home is None or gid not in ids:
            continue
        if typ == "giveaway":
            last_turnover[not home] = e["sec"]  # the other team now has the puck
        elif typ == "takeaway":
            last_turnover[home] = e["sec"]
        hg, ag = e["home_goalie"] is not None, e["away_goalie"] is not None
        nh, na = len(e["home_on"]), len(e["away_on"])
        diff = e["home_score"] - e["away_score"]

        def bump(for_home: bool, col: str, by: float = 1.0):
            own, opp = (nh, na) if for_home else (na, nh)
            key = (gid, ids[gid][0 if for_home else 1], for_home, strength(own, opp, hg if for_home else ag, ag if for_home else hg), _state(diff if for_home else -diff))
            rows[key][col] += by

        if typ in ATTEMPTS:
            xg = e["xGoal"] or 0.0
            far = e["x_norm"] is not None and math.hypot(89 - e["x_norm"], e["y_norm"]) > POINT_DISTANCE
            point = pos.get((gid, e["p1"])) == "D" or far
            off_turnover = home in last_turnover and e["sec"] - last_turnover[home] <= TURNOVER_WINDOW
            for side, sfx in ((home, "f"), (not home, "a")):
                bump(side, "c" + sfx)
                if point:
                    bump(side, "point_c" + sfx)
                if typ in UNBLOCKED:
                    bump(side, "f" + sfx)
                    bump(side, "xg" + sfx, xg)
                    if e["shotRush"]:
                        bump(side, "rush_xg" + sfx, xg)
                    if e["shotRebound"]:
                        bump(side, "reb_xg" + sfx, xg)
                    if off_turnover:
                        bump(side, "to_xg" + sfx, xg)
                if typ in ("shot-on-goal", "goal"):
                    bump(side, "s" + sfx)
                if typ == "goal":
                    bump(side, "g" + sfx)
            if typ == "blocked-shot":
                bump(not home, "blocks")
        elif typ == "hit":
            bump(home, "hits")
        elif typ == "giveaway":
            bump(home, "gives")
        elif typ == "takeaway":
            bump(home, "takes")

    cols = ["sec", "cf", "ca", "ff", "fa", "sf", "sa", "gf", "ga", "xgf", "xga", "rush_xgf", "rush_xga", "reb_xgf", "reb_xga",
            "to_xgf", "to_xga", "point_cf", "point_ca", "hits", "gives", "takes", "blocks"]
    table = pl.DataFrame(
        [{"game_id": k[0], "team_id": k[1], "is_home": k[2], "strength": k[3], "score_state": k[4], **{c: float(v.get(c, 0.0)) for c in cols}} for k, v in rows.items()]
    )
    table.write_parquet(d / "team_game.parquet")
    return {"season": season, "rows": table.height}
