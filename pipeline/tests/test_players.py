"""Player pages: shot areas, combining EDGE seasons, and the per-game table against raw event counts."""
import polars as pl
import pytest

from pipeline.config import TABLES
from pipeline.export.players import AREAS, _combine_edge, area


def test_areas_from_the_shooters_side():
    assert area(86, 0) == "crease"
    assert area(75, 5) == "lowSlot"
    assert area(75, 15) == "netSideL" and area(75, -15) == "netSideR"  # positive y is the shooter's left
    assert area(60, 25) == "circleL" and area(80, -30) == "circleR"
    assert area(60, 0) == "highSlot" and area(50, 0) == "highSlot"
    assert area(95, 0) == "behind" and area(95, 30) == "cornerL" and area(75, -40) == "cornerR"
    assert area(50, 30) == "outerL" and area(60, -40) == "outerR"
    assert area(35, 0) == "point" and area(35, 30) == "pointL" and area(35, -30) == "pointR"
    assert area(10, 0) == "outside"
    assert {area(x, y) for x in range(-20, 100, 3) for y in range(-42, 43, 3)} == set(AREAS)


def test_edge_blend_takes_bests_and_pools_rates():
    now = {"topSpeed": 22.0, "bursts": 10, "shotSpeed": 80.0, "distance": 30.0, "oz": 0.5, "games": 10, "hours": 3.0}
    last = {"topSpeed": 23.0, "bursts": 50, "shotSpeed": None, "distance": 150.0, "oz": 0.4, "games": 40, "hours": 12.0}
    b = _combine_edge(now, last, "blend")
    assert b["topSpeed"] == 23.0 and b["shotSpeed"] == 80.0
    assert b["bursts"] == pytest.approx(60 / 15) and b["distance"] == pytest.approx(180 / 15)
    assert b["oz"] == pytest.approx(100 * (0.5 * 10 + 0.4 * 40) / 50)
    assert _combine_edge(now, last, "season")["topSpeed"] == 22.0
    assert _combine_edge(None, None, "blend") is None


@pytest.mark.skipif(not (TABLES / "2025" / "player_game.parquet").exists(), reason="needs the built 2025 tables")
def test_player_game_matches_raw_counts():
    d = TABLES / "2025"
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == 2)["game_id"].to_list()
    ev = pl.read_parquet(d / "events.parquet").filter(pl.col("game_id").is_in(games))
    pg = pl.read_parquet(d / "player_game.parquet")
    assert pg["hits"].sum() == ev.filter((pl.col("type") == "hit") & pl.col("p1").is_not_null()).height
    assert pg["g"].sum() == ev.filter((pl.col("type") == "goal") & pl.col("p1").is_not_null()).height
    # Every skater's 5v5 time fits inside his team's.
    assert (pg["sec5"] <= pg["t_sec5"] + 1).all()
