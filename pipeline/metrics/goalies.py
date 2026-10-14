"""Goalie numbers: goals saved above expected, the workload each goalie faces, and how steady he is.

Everything uses shots faced while he was in net; empty-net shots are excluded. "blend" pools this season with
last season (goalie samples are small); "season" is this season alone.
"""
from collections import defaultdict
from functools import lru_cache

import numpy as np
import polars as pl

from pipeline.config import FULL_SEASONS, REGULAR, TABLES
from pipeline.metrics.team_game import BREAKDOWN_XG

UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
TIERS = (("low", 0.0, 0.05), ("medium", 0.05, 0.15), ("high", 0.15, 9.9))
LONG_WAIT = 180
MIN_STARTS = {"blend": 10, "season": 2}
# Goals saved above expected are mostly bounces over a few weeks, so the rating behind the "gsax" percentile is pulled
# toward league average (zero) by this many unblocked shots, about a season and a half for a starter. Picked
# 2026-10-11 by predicting the rest of each 2023-24 to 2025-26 season from a goalie's first 5 / 10 / 20 starts (and
# odd games from even): error per shot after 10 starts 1.65 raw, 0.67 at 1,000, 0.62 at 3,000, flat beyond.
SHRINK_SHOTS = 3000
# Rebound control: a save "gives a rebound" when the shooting team gets another unblocked shot within this many
# seconds, with no other play in between. Judged against how often saves on that kind of shot from that spot do
# (shot type x 10 ft distance band x 10 ft side band, 2023-24 to 2025-26). Tested 2026-10-12: a goalie's rate against
# expected repeats (split-half r 0.21-0.26, year to year 0.33-0.40) but does not predict his goals against, so it is
# a style card, not a quality one.
REBOUND_SECONDS = 3


def _rebound_cell() -> list[pl.Expr]:
    return [pl.col("shot_type").fill_null("unknown"), (pl.col("dist") / 10).floor().clip(0, 6).alias("db"), (pl.col("y_abs") / 10).floor().clip(0, 3).alias("ab")]


def _saves(season: int) -> pl.DataFrame:
    """Every save (shot on goal, no goal) with whether the shooting team shot again within REBOUND_SECONDS."""
    d = TABLES / str(season)
    ev = pl.read_parquet(d / "events.parquet").filter(pl.col("period_type") != "SO").sort("game_id", "sort").with_columns(
        t=pl.col("period") * 1200 + pl.col("sec"))
    nxt = [pl.col(c).shift(-1).over("game_id").alias(f"n_{c}") for c in ("type", "team_id", "t")]
    ev = ev.with_columns(nxt).filter((pl.col("type") == "shot-on-goal") & pl.col("goalie_id").is_not_null())
    reb = pl.col("n_type").is_in(UNBLOCKED) & (pl.col("n_team_id") == pl.col("team_id")) & (pl.col("n_t") - pl.col("t") <= REBOUND_SECONDS)
    f = pl.read_parquet(d / "shot_features.parquet").select("game_id", "event_id", "dist", "y_abs", "shot_type")
    return ev.with_columns(rebound=reb.fill_null(False)).select("game_id", "event_id", "rebound").join(f, on=["game_id", "event_id"])


@lru_cache(maxsize=1)
def rebound_rates() -> pl.DataFrame:
    """League rate of rebounds after a save, by shot type, distance and side band, over the full seasons."""
    s = pl.concat([_saves(yr) for yr in FULL_SEASONS if (TABLES / str(yr) / "shot_features.parquet").exists()])
    return s.group_by(_rebound_cell()).agg(rb_exp=pl.col("rebound").cast(pl.Float64).mean())


def shots(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date")
    ev = pl.read_parquet(d / "events.parquet").filter(pl.col("type").is_in(UNBLOCKED) & pl.col("goalie_id").is_not_null()).select("game_id", "event_id", "goalie_id", "type", "period", "sec")
    f = pl.read_parquet(d / "shot_features.parquet").select("game_id", "event_id", "empty_net", "prev_type", "prev_gap", "prev_x", "prev_same_team")
    xg = pl.read_parquet(d / "shots_xg_own.parquet")
    s = ev.join(f, on=["game_id", "event_id"]).join(xg, on=["game_id", "event_id"]).join(games, on="game_id").filter(~pl.col("empty_net"))
    sv = _saves(season).with_columns(_rebound_cell()).join(rebound_rates(), on=["shot_type", "db", "ab"], how="left").select(
        "game_id", "event_id", "rebound", pl.col("rb_exp").fill_null(0.0))
    s = s.join(sv, on=["game_id", "event_id"], how="left").with_columns(pl.col("rebound").fill_null(False), pl.col("rb_exp").fill_null(0.0))
    # Breakdowns in front of him: the team trait's rule exactly (team_game: a dangerous shot within a few seconds of
    # a turnover), read from the shots team_game flagged.
    if not (d / "turnover_shots.parquet").exists():
        from pipeline.metrics import team_game
        team_game.build(season)
    to = pl.read_parquet(d / "turnover_shots.parquet").with_columns(turnover=pl.lit(True))
    s = s.join(to, on=["game_id", "event_id"], how="left").with_columns(pl.col("turnover").fill_null(False))
    return s.with_columns(goal=pl.col("type") == "goal", on_goal=pl.col("type") != "missed-shot",
                          breakdown=pl.col("turnover") & (pl.col("xg") >= BREAKDOWN_XG), season=pl.lit(season))


def per_game(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    s = shots(season).sort("game_id", "goalie_id", "sec")
    # Wait before each shot on goal: since his previous shot on goal, or since the period began.
    og = s.filter(pl.col("on_goal")).with_columns(prev=pl.col("sec").shift(1).over("game_id", "goalie_id", "period"))
    og = og.with_columns(gap=pl.col("sec") - pl.coalesce(pl.col("prev"), (pl.col("period") - 1) * 1200))
    gaps = og.group_by("game_id", "goalie_id").agg(pl.col("gap").alias("gaps"))
    agg = s.group_by("game_id", "goalie_id", "date").agg(
        pl.len().alias("fa"), pl.col("on_goal").sum().alias("sa"), pl.col("goal").sum().alias("ga"), pl.col("xg").sum().alias("xga"), pl.col("breakdown").sum().alias("bd"),
        pl.col("rebound").sum().alias("rb"), pl.col("rb_exp").sum().alias("rb_exp"),
        *[((pl.col("xg") >= lo) & (pl.col("xg") < hi) & pl.col("on_goal")).sum().alias(f"sa_{name}") for name, lo, hi in TIERS],
        *[((pl.col("xg") >= lo) & (pl.col("xg") < hi) & pl.col("goal")).sum().alias(f"ga_{name}") for name, lo, hi in TIERS],
    )
    toi = pl.read_parquet(d / "toi_check.parquet").filter(pl.col("goalie")).select("game_id", pl.col("player_id").alias("goalie_id"), pl.col("shift_toi").alias("sec"))
    team = pl.read_parquet(d / "players.parquet").select("game_id", pl.col("player_id").alias("goalie_id"), "team_id")
    out = agg.join(gaps, on=["game_id", "goalie_id"], how="left").join(toi, on=["game_id", "goalie_id"]).join(team, on=["game_id", "goalie_id"])
    # The starter is whoever was in net at puck drop (first goalie on the shift chart). Most ice time is only the
    # fallback for a game without one: it named the relief goalie when a starter was pulled early (FLA at BUF
    # 2026-10-12, both starters pulled).
    first = _first_in_net(d)
    out = out.join(first, on=["game_id", "team_id"], how="left")
    most = pl.col("sec") == pl.col("sec").max().over("game_id", "team_id")
    started = pl.when(pl.col("first_goalie").is_null().any().over("game_id", "team_id") | ~(pl.col("first_goalie") == pl.col("goalie_id")).any().over("game_id", "team_id"))\
        .then(most).otherwise(pl.col("goalie_id") == pl.col("first_goalie"))
    return out.with_columns(started=started, season=pl.lit(season)).drop("first_goalie")


def _first_in_net(d) -> pl.DataFrame:
    """Each team's first goalie in net in each game, from the stints."""
    st = pl.read_parquet(d / "stints.parquet", columns=["game_id", "period", "start", "home_goalie", "away_goalie"]).sort("game_id", "period", "start")
    g = pl.read_parquet(d / "games.parquet", columns=["game_id", "home_id", "away_id"])
    side = [st.filter(pl.col(f"{s}_goalie").is_not_null()).group_by("game_id", maintain_order=True).first()
            .join(g, on="game_id").select("game_id", pl.col(f"{s}_id").alias("team_id"), pl.col(f"{s}_goalie").alias("first_goalie"))
            for s in ("home", "away")]
    return pl.concat(side)


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
           "gsax_est60": round((xga - ga) / hours * fa / (fa + SHRINK_SHOTS), 3) if hours else None,
           "xga60": round(xga / hours, 3) if hours else None, "sv": round((sa - ga) / sa, 3) if sa else None, "sa60": round(sa / hours, 1) if hours else None,
           "danger": round(xga / fa, 4) if fa else None, "bd60": round(float(g["bd"].sum()) / hours, 2) if hours else None,
           "gap": round(float(np.median(all_gaps))) if len(all_gaps) else None, "long_wait": round(100 * float((all_gaps > LONG_WAIT).mean())) if len(all_gaps) else None,
           "qs": round(100 * quality / starts.height) if starts.height else None, "rbs": round(100 * bad / starts.height) if starts.height else None,
           "bins": bins, "tiers": {}}
    saves = sa - ga
    if saves:  # rebounds per 100 saves, his and what the same saves usually give; reb_ctrl is higher when he gives fewer
        rb, rb_exp = float(g["rb"].sum()), float(g["rb_exp"].sum())
        out.update(reb=round(100 * rb / saves, 1), reb_exp=round(100 * rb_exp / saves, 1), reb_ctrl=round(100 * (rb_exp - rb) / saves, 2))
    else:
        out.update(reb=None, reb_exp=None, reb_ctrl=None)
    for name, _, _ in TIERS:
        n, a = float(g[f"sa_{name}"].sum()), float(g[f"ga_{name}"].sum())
        out["tiers"][name] = {"shots": int(n), "sv": round((n - a) / n, 3) if n else None}
    return out


TRAITS = {"gsax": ("gsax_est60", True), "rebounds": ("reb_ctrl", True), "busy": ("sa60", True), "danger": ("danger", True), "breakdowns": ("bd60", True), "gaps": ("gap", True), "steady": ("qs", True)}


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
        for trait, (field, higher) in TRAITS.items():
            vals = np.array([goalies[gid][mode][field] for gid in ok if goalies[gid][mode][field] is not None], dtype=float)
            for gid in goalies:
                if mode not in goalies[gid]:
                    continue
                v = goalies[gid][mode][field]
                enough = gid in ok and v is not None and len(vals) > 1
                goalies[gid][mode].setdefault("pct", {})[trait] = round(100 * (float((vals < v).sum() if higher else (vals > v).sum()) + 0.5) / len(vals)) if enough else None
    league = [{"id": gid, "x": m["blend"]["xga60"], "y": m["blend"]["gsax60"]} for gid, m in sorted(goalies.items()) if "blend" in m and m["blend"]["starts"] >= MIN_STARTS["blend"]]
    # This season's goalies per team, most-used first.
    by_team = defaultdict(list)
    use = current.group_by("team_id", "goalie_id").agg(pl.col("sec").sum(), pl.col("started").sum().alias("starts"), pl.col("date").max()).sort("sec", descending=True)
    for r in use.iter_rows(named=True):
        by_team[r["team_id"]].append(r["goalie_id"])
    # Goalies who played last season but not yet this one (injured, in the minors), for their player pages.
    idle = {gid: m for gid, m in goalies.items() if "season" not in m}
    return {"goalies": {gid: m for gid, m in goalies.items() if "season" in m}, "idle": idle, "league": league, "by_team": dict(by_team), "league_sv": league_sv, "min_starts": MIN_STARTS}
