"""In-house expected-goals model: gradient-boosted trees on play-by-play features.

Trained on unblocked shots (the only ones that can score). One model for every season, so seasons can be compared.
"""
import pickle

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, roc_auc_score

from pipeline.config import DATA, TABLES

NUMERIC = ["dist", "angle", "x", "y_abs", "own_skaters", "opp_skaters", "score_diff", "period", "prev_gap", "prev_dist", "prev_x", "prev_angle_change"]
FLAGS = ["empty_net", "prev_same_team", "is_home"]
CATEGORICAL = ["shot_type", "prev_type"]
MODEL_PATH = DATA / "models" / "xg_v1.pkl"


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(TABLES / str(season) / "shot_features.parquet").filter(pl.col("type") != "blocked-shot")


def _design(df: pl.DataFrame, levels: dict[str, list[str]]) -> np.ndarray:
    cols = [df[c].cast(pl.Float64).to_numpy() for c in NUMERIC + FLAGS]
    for c in CATEGORICAL:
        index = {v: i for i, v in enumerate(levels[c])}
        cols.append(np.array([index.get(v, len(index)) for v in df[c].to_list()], dtype=float))
    return np.column_stack(cols)


def fit(train: pl.DataFrame) -> dict:
    levels = {c: sorted(train[c].unique().to_list()) for c in CATEGORICAL}
    n = len(NUMERIC) + len(FLAGS)
    model = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0,
                                           categorical_features=list(range(n, n + len(CATEGORICAL))), random_state=7)
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
    bundle = fit(pl.concat([load(s) for s in seasons]))
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_bytes(pickle.dumps(bundle))
    return bundle


def score_season(bundle: dict, season: int) -> int:
    df = load(season)
    df.select("game_id", "event_id").with_columns(xg=pl.Series(predict(bundle, df))).write_parquet(TABLES / str(season) / "shots_xg_own.parquet")
    return df.height
