"""Post-game Impact: goal credit adds up, parts add up to the score, and every finished game gets a file."""
import json

import polars as pl
import pytest

from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import game_scores

needs_data = pytest.mark.skipif(not (TABLES / str(CURRENT_SEASON) / "events.parquet").exists(), reason="no local tables")


@needs_data
def test_each_goal_hands_out_one_goal_of_credit():
    credit, goalies = game_scores.shot_credit(CURRENT_SEASON)
    games = pl.read_parquet(TABLES / str(CURRENT_SEASON) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
    ev = pl.read_parquet(TABLES / str(CURRENT_SEASON) / "events.parquet").join(games, on="game_id")
    n_goals = ev.filter((pl.col("type") == "goal") & (pl.col("period_type") != "SO") & pl.col("p1").is_not_null()).height
    assert credit["credit"].sum() == pytest.approx(n_goals)
    assert (goalies["ga"] <= goalies["shots"]).all()


def test_goal_split_favours_the_scorer():
    g, a1, a2 = game_scores.SPLIT
    assert g + a1 + a2 == pytest.approx(1.0) and g > a1 > a2


@needs_data
def test_parts_add_up_and_percentiles_are_in_range():
    files = list((SITE_DATA / "scores").glob("*.json"))
    if not files:
        pytest.skip("scores not exported")
    for f in files:
        d = json.loads(f.read_text())
        assert d["players"], f
        for p in d["players"]:
            assert sum(p["parts"].values()) == pytest.approx(p["score"], abs=0.03)
            assert p["pct"] is None or 1 <= p["pct"] <= 99
        scores = [p["score"] for p in d["players"]]
        assert scores == sorted(scores, reverse=True)
