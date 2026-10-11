"""In-house expected-goals model: gradient-boosted trees on play-by-play features.

Trained on unblocked shots (the only ones that can score). One model for every season, so seasons can be compared.
"""
import pickle

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, roc_auc_score

from pipeline.config import DATA, TABLES

# v2 (2026-10-11): adds side-to-side movement since the last play, its speed, time since the faceoff, game time, the
# rebound and penalty-shot flags, and trains every tree instead of stopping early (early stopping left the model too
# timid: shots it rated 20-30% scored 26%, 60%+ scored 81%). Unseen-season log loss 0.21718 -> 0.2164 (2025-26).
# Then how long the defenders and shooters have been on the ice (from the stints, strictly before the shot; shots against
# defenders 15 s into a shift scored about a fifth less than the model expected, 60 s+ a little more), and unusual moments:
# a delayed penalty (goalie pulled for an extra attacker, scored ~45% above expectation), the shooter's own net empty,
# seconds since the manpower changed and time left on the penalty. Log loss 2025 0.2166 -> 0.2159, 2024 0.2102 -> 0.2097,
# 2023 0.2113 -> 0.2109. Tested and not kept: arena distance offsets (recording is now within about a foot everywhere) and
# the play two back.
NUMERIC = ["dist", "angle", "x", "y_abs", "own_skaters", "opp_skaters", "score_diff", "period", "prev_gap", "prev_dist", "prev_x", "prev_angle_change",
           "prev_dy", "prev_speed", "since_faceoff", "sec", "def_shift_avg", "def_shift_max", "att_shift_avg", "state_since", "pen_left"]
FLAGS = ["empty_net", "prev_same_team", "is_home", "rebound", "penalty_shot", "own_net_empty", "delayed_pen"]
CATEGORICAL = ["shot_type", "prev_type"]
MODEL_PATH = DATA / "models" / "xg_v2.pkl"
TRAIN_SEASONS = [2023, 2024, 2025]


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(TABLES / str(season) / "shot_features.parquet").filter(pl.col("type") != "blocked-shot").with_columns(
        prev_speed=pl.col("prev_dist") / pl.col("prev_gap").clip(1, 60),
        rebound=(pl.col("prev_type") == "shot-on-goal") & pl.col("prev_same_team") & (pl.col("prev_gap") <= 3),
        sec=pl.col("sec").clip(0, 3900))


def _design(df: pl.DataFrame, levels: dict[str, list[str]]) -> np.ndarray:
    cols = [df[c].cast(pl.Float64).to_numpy() for c in NUMERIC + FLAGS]
    for c in CATEGORICAL:
        index = {v: i for i, v in enumerate(levels[c])}
        cols.append(np.array([index.get(v, len(index)) for v in df[c].to_list()], dtype=float))
    return np.column_stack(cols)


def fit(train: pl.DataFrame) -> dict:
    levels = {c: sorted(train[c].unique().to_list()) for c in CATEGORICAL}
    n = len(NUMERIC) + len(FLAGS)
    model = HistGradientBoostingClassifier(max_iter=600, learning_rate=0.03, max_leaf_nodes=31, min_samples_leaf=100, l2_regularization=1.0,
                                           early_stopping=False, categorical_features=list(range(n, n + len(CATEGORICAL))), random_state=7)
    model.fit(_design(train, levels), train["goal"].to_numpy())
    return {"model": model, "levels": levels}


def predict(bundle: dict, df: pl.DataFrame) -> np.ndarray:
    return bundle["model"].predict_proba(_design(df, bundle["levels"]))[:, 1]


def evaluate(train_seasons: list[int], test_season: int) -> dict:
    """Train on some seasons, score a season the model has never seen, and compare with MoneyPuck on the same shots."""
    bundle = fit(pl.concat([load(s) for s in train_seasons]))
    test = load(test_season)
    p, y = predict(bundle, test), test["goal"].to_numpy()
    base = np.full(len(y), y.mean())
    out = {"test_shots": len(y), "goal_rate": round(float(y.mean()), 4), "log_loss": round(log_loss(y, p), 4), "log_loss_no_model": round(log_loss(y, base), 4),
           "auc": round(roc_auc_score(y, p), 4), "sum_xg": round(float(p.sum()), 1), "goals": int(y.sum())}
    order = np.argsort(p)
    out["calibration"] = [{"predicted": round(float(p[idx].mean()), 3), "actual": round(float(y[idx].mean()), 3), "shots": len(idx)} for idx in np.array_split(order, 10)]
    mp = pl.read_parquet(TABLES / str(test_season) / "shots_xg.parquet").select("game_id", "event_id", "xGoal")
    both = test.with_columns(ours=pl.Series(p)).join(mp, on=["game_id", "event_id"])
    yy = both["goal"].to_numpy()
    out["same_shots"] = {"n": both.height, "ours_log_loss": round(log_loss(yy, both["ours"].to_numpy()), 4), "moneypuck_log_loss": round(log_loss(yy, np.clip(both["xGoal"].to_numpy(), 1e-6, 1 - 1e-6)), 4),
                         "ours_auc": round(roc_auc_score(yy, both["ours"].to_numpy()), 4), "moneypuck_auc": round(roc_auc_score(yy, both["xGoal"].to_numpy()), 4),
                         "shot_level_correlation": round(float(np.corrcoef(both["ours"].to_numpy(), both["xGoal"].to_numpy())[0, 1]), 3)}
    return out


def train_final(seasons: list[int]) -> dict:
    from pipeline.build import shot_features

    for s in seasons:  # shot tables saved before a feature was added are rebuilt first
        if not set(NUMERIC + FLAGS) - {"prev_speed", "rebound"} <= set(pl.read_parquet_schema(TABLES / str(s) / "shot_features.parquet")):
            shot_features.build(s)
    bundle = fit(pl.concat([load(s) for s in seasons]))
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_bytes(pickle.dumps(bundle))
    return bundle


def saved() -> dict:
    """The site's model; trained from the saved seasons the first time a new version is needed (a few minutes)."""
    if not MODEL_PATH.exists():
        return train_final(TRAIN_SEASONS)
    return pickle.loads(MODEL_PATH.read_bytes())


def score_season(bundle: dict, season: int) -> int:
    df = load(season)
    df.select("game_id", "event_id").with_columns(xg=pl.Series(predict(bundle, df))).write_parquet(TABLES / str(season) / "shots_xg_own.parquet")
    return df.height
