"""Isolated impact: recovers planted effects from made-up shifts, and its stretches add up to the team totals."""
import numpy as np
import polars as pl
import pytest

from pipeline.config import TABLES
from pipeline.metrics import rapm


def test_finds_a_planted_scorer_and_a_planted_stopper():
    rng = np.random.default_rng(1)
    home_pool, away_pool = list(range(1, 21)), list(range(101, 121))
    rows = []
    for _ in range(6000):
        hs, as_ = sorted(rng.choice(home_pool, 5, replace=False).tolist()), sorted(rng.choice(away_pool, 5, replace=False).tolist())
        dur = int(rng.integers(20, 60))
        rate_h = 2.5 + (1.5 if 1 in hs else 0.0) - (1.5 if 101 in as_ else 0.0)  # player 1 creates, player 101 shuts down
        rate_a = 2.5
        rows.append({"game_id": 1, "home_skaters": hs, "away_skaters": as_, "duration": dur,
                     "xg_home": rng.poisson(rate_h * dur / 3600 * 10) / 10, "xg_away": rng.poisson(rate_a * dur / 3600 * 10) / 10,
                     "home_state": 0, "home_zone": "F"})
    X, y, w, players, _ = rapm.design(pl.DataFrame(rows))
    m = rapm.fit(X, y, w, 2000.0, len(players))
    n = len(players)
    off, dfn = dict(zip(players, m["beta"][:n])), dict(zip(players, -m["beta"][n:2 * n]))
    assert max(off, key=off.get) == 1
    assert max(dfn, key=dfn.get) == 101
    assert np.all(m["sd"] > 0)


@pytest.mark.skipif(not (TABLES / "2025" / "team_game.parquet").exists(), reason="needs the built 2025 tables")
def test_stretches_hold_all_5v5_expected_goals():
    st = rapm.stretches(2025)
    games = pl.read_parquet(TABLES / "2025" / "games.parquet").filter(pl.col("game_type") == 2).select("game_id")
    tg = pl.read_parquet(TABLES / "2025" / "team_game.parquet").filter(pl.col("strength") == "5v5").join(games, on="game_id")
    assert st["xg_home"].sum() + st["xg_away"].sum() == pytest.approx(tg["xgf"].sum(), rel=1e-6)
