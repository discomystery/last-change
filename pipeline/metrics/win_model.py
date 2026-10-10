"""Pre-game win probability (kept off the site before puck drop).

The site never shows a pre-game number: the probability only steers preview wording, and the post-game page may
reveal it with its breakdown. Every input is built from games played strictly before the game's date, so the same
figure can be recomputed after the game without leaking the result.

Inputs, per team, as a decaying average over its recent games (older games count less; last season's games count
for less again; a few phantom league-average games pull small samples toward the middle):
  - xgd: expected-goal difference per game, all situations, empty nets excluded (how well a team controls chances)
  - rest: playing on the second night of a back-to-back
Lineups shift each team's chance rating, in the same expected-goals-per-game units (see _lineup_pass): each
skater is valued by his isolated 5v5 impact (rapm.py, fitted on earlier seasons only) times his usual 5v5 ice time,
and a lineup is compared with the lineups behind the team's rating, so the model does not wait for results to show
that a star is missing or back.
  - roster (morning): the lineup the team used in its previous game. Catches players already out or back.
  - lineup (puck drop): tonight's dressed skaters, which also catches tonight's changes.
Backtest 2024-25 to 2026-27: in the 37% of games where a team's lineup was clearly weaker (0.15 expected goals a
game) than the lineups behind its rating, the model without lineups gave it 46%, the morning figure 42%, the
puck-drop figure 41%, and it won 41%. In the 14% where it was clearly weaker than in its previous game (news the
morning figure cannot see), the morning figure gave it 52%, puck drop 48%, and it won 48%. Lineups move the chance
2 points in a typical game, 5 at the 90th percentile, 13 at most.
Finishing and goaltending (goals minus expected goals, for the team or for the starting goalie) were tested and
dropped: over 2023-25 their weight came out at zero. A logistic regression on the home-minus-away gaps (plus home
ice) turns them into a home win probability.
Shootout and overtime results count as wins and losses like any other.
"""
import math
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from pipeline.config import FINAL_STATES, REGULAR, TABLES

FEATURES = ["xgd", "b2b"]
# Lineup terms: less cautious impact ratings than the player pages' (those are tuned for one player's numbers, not a
# lineup's sum). Backtest log loss gains over no lineup, 2024-25 / 2025-26 / 2026-27 so far: puck drop 0.0036 /
# 0.0010 / 0.0125 (the earlier rule, tonight against a flat average of the last ten lineups: 0.0013 / 0.0011 /
# 0.0133); morning 0.0021 / -0.0011 / 0.0129. A heavier weight helped one season and hurt another, so it stays at one.
LINEUP_LAMBDA = 15000.0
LINEUP_WEIGHT = 1.0
SEC5_PER_GAME = 5 * 2900  # skater-seconds of 5v5 play in a typical game, to share out by usual ice time


@dataclass(frozen=True)
class Params:
    # Chosen by fitting on 2023-24 and checking 2024-25; nearby values score within 0.001 log loss.
    decay: float = 0.95  # weight kept per game played since (half-life about 14 games)
    carry: float = 0.8  # extra weight kept on last season's games when a new season starts
    prior_games: float = 4.0  # phantom league-average games added to every rating


def team_games(seasons: list[int]) -> pl.DataFrame:
    """One row per regular-season team-game: date, both teams' expected-goal difference with empty nets removed."""
    out = []
    for s in seasons:
        d = TABLES / str(s)
        g = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(FINAL_STATES))
        tg = (pl.read_parquet(d / "team_game.parquet").filter(pl.col("strength") != "EN")
              .group_by("game_id", "team_id").agg((pl.col("xgf") - pl.col("xga")).sum().alias("xgd")))
        out.append(g.select("game_id", "season", "date", "home_id", "away_id", "home_score", "away_score").join(
            tg.rename({"team_id": "home_id", "xgd": "h_xgd"}), on=["game_id", "home_id"]).join(
            tg.rename({"team_id": "away_id", "xgd": "a_xgd"}), on=["game_id", "away_id"]))
    return pl.concat(out).sort("date", "game_id")


def pregame(games: pl.DataFrame, p: Params = Params()) -> pl.DataFrame:
    """Each game with both teams' ratings as of the morning of the game (only earlier dates feed them)."""
    sw: dict[int, float] = defaultdict(float)  # summed weights
    sx: dict[tuple, float] = defaultdict(float)  # summed weighted values per (team, stat)
    last_day: dict[int, object] = {}
    season = None
    rows = []
    by_date = games.partition_by("date", maintain_order=True)
    for day in by_date:
        d0 = day["date"][0]
        if day["season"][0] != season:
            season = day["season"][0]
            for t in list(sw):
                sw[t] *= p.carry
                sx[(t, "xgd")] *= p.carry

        def rating(t, k):
            return sx[(t, k)] / (sw[t] + p.prior_games)

        dt = np.datetime64(d0)
        for g in day.iter_rows(named=True):
            r = {"game_id": g["game_id"], "season": g["season"], "date": g["date"], "home_id": g["home_id"], "away_id": g["away_id"],
                 "home_win": int(g["home_score"] > g["away_score"]), "home_gp": sw[g["home_id"]], "away_gp": sw[g["away_id"]]}
            for side, t in (("h", g["home_id"]), ("a", g["away_id"])):
                r[f"{side}_xgd"] = rating(t, "xgd")
                prev = last_day.get(t)
                r[f"{side}_b2b"] = int(prev is not None and (dt - np.datetime64(prev)).astype(int) == 1)
            rows.append(r)
        # Results of the day go in only after every game of that day has its rating.
        for g in day.iter_rows(named=True):
            for t, xgd in ((g["home_id"], g["h_xgd"]), (g["away_id"], g["a_xgd"])):
                sw[t] = sw[t] * p.decay + 1
                sx[(t, "xgd")] = sx[(t, "xgd")] * p.decay + xgd
                last_day[t] = d0
    df = pl.DataFrame(rows)
    return df.with_columns(xgd=pl.col("h_xgd") - pl.col("a_xgd"), b2b=pl.col("h_b2b") - pl.col("a_b2b"))


def _rates(season: int, have: list[int]) -> dict[int, float]:
    """Each skater's offense plus defense impact per 60 from the two seasons before `season` (none for rookies)."""
    from pipeline.metrics import rapm

    prior = [x for x in (season - 2, season - 1) if x in have]
    if not prior:
        return {}
    r = rapm.run(prior, LINEUP_LAMBDA)
    return dict(zip(r["player_id"].to_list(), (r["off"] + r["def"]).to_list()))


@lru_cache(maxsize=4)
def _lineup_pass(seasons: tuple[int, ...], p: Params = Params()) -> tuple[pl.DataFrame, dict[int, float]]:
    """Walks every game day in order and values lineups against what each team's rating has already seen.

    A lineup's value is the sum over its skaters of impact per 60 times his share of a game's 5v5 time, judged from
    his earlier games. A team's rating (pregame) is a decaying average of its games, so it already holds the lineups
    of those games; the lineup terms are tonight's lineup minus that same decaying average of past lineups, valued
    with tonight's ratings. A star who has missed five games is therefore already partly out of the rating, and a
    returning one is added back in full on his first night.

    Returns, per (game, team): `lineup` (who actually dressed, known at puck drop) and `roster` (who dressed in the
    team's previous game, known the morning of the game), plus each team's `roster` term for its next game."""
    have = [s for s in range(min(seasons) - 2, max(seasons) + 1) if (TABLES / str(s) / "player_game.parquet").exists()]
    rate = {s: _rates(s, have) for s in seasons}
    pg = pl.concat([
        pl.read_parquet(TABLES / str(s) / "player_game.parquet").filter(pl.col("pos") != "G").select("game_id", "player_id", "team_id", "pos", "sec5")
        .join(pl.read_parquet(TABLES / str(s) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date"), on="game_id")
        .with_columns(season=pl.lit(s)) for s in have]).sort("date", "game_id")
    default = {d: float(pg.filter((pl.col("pos") == "D") == d)["sec5"].mean() or pg["sec5"].mean()) for d in (True, False)}
    tw: dict[int, float] = defaultdict(float)  # decaying ice-time history per skater
    tx: dict[int, float] = defaultdict(float)
    seen: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))  # per team: decayed 5v5 seconds per skater
    seen_w: dict[int, float] = defaultdict(float)  # per team: decayed game count, as in pregame
    last: dict[int, tuple[list, list]] = {}  # per team: skaters and positions of its latest game
    season, r = None, {}

    def shares(ids, pos):
        usual = np.array([tx[q] / tw[q] if tw[q] else default[x == "D"] for q, x in zip(ids, pos)])
        return usual / usual.sum() * SEC5_PER_GAME

    def value(ids, pos):
        return float(sum(r.get(q, 0.0) * sec / 3600 for q, sec in zip(ids, shares(ids, pos))))

    def base(tid):  # the lineup the rating has seen; its phantom average games count as an average lineup (zero)
        return sum(r.get(q, 0.0) * sec / 3600 for q, sec in seen[tid].items()) / (seen_w[tid] + p.prior_games)

    rows = []
    for day in pg.partition_by("date", maintain_order=True):
        if day["season"][0] != season:
            season = day["season"][0]
            r = rate.get(season, {})
            for t in seen:
                seen_w[t] *= p.carry
                for q in seen[t]:
                    seen[t][q] *= p.carry
        today = []
        for (gid, tid), grp in day.group_by(["game_id", "team_id"], maintain_order=True):
            ids, pos = grp["player_id"].to_list(), grp["pos"].to_list()
            b = base(tid)
            prev = last.get(tid)
            rows.append({"game_id": gid, "team_id": tid, "lineup": value(ids, pos) - b,
                         "roster": (value(*prev) if prev else value(ids, pos)) - b})
            today.append((tid, ids, pos, shares(ids, pos)))
        for tid, ids, pos, sh in today:
            seen_w[tid] = seen_w[tid] * p.decay + 1
            for q in seen[tid]:
                seen[tid][q] *= p.decay
            for q, sec in zip(ids, sh):
                seen[tid][q] += sec
            last[tid] = (ids, pos)
        for q, sec in zip(day["player_id"].to_list(), day["sec5"].to_list()):
            tw[q] = tw[q] * 0.9 + 1
            tx[q] = tx[q] * 0.9 + sec
    if season != max(seasons):  # the new season has not started: next games are valued with its ratings
        r = rate.get(max(seasons), {})
        for t in seen:
            seen_w[t] *= p.carry
            for q in seen[t]:
                seen[t][q] *= p.carry
    upcoming = {tid: value(*last[tid]) - base(tid) for tid in last}
    df = pl.DataFrame(rows, schema={"game_id": pl.Int64, "team_id": pl.Int64, "lineup": pl.Float64, "roster": pl.Float64})
    return df.filter(pl.col("game_id") // 1_000_000 >= min(seasons)), upcoming


def lineups(seasons: list[int], p: Params = Params()) -> pl.DataFrame:
    """Per (game, team): `lineup` and `roster`, tonight's and last game's lineup against what the rating has seen,
    in expected goals per game (see _lineup_pass)."""
    return _lineup_pass(tuple(sorted(seasons)), p)[0]


def next_rosters(seasons: list[int], p: Params = Params()) -> dict[int, float]:
    """Each team's `roster` term for its next game: its latest lineup against what its rating has seen."""
    return _lineup_pass(tuple(sorted(seasons)), p)[1]


def with_lineups(df: pl.DataFrame, lineup: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in ("lineup", "roster") if c in lineup.columns]
    h = lineup.select("game_id", pl.col("team_id").alias("home_id"), *[pl.col(c).alias("h_" + c) for c in cols])
    a = lineup.select("game_id", pl.col("team_id").alias("away_id"), *[pl.col(c).alias("a_" + c) for c in cols])
    return (df.join(h, on=["game_id", "home_id"], how="left").join(a, on=["game_id", "away_id"], how="left")
            .with_columns(*[(pl.col("h_" + c) - pl.col("a_" + c)).fill_null(0.0).alias(c) for c in cols]))


def fit(train: pl.DataFrame) -> LogisticRegression:
    m = LogisticRegression(C=1.0)
    m.fit(train.select(FEATURES).to_numpy(), train["home_win"].to_numpy())
    return m


def predict(m: LogisticRegression, df: pl.DataFrame, puck_drop: bool = False) -> np.ndarray:
    """Home win chance: the morning figure (with last game's lineups, when the frame has them), or at puck drop with
    tonight's lineups instead. Lineup terms shift the chance ratings, weighted by LINEUP_WEIGHT."""
    col = "lineup" if puck_drop else "roster"
    if col in df.columns:
        df = df.with_columns(xgd=pl.col("xgd") + LINEUP_WEIGHT * pl.col(col).fill_null(0.0))
    return m.predict_proba(df.select(FEATURES).to_numpy())[:, 1]


def explain(m: LogisticRegression, row: dict) -> list[dict]:
    """How each input moved the home team's chance, starting from two evenly matched teams on neutral ice.

    Steps are taken in a fixed order (home ice, chances, who has been playing, rest, then the change in tonight's
    lineup if the row has one), so they add up to the final figure: the morning figure is the sum before the
    lineup step."""
    names = {"home": "Home ice", "xgd": "Recent chances", "roster": "Who's been playing", "b2b": "Back-to-back",
             "lineup": "Tonight's lineup"}
    coef = dict(zip(FEATURES, m.coef_[0]))
    roster = row.get("roster") or 0.0
    parts = [("home", m.intercept_[0]), ("xgd", coef["xgd"] * row["xgd"])]
    if row.get("roster") is not None:
        parts.append(("roster", coef["xgd"] * LINEUP_WEIGHT * roster))
    parts.append(("b2b", coef["b2b"] * row["b2b"]))
    if row.get("lineup") is not None:
        parts.append(("lineup", coef["xgd"] * LINEUP_WEIGHT * (row["lineup"] - roster)))
    z, steps, before = 0.0, [], 0.5
    for k, v in parts:
        z += v
        after = 1 / (1 + math.exp(-z))
        steps.append({"input": k, "label": names[k], "shift": after - before})
        before = after
    return steps


def score(p: np.ndarray, y: np.ndarray) -> dict:
    """Hit rate, Brier score, log loss and a calibration table (chance given to the picked team against how often it won)."""
    p = np.clip(p, 1e-6, 1 - 1e-6)
    pick = p >= 0.5
    fav = np.where(pick, p, 1 - p)  # chance given to the team we would pick
    won = np.where(pick, y, 1 - y)
    bands = []
    for lo, hi in ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)):
        sel = (fav >= lo) & (fav < hi)
        if sel.sum():
            bands.append({"band": f"{lo:.0%}-{hi:.0%}" if hi < 1 else f"{lo:.0%}+", "games": int(sel.sum()),
                          "said": float(fav[sel].mean()), "won": float(won[sel].mean())})
    return {"games": int(len(y)), "hit_rate": float(won.mean()), "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), "calibration": bands}
