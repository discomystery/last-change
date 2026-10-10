"""Pre-game win probability (kept off the site before puck drop).

The site never shows a pre-game number: the probability only steers preview wording, and the post-game page may
reveal it with its breakdown. Every input is built from games played strictly before the game's date, so the same
figure can be recomputed after the game without leaking the result.

Inputs, per team, as a decaying average over its recent games (older games count less; last season's games count
for less again; a few phantom league-average games pull small samples toward the middle):
  - xgd: expected-goal difference per game, all situations, empty nets excluded (how well a team controls chances)
  - rest: playing on the second night of a back-to-back
That is the base figure, known the morning of the game. At puck drop the lineup is known too, and the puck-drop
figure adds to each team's chance rating:
  - lineup: tonight's dressed skaters against the team's last ten lineups, each skater valued by his isolated 5v5
    impact (rapm.py, fitted on earlier seasons only) times his usual 5v5 ice time, in expected goals per game.
    It is in the same units as xgd, so it shifts that rating and needs no weight of its own. Over 2024-25 and
    2025-26 it improved log loss by 0.0012 (80% range 0.0001-0.0023): small, because most nights a team dresses
    its usual players.
Finishing and goaltending (goals minus expected goals, for the team or for the starting goalie) were tested and
dropped: over 2023-25 their weight came out at zero. A logistic regression on the home-minus-away gaps (plus home
ice) turns them into a home win probability.
Shootout and overtime results count as wins and losses like any other.
"""
import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from pipeline.config import FINAL_STATES, REGULAR, TABLES

FEATURES = ["xgd", "b2b"]
LINEUP_WINDOW = 10  # a team's usual lineup is the average of its last this-many
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


def lineups(seasons: list[int]) -> pl.DataFrame:
    """Per (game, team): tonight's lineup value minus the team's usual, in expected goals per game.

    A skater's value is his offense plus defense impact per 60 from the seasons before this one (none for rookies),
    times his share of the night's 5v5 time judged from his earlier games. Only earlier games feed both."""
    from pipeline.metrics import rapm
    from pipeline.metrics.players import RAPM_LAMBDA

    have = [s for s in range(min(seasons) - 2, max(seasons) + 1) if (TABLES / str(s) / "player_game.parquet").exists()]
    rate = {}
    for s in seasons:
        prior = [x for x in (s - 2, s - 1) if x in have]
        r = rapm.run(prior, RAPM_LAMBDA) if prior else pl.DataFrame({"player_id": [], "off": [], "def": []})
        rate[s] = dict(zip(r["player_id"].to_list(), (r["off"] + r["def"]).to_list()))
    pg = pl.concat([
        pl.read_parquet(TABLES / str(s) / "player_game.parquet").filter(pl.col("pos") != "G").select("game_id", "player_id", "team_id", "pos", "sec5")
        .join(pl.read_parquet(TABLES / str(s) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date"), on="game_id")
        .with_columns(season=pl.lit(s)) for s in have]).sort("date", "game_id")
    default = {d: float(pg.filter((pl.col("pos") == "D") == d)["sec5"].mean()) for d in (True, False)}
    tw: dict[int, float] = defaultdict(float)  # decaying ice-time history per skater
    tx: dict[int, float] = defaultdict(float)
    past: dict[int, list[float]] = defaultdict(list)
    rows = []
    for day in pg.partition_by("date", maintain_order=True):
        r = rate.get(day["season"][0], {})
        today = []
        for (gid, tid), grp in day.group_by(["game_id", "team_id"], maintain_order=True):
            ids = grp["player_id"].to_list()
            usual = np.array([tx[p] / tw[p] if tw[p] else default[pos == "D"] for p, pos in zip(ids, grp["pos"].to_list())])
            share = usual / usual.sum() * SEC5_PER_GAME
            value = float(sum(r.get(p, 0.0) * sec / 3600 for p, sec in zip(ids, share)))
            h = past[tid][-LINEUP_WINDOW:]
            rows.append({"game_id": gid, "team_id": tid, "lineup": value - (float(np.mean(h)) if h else value)})
            today.append((tid, value))
        for tid, value in today:
            past[tid].append(value)
        for p, sec in zip(day["player_id"].to_list(), day["sec5"].to_list()):
            tw[p] = tw[p] * 0.9 + 1
            tx[p] = tx[p] * 0.9 + sec
    return pl.DataFrame(rows).filter(pl.col("game_id") // 1_000_000 % 10_000 >= min(seasons))


def with_lineups(df: pl.DataFrame, lineup: pl.DataFrame) -> pl.DataFrame:
    h = lineup.rename({"team_id": "home_id", "lineup": "h_lineup"})
    a = lineup.rename({"team_id": "away_id", "lineup": "a_lineup"})
    return (df.join(h, on=["game_id", "home_id"], how="left").join(a, on=["game_id", "away_id"], how="left")
            .with_columns(lineup=(pl.col("h_lineup") - pl.col("a_lineup")).fill_null(0.0)))


def fit(train: pl.DataFrame) -> LogisticRegression:
    m = LogisticRegression(C=1.0)
    m.fit(train.select(FEATURES).to_numpy(), train["home_win"].to_numpy())
    return m


def predict(m: LogisticRegression, df: pl.DataFrame, puck_drop: bool = False) -> np.ndarray:
    """Home win chance: the morning figure, or at puck drop with tonight's lineup added to the chance ratings."""
    if puck_drop:
        df = df.with_columns(xgd=pl.col("xgd") + pl.col("lineup"))
    return m.predict_proba(df.select(FEATURES).to_numpy())[:, 1]


def explain(m: LogisticRegression, row: dict) -> list[dict]:
    """How each input moved the home team's chance, starting from two evenly matched teams on neutral ice.

    Steps are taken in a fixed order (home ice, chances, rest, then tonight's lineup if the row has one), so they
    add up to the final figure: the morning figure is the sum before the lineup step."""
    names = {"home": "Home ice", "xgd": "Control of chances", "b2b": "Back-to-back", "lineup": "Tonight's lineup"}
    coef = dict(zip(FEATURES, m.coef_[0]))
    parts = [("home", m.intercept_[0])] + [(f, coef[f] * row[f]) for f in FEATURES]
    if row.get("lineup") is not None:
        parts.append(("lineup", coef["xgd"] * row["lineup"]))
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
