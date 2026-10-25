"""Wins above replacement: parts are centered on an average minute, the pieces add up, and the range holds the value."""
import pytest

from pipeline.config import CURRENT_SEASON, TABLES
from pipeline.metrics import war

needs_data = pytest.mark.skipif(not (TABLES / str(CURRENT_SEASON - 1) / "player_game.parquet").exists(), reason="no local tables")


@needs_data
def test_parts_are_centered_at_each_position():
    t = war.parts(CURRENT_SEASON - 1)
    for g in ("F", "D"):
        sub = t.filter(t["grp"] == g)
        for c in ("ev", "ppv", "pen"):
            assert abs(sub[c].sum()) < 1e-6


@needs_data
def test_pieces_add_up_and_ranges_hold_the_value():
    x = war.pyth_x()
    assert 1.8 < x < 2.4
    rows = war.season_war(CURRENT_SEASON - 1, x)
    for r in rows.values():
        assert r["ev"] + r["pp"] + r["pen"] + r["fill"] == pytest.approx(r["war"], abs=0.03)
        assert r["lo"] <= r["war"] <= r["hi"]
        assert r["fill"] >= 0  # a fill-in is below average, so everyone gets that much back
        assert 1 <= r["rank"] <= r["of"]
