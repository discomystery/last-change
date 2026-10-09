"""Goalie numbers: goals saved above expected, the workload each goalie faces, and how steady he is.

Everything uses shots faced while he was in net; empty-net shots are excluded. "blend" pools this season with
last season (goalie samples are small); "season" is this season alone.
"""
from collections import defaultdict

import numpy as np
import polars as pl

from pipeline.config import REGULAR, TABLES

UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
TIERS = (("low", 0.0, 0.05), ("medium", 0.05, 0.15), ("high", 0.15, 9.9))
LONG_WAIT = 180
MIN_STARTS = {"blend": 10, "season": 2}


def shots(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date")
    ev = pl.read_parquet(d / "events.parquet").filter(pl.col("type").is_in(UNBLOCKED) & pl.col("goalie_id").is_not_null()).select("game_id", "event_id", "goalie_id", "type", "period", "sec")
    f = pl.read_parquet(d / "shot_features.parquet").select("game_id", "event_id", "empty_net", "prev_type", "prev_gap", "prev_x", "prev_same_team")
    xg = pl.read_parquet(d / "shots_xg_own.parquet")
    s = ev.join(f, on=["game_id", "event_id"]).join(xg, on=["game_id", "event_id"]).join(games, on="game_id").filter(~pl.col("empty_net"))
    turnover = (pl.col("prev_gap") <= 5) & (((pl.col("prev_type") == "giveaway") & ~pl.col("prev_same_team")) | ((pl.col("prev_type") == "takeaway") & pl.col("prev_same_team")))
    return s.with_columns(goal=pl.col("type") == "goal", on_goal=pl.col("type") != "missed-shot", breakdown=turnover & (pl.col("xg") >= 0.15), season=pl.lit(season))


def per_game(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    s = shots(season).sort("game_id", "goalie_id", "sec")
    # Wait before each shot on goal: since his previous shot on goal, or since the period began.
    og = s.filter(pl.col("on_goal")).with_columns(prev=pl.col("sec").shift(1).over("game_id", "goalie_id", "period"))
    og = og.with_columns(gap=pl.col("sec") - pl.coalesce(pl.col("prev"), (pl.col("period") - 1) * 1200))
    gaps = og.group_by("game_id", "goalie_id").agg(pl.col("gap").alias("gaps"))
    agg = s.group_by("game_id", "goalie_id", "date").agg(
        pl.len().alias("fa"), pl.col("on_goal").sum().alias("sa"), pl.col("goal").sum().alias("ga"), pl.col("xg").sum().alias("xga"), pl.col("breakdown").sum().alias("bd"),
        *[((pl.col("xg") >= lo) & (pl.col("xg") < hi) & pl.col("on_goal")).sum().alias(f"sa_{name}") for name, lo, hi in TIERS],
        *[((pl.col("xg") >= lo) & (pl.col("xg") < hi) & pl.col("goal")).sum().alias(f"ga_{name}") for name, lo, hi in TIERS],
    )
    toi = pl.read_parquet(d / "toi_check.parquet").filter(pl.col("goalie")).select("game_id", pl.col("player_id").alias("goalie_id"), pl.col("shift_toi").alias("sec"))
    team = pl.read_parquet(d / "players.parquet").select("game_id", pl.col("player_id").alias("goalie_id"), "team_id")
    out = agg.join(gaps, on=["game_id", "goalie_id"], how="left").join(toi, on=["game_id", "goalie_id"]).join(team, on=["game_id", "goalie_id"])
    # The starter is whoever played most of the game for his team.
    return out.with_columns(started=pl.col("sec") == pl.col("sec").max().over("game_id", "team_id"), season=pl.lit(season))


def summarise(games: pl.DataFrame, league_sv: dict[int, float]) -> dict:
    g = games.sort("date", "game_id")
    sec, sa, ga, xga, fa = (float(g[c].sum()) for c in ("sec", "sa", "ga", "xga", "fa"))
    starts = g.filter(pl.col("started") & (pl.col("sec") >= 1200))
    all_gaps = np.array([x for row in g["gaps"].to_list() if row for x in row], dtype=float)
    per_start = [(r["xga"] - r["ga"], (r["sa"] - r["ga"]) / r["sa"] if r["sa"] else 1.0, r["sa"], r["season"]) for r in starts.iter_rows(named=True)]
    quality = sum(1 for _, sv, n, yr in per_start if sv >= league_sv[yr] or (n <= 20 and sv >= 0.885))
    bad = sum(1 for _, sv, _, _ in per_start if sv < 0.85)
    bins = [0] * 6
    for v, *_ in per_start:
        bins[int(min(5, max(0, np.floor(v) + 3)))] += 1
    hours = sec / 3600 if sec else None
    out = {"starts": starts.height, "games": g.height, "shots": int(sa), "goals": int(ga), "gsax": round(xga - ga, 1), "gsax60": round((xga - ga) / hours, 3) if hours else None,
           "xga60": round(xga / hours, 3) if hours else None, "sv": round((sa - ga) / sa, 3) if sa else None, "sa60": round(sa / hours, 1) if hours else None,
           "danger": round(xga / fa, 4) if fa else None, "bd60": round(float(g["bd"].sum()) / hours, 2) if hours else None,
           "gap": round(float(np.median(all_gaps))) if len(all_gaps) else None, "long_wait": round(100 * float((all_gaps > LONG_WAIT).mean())) if len(all_gaps) else None,
           "qs": round(100 * quality / starts.height) if starts.height else None, "rbs": round(100 * bad / starts.height) if starts.height else None,
           "bins": bins, "tiers": {}}
    for name, _, _ in TIERS:
        n, a = float(g[f"sa_{name}"].sum()), float(g[f"ga_{name}"].sum())
        out["tiers"][name] = {"shots": int(n), "sv": round((n - a) / n, 3) if n else None}
    return out


TRAITS = {"gsax": ("gsax60", True), "busy": ("sa60", True), "danger": ("danger", True), "breakdowns": ("bd60", True), "gaps": ("gap", True), "steady": ("qs", True)}


def run(season: int) -> dict:
    frames = {yr: per_game(yr) for yr in (season - 1, season)}
    league_sv = {}
    for yr, f in frames.items():
        league_sv[yr] = float((f["sa"].sum() - f["ga"].sum()) / f["sa"].sum())
    modes = {"blend": pl.concat(list(frames.values())), "season": frames[season]}
    current = frames[season]
    goalies = defaultdict(dict)
    for mode, table in modes.items():
        for (gid,), games in table.group_by("goalie_id"):
            goalies[gid][mode] = summarise(games, league_sv)
        # Where each goalie sits among those with enough starts to judge.
        ok = [gid for gid, m in goalies.items() if mode in m and m[mode]["starts"] >= MIN_STARTS[mode]]
        for trait, (field, _) in TRAITS.items():
            vals = np.array([goalies[gid][mode][field] for gid in ok if goalies[gid][mode][field] is not None], dtype=float)
            for gid in goalies:
                if mode not in goalies[gid]:
                    continue
                v = goalies[gid][mode][field]
                enough = gid in ok and v is not None and len(vals) > 1
                goalies[gid][mode].setdefault("pct", {})[trait] = round(100 * (float((vals < v).sum()) + 0.5) / len(vals)) if enough else None
    league = [{"id": gid, "x": m["blend"]["xga60"], "y": m["blend"]["gsax60"]} for gid, m in sorted(goalies.items()) if "blend" in m and m["blend"]["starts"] >= MIN_STARTS["blend"]]
    # This season's goalies per team, most-used first.
    by_team = defaultdict(list)
    use = current.group_by("team_id", "goalie_id").agg(pl.col("sec").sum(), pl.col("started").sum().alias("starts"), pl.col("date").max()).sort("sec", descending=True)
    for r in use.iter_rows(named=True):
        by_team[r["team_id"]].append(r["goalie_id"])
    return {"goalies": {gid: m for gid, m in goalies.items() if "season" in m}, "league": league, "by_team": dict(by_team), "league_sv": league_sv, "min_starts": MIN_STARTS}
