"""Run it back: how often each team would have won a game, given the chances both teams actually got.

Similar in spirit to MoneyPuck's game simulations, with our own xG and rules. Every unblocked shot is a coin flip that comes up "goal" with the
probability our xG model gives it; replaying the game's shots many times gives a share of replays each team wins.
Instead of simulating, the exact answer is computed: each team's goal total is a sum of independent coin flips
(a Poisson-binomial distribution), and the two totals are compared directly. The result is the same every run.

Choices, each kept deliberately simple for the prototype:
- Regulation only (periods 1-3). A replay that ends level counts as half a win for each team, because 3-on-3
  overtime and shootouts are close to coin flips; the tie share is reported separately.
- Shots at an empty net are left out: they say more about the score than about who played better.
- Penalty shots count (a real chance a team earned). Shootout attempts never count.
- Rebound flurries: a shot and the rebound off it cannot both go in (the first goal stops play). Each flurry
  (an unblocked shot within 3 seconds of a teammate's shot on goal, chained) becomes one coin flip with
  probability 1 - (chance every shot in it is stopped). Without this, teams that generate scrambles look like
  they earned more goals than were possible.
- Shooter and goalie skill are not in it: an xG rating assumes a league-average shooter and goalie.
- Optional score adjustment (`adjust_score=True`) weights each shot by the league's score-and-venue weight, so a
  trailing team's chase is discounted. It barely changes anything (see below), so it is off by default.

How to read it (2023-24 to 2025-26 regular seasons, 3,936 games): the share means "won this many of 100 replays",
not "won this often in real life". Teams the meter gave 80%+ won 68% of the time. The code is right (replaying
games with goals drawn from the same xG lands within a point or two of the meter in every band); the gap is
hockey: a team that scores early on few chances tends to sit back while the other team piles up chances chasing.
Its value is as a record of who carried play: a team's replay wins over half a season predict its wins in the
other half far better than its real wins do (r 0.67 against 0.50), about as well as plain xG share.
"""
import numpy as np
import polars as pl

from pipeline.config import TABLES
from pipeline.metrics import adjust

UNBLOCKED = ("shot-on-goal", "missed-shot", "goal")
REBOUND_GAP = 3  # seconds; the same rebound rule team_game uses


def shots(season: int) -> pl.DataFrame:
    """Every regulation unblocked shot at a goalie with its xG, tagged with the flurry it belongs to."""
    d = TABLES / str(season)
    events = pl.read_parquet(d / "events.parquet").select("game_id", "event_id", "sort", "period", "period_type")
    feats = pl.read_parquet(d / "shot_features.parquet").select(
        "game_id", "event_id", "type", "is_home", "empty_net", "score_diff",
        ((pl.col("prev_type") == "shot-on-goal") & pl.col("prev_same_team") & (pl.col("prev_gap") <= REBOUND_GAP)).alias("rebound"))
    xg = pl.read_parquet(d / "shots_xg_own.parquet")
    s = (
        feats.filter(pl.col("type").is_in(UNBLOCKED))
        .join(xg, on=["game_id", "event_id"])
        .join(events, on=["game_id", "event_id"])
        .filter((pl.col("period") <= 3) & (pl.col("period_type") != "SO"))
        .sort("game_id", "sort")
    )
    # A flurry starts at any shot that is not a rebound; rebounds join the flurry of the shot before them. The
    # empty-net filter comes after chaining so a flurry is never split by it (a goalie can't vanish mid-scramble).
    s = s.with_columns((~pl.col("rebound")).cum_sum().over("game_id").alias("flurry"))
    return s.filter(~pl.col("empty_net"))


def goal_pmf(p: np.ndarray) -> np.ndarray:
    """Probability of 0, 1, 2, ... goals from independent chances with scoring probabilities p."""
    pmf = np.zeros(len(p) + 1)
    pmf[0] = 1.0
    for i, q in enumerate(p):
        pmf[1:i + 2] = pmf[1:i + 2] * (1 - q) + pmf[:i + 1] * q
        pmf[0] *= 1 - q
    return pmf


def outcome(home: np.ndarray, away: np.ndarray) -> tuple[float, float, float]:
    """(home wins, level, away wins) in regulation, from each team's chance probabilities."""
    h, a = goal_pmf(home), goal_pmf(away)
    joint = np.outer(h, a)  # joint[i, j] = P(home scores i, away scores j)
    win, tie = np.tril(joint, -1).sum(), np.trace(joint)
    return float(win), float(tie), float(max(0.0, 1.0 - win - tie))


def score_adjusted(s: pl.DataFrame) -> pl.DataFrame:
    """Weight each shot's xG by the league's score-and-venue weight for its team's score state (5v5 xG weights)."""
    w = adjust.weights().filter(pl.col("metric") == "xgf").select("home_state", "w_home", "w_away")
    return (s.with_columns(pl.when(pl.col("is_home")).then(pl.col("score_diff")).otherwise(-pl.col("score_diff")).alias("home_state"))
            .join(w, on="home_state", how="left")
            .with_columns((pl.col("xg") * pl.when(pl.col("is_home")).then(pl.col("w_home")).otherwise(pl.col("w_away"))).clip(0, 0.99).alias("xg"))
            .drop("home_state", "w_home", "w_away"))


def chances(s: pl.DataFrame, collapse_flurries: bool = True) -> pl.DataFrame:
    """One row per scoring chance (a flurry, or every shot on its own) with its probability of producing a goal."""
    if not collapse_flurries:
        return s.select("game_id", "is_home", pl.col("xg").alias("p"))
    return s.group_by("game_id", "flurry", "is_home").agg((1 - (1 - pl.col("xg")).product()).alias("p"))


def season_table(season: int, game_type: int | None = 2, collapse_flurries: bool = True, adjust_score: bool = False) -> pl.DataFrame:
    """One row per finished game: replay shares, expected goals and the real result."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("state").is_in(["OFF", "FINAL"]))
    if game_type is not None:
        games = games.filter(pl.col("game_type") == game_type)
    s = shots(season)
    c = chances(score_adjusted(s) if adjust_score else s, collapse_flurries)
    by_game = {
        (gid, home): np.asarray(p)
        for gid, home, p in c.group_by("game_id", "is_home").agg("p").iter_rows()
    }
    # Regulation goals actually scored at a goalie: the result the replays should be compared with.
    real = {(gid, home): n for gid, home, n in s.filter(pl.col("type") == "goal").group_by("game_id", "is_home").len().iter_rows()}
    rows = []
    for g in games.iter_rows(named=True):
        gid = g["game_id"]
        hp, ap = by_game.get((gid, True), np.zeros(0)), by_game.get((gid, False), np.zeros(0))
        win, tie, loss = outcome(hp, ap)
        rows.append({
            "game_id": gid, "date": g["date"], "home": g["home"], "away": g["away"],
            "home_score": g["home_score"], "away_score": g["away_score"], "last_period": g["last_period"],
            # Expected goals after flurries are collapsed (a flurry counts once, at its combined chance).
            "home_xg": float(hp.sum()), "away_xg": float(ap.sum()), "home_chances": int(hp.size), "away_chances": int(ap.size),
            "home_reg_win": win, "reg_tie": tie, "away_reg_win": loss,
            "home_share": win + tie / 2, "away_share": loss + tie / 2,
            "home_won": g["home_score"] > g["away_score"],
            "real_home_goals": real.get((gid, True), 0), "real_away_goals": real.get((gid, False), 0),
        })
    return pl.DataFrame(rows)


def team_season(table: pl.DataFrame) -> pl.DataFrame:
    """Per team: games, real wins and replay wins (the sum of its replay shares)."""
    side = lambda home: table.select(
        pl.col("home" if home else "away").alias("team"),
        pl.col("home_share" if home else "away_share").alias("share"),
        (pl.col("home_won") == home).alias("won"))
    return (pl.concat([side(True), side(False)]).group_by("team")
            .agg(pl.len().alias("gp"), pl.col("won").sum().alias("wins"), pl.col("share").sum().alias("replay_wins"))
            .with_columns((pl.col("wins") - pl.col("replay_wins")).alias("luck")).sort("luck", descending=True))


def build(season: int) -> dict:
    """Write replays.parquet for a season's finished regular-season games."""
    t = season_table(season)
    t.write_parquet(TABLES / str(season) / "replays.parquet")
    favourite_won = t.filter(pl.col("home_share") != 0.5).select(((pl.col("home_share") > 0.5) == pl.col("home_won")).mean()).item()
    return {"season": season, "games": t.height, "replay_favourite_won_pct": round(100 * favourite_won, 1) if t.height else None}
