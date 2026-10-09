"""One row per shot attempt with everything the in-house expected-goals model looks at.

Nothing here identifies the shooter or the goalie: the model is meant to describe the chance, not the players.
"""
import math

import polars as pl

from pipeline.config import TABLES

NET_X = 89.0
ATTEMPTS = {"shot-on-goal", "missed-shot", "goal", "blocked-shot"}
UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
LIVE_RESET = {"stoppage", "period-start", "period-end"}


def build(season: int) -> dict:
    d = TABLES / str(season)
    events = pl.read_parquet(d / "events.parquet").sort("game_id", "sort")
    rows, prev, game = [], None, None
    for e in events.iter_rows(named=True):
        if e["game_id"] != game:
            game, prev = e["game_id"], None
        typ = e["type"]
        if typ in LIVE_RESET:
            prev = None
            continue
        if typ in ATTEMPTS and e["x_norm"] is not None and e["is_home"] is not None:
            home = e["is_home"]
            x, y = float(e["x_norm"]), float(e["y_norm"])
            dist = math.hypot(NET_X - x, y)
            angle = abs(math.degrees(math.atan2(y, NET_X - x)))  # 0 = straight on, 90 = from the goal line, >90 = behind it
            own, opp = (len(e["home_on"]), len(e["away_on"])) if home else (len(e["away_on"]), len(e["home_on"]))
            goalie = e["away_goalie"] if home else e["home_goalie"]
            row = {
                "game_id": e["game_id"], "event_id": e["event_id"], "type": typ, "is_home": home, "goal": typ == "goal",
                "x": x, "y_abs": abs(y), "dist": dist, "angle": angle, "shot_type": e["shot_type"] or "unknown",
                "own_skaters": own, "opp_skaters": opp, "empty_net": goalie is None,
                "score_diff": max(-3, min(3, (e["home_score"] - e["away_score"]) * (1 if home else -1))),
                "period": min(e["period"], 4), "sec": e["sec"],
                "prev_type": "none", "prev_same_team": False, "prev_gap": 60.0, "prev_dist": 0.0, "prev_x": 0.0, "prev_angle_change": 0.0,
            }
            if prev is not None and e["period"] == prev["period"]:
                same = prev["is_home"] == home
                px, py = (prev["x"], prev["y"]) if same else (-prev["x"], -prev["y"])  # into the shooter's frame
                gap = float(e["sec"] - prev["sec"])
                p_angle = math.degrees(math.atan2(py, NET_X - px))
                row |= {"prev_type": prev["type"], "prev_same_team": same, "prev_gap": min(gap, 60.0), "prev_dist": math.hypot(x - px, y - py),
                        "prev_x": px, "prev_angle_change": abs(math.degrees(math.atan2(y, NET_X - x)) - p_angle) if prev["type"] in UNBLOCKED else 0.0}
            rows.append(row)
        if e["x_norm"] is not None and e["is_home"] is not None:
            prev = {"type": typ, "is_home": e["is_home"], "x": float(e["x_norm"]), "y": float(e["y_norm"]), "sec": e["sec"], "period": e["period"]}
    out = pl.DataFrame(rows)
    out.write_parquet(d / "shot_features.parquet")
    return {"season": season, "attempts": out.height, "unblocked": out.filter(pl.col("type") != "blocked-shot").height}
