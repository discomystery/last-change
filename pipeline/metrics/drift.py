"""Recording-change alarm: has the league changed how it records plays?

Our chance model learned from 2023-24 to 2025-26 play-by-play. If the NHL changes how it records events, the model
goes wrong without any error being raised: before 2023-24, shots logged within a second of the previous play scored
9.9% of the time, against 2.3-3.2% since, and a model trained on one era rated the other badly (CLAUDE.md, three-way
xG comparison). So every full update compares this season's recording with the training seasons on a few league-wide
rates and flags any that moves further than those seasons ever differed from each other or than sampling can explain.

Checks (regular season, unblocked shots at a goalie):
- quick_goal_rate: how often shots within 1 s of the previous play score (the pre-2023 break showed up here;
  allowed 1.5 points either way at least, the training seasons spread 0.9)
- quick_share: the share of shots logged within 1 s of the previous play (at least 0.6 points either way)
- distance: average shot distance in feet (a change in where shots are placed)
- goals_per_xg: goals scored per goal our model expects (the model as a whole drifting)
- no_previous / no_location: shots with no previous play or no location recorded

Tested: run against 2022-23 (the old recording) it flags quick_goal_rate and quick_share; each of 2023-24 to 2025-26
against the other two passes, as does 2026-27 so far.
"""
import json
import math
import sys

import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, RAW, REGULAR, TABLES

MIN_SHOTS = 1500  # about 25 games; before that the rates are too loose to judge
FILE = RAW / "recording_check.json"


def _shots(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
    s = pl.read_parquet(d / "shot_features.parquet").join(games, on="game_id").filter((pl.col("type") != "blocked-shot") & ~pl.col("empty_net"))
    xg = d / "shots_xg_own.parquet"
    if xg.exists():
        s = s.join(pl.read_parquet(xg), on=["game_id", "event_id"], how="left")
    else:
        s = s.with_columns(xg=pl.lit(None, pl.Float64))
    return s


def measures(season: int) -> dict:
    s = _shots(season)
    n = s.height
    if not n:
        return {"shots": 0}
    quick = s.filter(pl.col("prev_gap") <= 1)
    goals = float(s["goal"].sum())
    xg = float(s["xg"].fill_null(0).sum())
    out = {"shots": n, "quick_shots": quick.height,
           "quick_goal_rate": float(quick["goal"].mean()) if quick.height else None,
           "quick_share": quick.height / n,
           "distance": float(s["dist"].mean()), "distance_sd": float(s["dist"].std()),
           "goals": goals, "goals_per_xg": goals / xg if xg else None,
           "no_previous": float(s["prev_type"].is_null().mean()),
           "no_location": float(s["dist"].is_null().mean())}
    return out


def _tolerance(base: list[float], se: float, floor: float) -> float:
    """How far from the training seasons' average a season may sit: the larger of 1.5x their own spread, 3 standard
    errors of this season's sample, and a fixed floor."""
    half_range = (max(base) - min(base)) / 2
    return max(1.5 * half_range, 3 * se, floor)


def check(season: int = CURRENT_SEASON, baseline: list[int] | None = None) -> dict:
    baseline = [s for s in (baseline or FULL_SEASONS) if s != season and (TABLES / str(s) / "shot_features.parquet").exists()]
    now = measures(season)
    out = {"season": season, "baseline": baseline, "shots": now.get("shots", 0), "checks": []}
    if now.get("shots", 0) < MIN_SHOTS or not baseline:
        out["status"] = "early"
        return out
    base = {s: measures(s) for s in baseline}

    def add(name: str, value, se: float, floor: float, label: str) -> None:
        if value is None:
            return
        ref = [base[s][name] for s in baseline if base[s].get(name) is not None]
        mean = sum(ref) / len(ref)
        tol = _tolerance(ref, se, floor)
        out["checks"].append({"name": name, "label": label, "value": round(value, 4), "training": [round(r, 4) for r in ref],
                              "allowed": [round(mean - tol, 4), round(mean + tol, 4)], "ok": abs(value - mean) <= tol})

    q, p = now["quick_shots"], now["quick_goal_rate"]
    if q:
        add("quick_goal_rate", p, math.sqrt(max(p * (1 - p), 1e-6) / q), 0.015, "goal rate of shots within 1 s of the previous play")
    n, share = now["shots"], now["quick_share"]
    add("quick_share", share, math.sqrt(share * (1 - share) / n), 0.006, "share of shots within 1 s of the previous play")
    add("distance", now["distance"], now["distance_sd"] / math.sqrt(n), 1.5, "average shot distance (ft)")
    if now["goals_per_xg"] is not None:
        add("goals_per_xg", now["goals_per_xg"], math.sqrt(now["goals"]) / (now["goals"] / now["goals_per_xg"]), 0.06, "goals per expected goal")
    for name, label in (("no_previous", "shots with no previous play recorded"), ("no_location", "shots with no location recorded")):
        out["checks"].append({"name": name, "label": label, "value": round(now[name], 4), "allowed": [0, 0.01], "ok": now[name] <= 0.01})
    out["status"] = "ok" if all(c["ok"] for c in out["checks"]) else "changed"
    return out


def save(result: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(result, indent=1))


def alarm_text(result: dict) -> str:
    bad = [c for c in result.get("checks", []) if not c["ok"]]
    lines = [f"The nightly update found that the NHL's play-by-play for {result['season']}-{(result['season'] + 1) % 100:02d} "
             f"no longer looks like the seasons our chance model learned from ({', '.join(map(str, result['baseline']))}). "
             "If the league changed how it records plays, chance numbers across the site may be off until the model is checked.", ""]
    for c in bad:
        lines.append(f"- {c['label']}: {c['value']} (training seasons {c['training'] if 'training' in c else ''}, allowed {c['allowed'][0]} to {c['allowed'][1]})")
    lines += ["", f"Shots checked: {result['shots']}. Ask Claude in the project to look into it (pipeline/metrics/drift.py)."]
    return "\n".join(lines)


if __name__ == "__main__":
    # `python -m pipeline.metrics.drift`: print the saved check; with --alarm, print the issue text when it flagged.
    r = json.loads(FILE.read_text()) if FILE.exists() else check()
    if "--alarm" in sys.argv:
        if r.get("status") == "changed":
            print(alarm_text(r))
    else:
        print(json.dumps(r, indent=1))
