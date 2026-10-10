"""Win probability: ratings only see earlier days, back-to-backs are caught, and the breakdown adds up."""
import numpy as np
import polars as pl

from pipeline.metrics import win_model


def _games(rows):
    return pl.DataFrame(rows, schema=["game_id", "season", "date", "home_id", "away_id", "home_score", "away_score", "h_xgd", "a_xgd"], orient="row")


def test_ratings_use_only_earlier_days():
    g = _games([
        (1, 2025, "2025-10-01", 1, 2, 5, 0, 3.0, -3.0),
        (2, 2025, "2025-10-01", 3, 4, 0, 1, 0.0, 0.0),
        (3, 2025, "2025-10-02", 1, 3, 2, 1, 9.0, -9.0),  # its own result must not feed its own rating
    ])
    df = win_model.pregame(g, win_model.Params(decay=1.0, carry=1.0, prior_games=1.0))
    first, third = df.row(0, named=True), df.row(2, named=True)
    assert first["h_xgd"] == 0 and first["a_xgd"] == 0
    assert third["h_xgd"] == 3.0 / 2  # one game of +3 plus one phantom average game
    assert third["h_b2b"] == 1 and third["a_b2b"] == 1 and first["h_b2b"] == 0


def test_explanation_adds_up_to_the_probability():
    rng = np.random.default_rng(0)
    df = pl.DataFrame({"xgd": rng.normal(0, 0.5, 2000), "b2b": rng.integers(-1, 2, 2000)})
    y = (rng.random(2000) < 1 / (1 + np.exp(-(0.15 + 0.8 * df["xgd"].to_numpy())))).astype(int)
    m = win_model.fit(df.with_columns(home_win=pl.Series(y)))
    row = {"xgd": 0.4, "b2b": -1}
    steps = win_model.explain(m, row)
    p = win_model.predict(m, pl.DataFrame([row]))[0]
    assert abs(0.5 + sum(s["shift"] for s in steps) - p) < 1e-9
    assert steps[0]["input"] == "home" and steps[0]["shift"] > 0


def test_score_on_a_coin_flip():
    s = win_model.score(np.full(4, 0.5), np.array([1, 0, 1, 0]))
    assert s["brier"] == 0.25 and abs(s["log_loss"] - np.log(2)) < 1e-9
