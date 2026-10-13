"""Playoff series from the season's games: who plays whom, the series score before each game, who won.

NHL playoff game ids carry the series: 2025030314 = 2025-26 season, playoffs (03), round 3, series 1 of that round,
game 4. The NHL letters its series A to O through the bracket (round 1 A-H, round 2 I-L, conference finals M-N,
the Final O), so a series is named by its spring and letter: "2026-m".
"""
from collections import defaultdict
from functools import lru_cache

import polars as pl

from pipeline.config import FINAL_STATES, PLAYOFFS, TABLES

ROUND_START = {1: 0, 2: 8, 3: 12, 4: 14}  # letters used before each round
ROUND_NAMES = {1: "First round", 2: "Second round", 3: "Conference final", 4: "Stanley Cup Final"}
ROUND_SHORT = {1: "Round 1", 2: "Round 2", 3: "Conference final", 4: "Final"}


def parts(game_id: int) -> tuple[int, int, int]:
    """(round, series number within the round, game number) from a playoff game id."""
    s = str(game_id)
    return int(s[-3]), int(s[-2]), int(s[-1])


def letter(rnd: int, num: int) -> str:
    return "ABCDEFGHIJKLMNO"[ROUND_START[rnd] + num - 1]


def series_id(season: int, game_id: int) -> str:
    rnd, num, _ = parts(game_id)
    return f"{season + 1}-{letter(rnd, num).lower()}"


def build(season: int, conferences: dict[str, str] | None = None) -> list[dict]:
    """Every series of the season's playoffs, in bracket order. `top` is the team with home ice (home in Game 1).
    Each game carries the series score before it ([top, bottom]); `wins` is the score after the last final game."""
    games = (pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type") == PLAYOFFS)
             .sort("game_id"))
    by = defaultdict(list)
    for g in games.iter_rows(named=True):
        by[series_id(season, g["game_id"])].append(g)
    out = []
    for sid, gs in sorted(by.items(), key=lambda kv: kv[0]):
        rnd, num, _ = parts(gs[0]["game_id"])
        top, bottom = gs[0]["home"], gs[0]["away"]
        wins = {top: 0, bottom: 0}
        rows = []
        for g in gs:
            _, _, n = parts(g["game_id"])
            final = g["state"] in FINAL_STATES and g["home_score"] is not None
            row = {"id": g["game_id"], "game": n, "date": g["date"], "start": g["start_utc"], "home": g["home"], "away": g["away"],
                   "venue": g["venue"], "final": final, "before": [wins[top], wins[bottom]]}
            if final:
                w = g["home"] if g["home_score"] > g["away_score"] else g["away"]
                wins[w] += 1
                row.update({"hs": g["home_score"], "as": g["away_score"], "end": g["last_period"], "winner": w,
                            "ot": max(0, _periods(g) - 3)})
            rows.append(row)
        winner = next((t for t, w in wins.items() if w == 4), None)
        conf = (conferences or {}).get(top) if rnd < 4 else None
        out.append({"id": sid, "letter": sid[-1].upper(), "round": rnd, "round_name": ROUND_NAMES[rnd], "conference": conf,
                    "top": top, "bottom": bottom, "wins": [wins[top], wins[bottom]], "winner": winner,
                    "loser": (bottom if winner == top else top) if winner else None, "games": rows})
    return out


@lru_cache(maxsize=4)
def _last_periods(season: int) -> dict[int, int]:
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet", columns=["game_id", "period"])
    return dict(ev.group_by("game_id").agg(pl.col("period").max()).iter_rows())


def _periods(g: dict) -> int:
    """How many periods a final game ran (3 in regulation); multi-overtime games need the play-by-play."""
    if g["last_period"] != "OT":
        return 3
    return int(_last_periods(g["season"]).get(g["game_id"], 4))


def score_words(a: str, b: str, wins: list[int], done: bool = False) -> str:
    """"CAR lead 2-1", "Tied 2-2", "CAR win 4-1" from the series score [a's wins, b's wins]."""
    x, y = wins
    if x == y:
        return f"Tied {x}-{y}"
    lead, w = (a, (x, y)) if x > y else (b, (y, x))
    verb = "won" if done else "lead"
    return f"{lead} {verb} {w[0]}-{w[1]}"
