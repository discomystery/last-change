"""Relevancy: the league's top is made of scorers and play-drivers, not ice-time eaters, and hurt players keep their standing."""
import polars as pl
import pytest

from pipeline.config import TABLES
from pipeline.metrics import relevancy

pytestmark = pytest.mark.skipif(not (TABLES / "2025" / "player_game.parquet").exists() or not (TABLES / "2024" / "player_game.parquet").exists(),
                                reason="needs the built 2024 and 2025 tables")


@pytest.fixture(scope="module")
def season_2025():
    return relevancy.run(2025)


def test_one_score_per_player_with_a_rank_on_his_team(season_2025):
    r = season_2025
    assert r["player_id"].n_unique() == r.height
    assert r.group_by("team_id").agg(pl.col("team_rank").min())["team_rank"].max() == 1
    assert r["pct"].min() >= 0 and r["pct"].max() <= 100


def test_top_of_the_league_is_scorers_not_minutes(season_2025):
    lines = relevancy.season_lines(2025).filter(pl.col("gp") >= 40)
    r = season_2025.join(lines.select("player_id", "pts"), on="player_id")
    top = r.sort("score", descending=True).head(30)
    rest = r.filter(~pl.col("player_id").is_in(top["player_id"].to_list()))
    assert top["pts"].median() > 1.6 * rest["pts"].median()


def test_with_without_never_leans_mostly_on_the_gap(season_2025):
    ww = relevancy.with_without(2025, season_2025)
    assert ww.height > 100
    assert ww["weight_on_gap"].max() < 0.5


def test_goalies_have_one_franchise_group_of_five():
    g = relevancy.goalies(2025)
    assert g["player_id"].n_unique() == g.height
    assert g.filter(pl.col("tier") == "franchise").height == 5
    assert g.filter(pl.col("tier") == "franchise")["share"].min() > 0.4  # all regular starters
