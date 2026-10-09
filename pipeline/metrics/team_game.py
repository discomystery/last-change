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
RUSH_WINDOW = 4  # seconds from an event outside the offensive zone to a shot for it to count as a rush shot
POINT_DISTANCE = 50  # feet
BREAKDOWN_XG = 0.15  # a rush or off-turnover shot at least this dangerous counts as a breakdown chance
FACEOFF_WINDOW = 5  # seconds from an offensive-zone faceoff win to a goal for it to count as off the draw
COUNTS = ["cf", "ff", "sf", "gf", "xgf", "rush_xgf", "reb_xgf", "to_xgf", "point_cf", "hits", "gives", "takes", "blocks"]


def _state(diff: int) -> int:
    return max(-3, min(3, diff))


def build(season: int) -> dict:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet")
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    pos = {(r["game_id"], r["player_id"]): r["pos"] for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    # Expected goals come from the in-house model (one model for every season). A rebound is an unblocked shot
    # within 3 seconds of a teammate's shot on goal.
    own = pl.read_parquet(d / "shots_xg_own.parquet").rename({"xg": "xGoal"})
    feats = pl.read_parquet(d / "shot_features.parquet").select(
        "game_id", "event_id", ((pl.col("prev_type") == "shot-on-goal") & pl.col("prev_same_team") & (pl.col("prev_gap") <= 3)).alias("shotRebound"))
    events = pl.read_parquet(d / "events.parquet").join(own, on=["game_id", "event_id"], how="left").join(feats, on=["game_id", "event_id"], how="left").sort("game_id", "sort")
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

    goal_rows = []  # one row per goal, tagged with how it came about (goal sources)
    last_turnover: dict[bool, int] = {}
    last_faceoff = None  # (second, won by home team) of the last offensive-zone faceoff win since play stopped
    prev = None  # (second, was a home-team event, x toward that team's attacking end) of the last live-play event
    current = None
    for e in events.iter_rows(named=True):
        gid = e["game_id"]
        if gid != current:
            current, last_turnover, prev, last_faceoff = gid, {}, None, None
        typ, home = e["type"], e["is_home"]
        if typ in ("stoppage", "faceoff", "period-start", "period-end"):
            last_turnover, prev, last_faceoff = {}, None, None
            if typ == "faceoff" and home is not None and (e["x_norm"] or 0) > 25:
                last_faceoff = (e["sec"], home)
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
            # Rush: the previous live-play event was moments ago and outside the shooter's offensive zone.
            # MoneyPuck's own shotRush flag is almost never set (0.06% of shots), so it is not used.
            rush = False
            if prev is not None and e["sec"] - prev[0] <= RUSH_WINDOW:
                prev_x = prev[2] if prev[1] == home else -prev[2]
                rush = prev_x <= 25
            for side, sfx in ((home, "f"), (not home, "a")):
                bump(side, "c" + sfx)
                if point:
                    bump(side, "point_c" + sfx)
                if typ in UNBLOCKED:
                    bump(side, "f" + sfx)
                    bump(side, "xg" + sfx, xg)
                    if rush:
                        bump(side, "rush_xg" + sfx, xg)
                    if e["shotRebound"]:
                        bump(side, "reb_xg" + sfx, xg)
                    if off_turnover:
                        bump(side, "to_xg" + sfx, xg)
                    if (rush or off_turnover) and xg >= BREAKDOWN_XG:
                        bump(side, "bd_" + sfx)
                if typ in ("shot-on-goal", "goal"):
                    bump(side, "s" + sfx)
                if typ == "goal":
                    bump(side, "g" + sfx)
            if typ == "goal":
                goal_rows.append({
                    "game_id": gid, "event_id": e["event_id"], "team_id": ids[gid][0 if home else 1], "is_home": home,
                    "strength": strength(nh if home else na, na if home else nh, hg if home else ag, ag if home else hg),
                    "rebound": bool(e["shotRebound"]), "rush": rush, "off_turnover": off_turnover,
                    "off_faceoff": last_faceoff is not None and last_faceoff[1] == home and e["sec"] - last_faceoff[0] <= FACEOFF_WINDOW,
                    "xg": xg,
                })
            if typ == "blocked-shot":
                bump(not home, "blocks")
        if e["x_norm"] is not None:
            prev = (e["sec"], home, e["x_norm"])
        if typ in ATTEMPTS:
            continue
        if typ == "hit":
            bump(home, "hits")
            if e["zone"] == "O":
                bump(home, "oz_hits")
        elif typ == "giveaway":
            bump(home, "gives")
            if e["zone"] == "D":
                bump(not home, "forced_gives")  # coughed up in their own end: credit the forechecking team
        elif typ == "takeaway":
            bump(home, "takes")
            if e["zone"] == "O":
                bump(home, "oz_takes")

    cols = ["sec", "cf", "ca", "ff", "fa", "sf", "sa", "gf", "ga", "xgf", "xga", "rush_xgf", "rush_xga", "reb_xgf", "reb_xga",
            "to_xgf", "to_xga", "point_cf", "point_ca", "hits", "gives", "takes", "blocks", "bd_f", "bd_a", "oz_hits", "oz_takes", "forced_gives"]
    table = pl.DataFrame(
        [{"game_id": k[0], "team_id": k[1], "is_home": k[2], "strength": k[3], "score_state": k[4], **{c: float(v.get(c, 0.0)) for c in cols}} for k, v in rows.items()]
    )
    table.write_parquet(d / "team_game.parquet")
    pl.DataFrame(goal_rows).write_parquet(d / "goals.parquet")

    # Discipline: count each stretch a team spends shorthanded (from who is actually on the ice), not penalty calls,
    # so offsetting minors and misconducts that change nothing are ignored.
    short = defaultdict(int)
    seconds = defaultdict(int)
    was = {}
    for s in stints.sort("game_id", "start").iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids:
            continue
        hg, ag = s["home_goalie"] is not None, s["away_goalie"] is not None
        for home in (True, False):
            own, opp = (s["n_home"], s["n_away"]) if home else (s["n_away"], s["n_home"])
            down = strength(own, opp, hg if home else ag, ag if home else hg) in ("4v5", "3v5", "3v4")
            key = (gid, ids[gid][0 if home else 1])
            seconds[key] += s["duration"]
            if down and not was.get(key, False):
                short[key] += 1
            was[key] = down
    pl.DataFrame([{"game_id": k[0], "team_id": k[1], "times_short": short.get(k, 0), "game_sec": v} for k, v in seconds.items()]).write_parquet(d / "discipline.parquet")
    return {"season": season, "rows": table.height}
