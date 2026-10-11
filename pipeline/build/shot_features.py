"""One row per shot attempt with everything the in-house expected-goals model looks at.

Nothing here identifies the shooter or the goalie: the model is meant to describe the chance, not the players.
"""
import bisect
import math
from collections import defaultdict

import polars as pl

from pipeline.build.stints import penalty_shot
from pipeline.config import TABLES

NET_X = 89.0
ATTEMPTS = {"shot-on-goal", "missed-shot", "goal", "blocked-shot"}
UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
LIVE_RESET = {"stoppage", "period-start", "period-end"}


def shift_starts(stints: pl.DataFrame) -> dict:
    """game -> player -> (sorted shift starts, [(start, end)]): each player's unbroken stretches on the ice, from the stints."""
    out = {}
    for (g,), grp in stints.sort("start").group_by("game_id", maintain_order=True):
        shifts, open_, last_end = defaultdict(list), {}, None
        for r in grp.iter_rows(named=True):
            on = set(r["home_skaters"]) | set(r["away_skaters"])
            for p in [p for p in open_ if p not in on or r["start"] != last_end]:
                shifts[p].append(open_.pop(p))
            for p in on:
                open_[p] = (open_[p][0] if p in open_ else r["start"], r["end"])
            last_end = r["end"]
        for p, iv in open_.items():
            shifts[p].append(iv)
        out[g] = {p: ([a for a, _ in v], v) for p, v in shifts.items()}
    return out


def _on_for(starts: dict, player: int, sec: int) -> int | None:
    """Seconds the player has been on the ice at this moment (only time before the shot, so nothing after it can leak in)."""
    if player not in starts:
        return None
    first, iv = starts[player]
    i = bisect.bisect_right(first, sec) - 1
    if i < 0 or sec > iv[i][1]:
        return None
    return sec - iv[i][0]


def build(season: int) -> dict:
    d = TABLES / str(season)
    events = pl.read_parquet(d / "events.parquet").sort("game_id", "sort")
    shifts = shift_starts(pl.read_parquet(d / "stints.parquet"))
    rows, prev, game, faceoff = [], None, None, None
    for e in events.iter_rows(named=True):
        if e["game_id"] != game:
            game, prev, faceoff = e["game_id"], None, None
            code, code_since, delayed, pens = None, 0, None, []
        typ, s = e["type"], e["sec"]
        if e["situation_code"] != code:
            code, code_since = e["situation_code"], s
        if typ == "delayed-penalty":
            delayed = e["is_home"]  # the side about to be penalised; the other side has pulled its goalie
        if typ == "penalty" and e["pen_min"] in (2, 4, 5) and e["is_home"] is not None:
            pens.append([e["is_home"], s, s + 60 * e["pen_min"], e["pen_min"]])
        if typ == "faceoff":
            faceoff = e["sec"]
        if typ in LIVE_RESET or typ == "faceoff":
            delayed = None
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
            mine, theirs = (e["home_on"], e["away_on"]) if home else (e["away_on"], e["home_on"])
            on_ice = shifts.get(e["game_id"], {})
            de = [v for v in (_on_for(on_ice, p, s) for p in theirs) if v is not None]
            at = [v for v in (_on_for(on_ice, p, s) for p in mine) if v is not None]
            pk_left = [p[2] - s for p in pens if p[0] != home and p[1] <= s < p[2]]
            row = {
                "game_id": e["game_id"], "event_id": e["event_id"], "type": typ, "is_home": home, "goal": typ == "goal",
                "x": x, "y_abs": abs(y), "dist": dist, "angle": angle, "shot_type": e["shot_type"] or "unknown",
                "own_skaters": own, "opp_skaters": opp, "empty_net": goalie is None,
                "score_diff": max(-3, min(3, (e["home_score"] - e["away_score"]) * (1 if home else -1))),
                "period": min(e["period"], 4), "sec": e["sec"],
                "prev_type": "none", "prev_same_team": False, "prev_gap": 60.0, "prev_dist": 0.0, "prev_x": 0.0, "prev_angle_change": 0.0,
                "prev_dy": 0.0, "since_faceoff": float(min(max(e["sec"] - faceoff, 0), 120)) if faceoff is not None else 120.0,
                "penalty_shot": penalty_shot(e["situation_code"]),
                # how long the defenders (and the shooters) have been out: tired defenders give up more
                "def_shift_avg": sum(de) / len(de) if de else -1.0, "def_shift_max": float(max(de)) if de else -1.0,
                "att_shift_avg": sum(at) / len(at) if at else -1.0,
                "own_net_empty": (e["home_goalie"] if home else e["away_goalie"]) is None and not penalty_shot(e["situation_code"]),
                "delayed_pen": delayed is not None and delayed != home,
                "state_since": float(min(s - code_since, 300)),  # seconds since the manpower last changed
                "pen_left": float(min(pk_left)) if pk_left else 0.0,  # time left on the other side's shortest live penalty
            }
            if prev is not None and e["period"] == prev["period"]:
                same = prev["is_home"] == home
                px, py = (prev["x"], prev["y"]) if same else (-prev["x"], -prev["y"])  # into the shooter's frame
                gap = float(e["sec"] - prev["sec"])
                p_angle = math.degrees(math.atan2(py, NET_X - px))
                row |= {"prev_type": prev["type"], "prev_same_team": same, "prev_gap": min(gap, 60.0), "prev_dist": math.hypot(x - px, y - py),
                        "prev_dy": abs(y - py),  # side-to-side movement since the last play: the closest public proxy for a pass across
                        "prev_x": px, "prev_angle_change": abs(math.degrees(math.atan2(y, NET_X - x)) - p_angle) if prev["type"] in UNBLOCKED else 0.0}
            rows.append(row)
        if typ == "goal":  # a power-play goal ends the earliest minor against the side scored on
            live = [p for p in pens if p[0] != e["is_home"] and p[1] <= s < p[2] and p[3] == 2]
            if live:
                min(live, key=lambda p: p[1])[2] = s
        if e["x_norm"] is not None and e["is_home"] is not None:
            prev = {"type": typ, "is_home": e["is_home"], "x": float(e["x_norm"]), "y": float(e["y_norm"]), "sec": e["sec"], "period": e["period"]}
    out = pl.DataFrame(rows)
    out.write_parquet(d / "shot_features.parquet")
    return {"season": season, "attempts": out.height, "unblocked": out.filter(pl.col("type") != "blocked-shot").height}
