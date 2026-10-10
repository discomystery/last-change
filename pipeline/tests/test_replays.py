"""Run it back: exact replay maths agrees with brute-force simulation, and flurries count once."""
import numpy as np
import polars as pl
import pytest

from pipeline.config import TABLES
from pipeline.metrics import replays


def test_goal_distribution_matches_simulation():
    rng = np.random.default_rng(3)
    home, away = rng.uniform(0.01, 0.3, 30), rng.uniform(0.01, 0.3, 25)
    win, tie, loss = replays.outcome(home, away)
    assert win + tie + loss == pytest.approx(1.0)
    n = 200_000
    h = (rng.random((n, home.size)) < home).sum(1)
    a = (rng.random((n, away.size)) < away).sum(1)
    assert win == pytest.approx((h > a).mean(), abs=0.005)
    assert tie == pytest.approx((h == a).mean(), abs=0.005)


def test_simple_cases():
    assert replays.outcome(np.array([1.0]), np.zeros(0)) == pytest.approx((1.0, 0.0, 0.0))
    assert replays.outcome(np.zeros(0), np.zeros(0)) == pytest.approx((0.0, 1.0, 0.0))
    assert replays.goal_pmf(np.array([0.5, 0.5])) == pytest.approx([0.25, 0.5, 0.25])


def test_a_flurry_is_one_chance():
    s = pl.DataFrame({"game_id": [1, 1, 1], "flurry": [1, 1, 2], "is_home": [True, True, True], "xg": [0.2, 0.5, 0.1]})
    c = replays.chances(s).sort("flurry")
    assert c["p"].to_list() == pytest.approx([1 - 0.8 * 0.5, 0.1])
    assert replays.chances(s, collapse_flurries=False).height == 3


@pytest.mark.skipif(not (TABLES / "2025" / "shot_features.parquet").exists(), reason="needs the data branch in data/")
def test_real_season_is_sane():
    t = replays.season_table(2025)
    assert t.height > 1000
    assert ((t["home_share"] + t["away_share"]) - 1).abs().max() < 1e-9
    # Flurry-collapsed xG sits just under the goals scored at a goalie in regulation.
    ratio = (t["home_xg"] + t["away_xg"]).sum() / (t["real_home_goals"] + t["real_away_goals"]).sum()
    assert 0.9 < ratio < 1.05
    assert t.filter(pl.col("home_share") != 0.5).select(((pl.col("home_share") > 0.5) == pl.col("home_won")).mean()).item() > 0.55
