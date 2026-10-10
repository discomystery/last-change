"""Post-game report cards: the grading rules and the 'typical night' bar."""
import json
import math

import pytest

from pipeline.config import SITE_DATA
from pipeline.config import RAW, TABLES
from pipeline.export.jobs import GRID, RARE_GOAL, binom_tail, box_extras, grade, poisson_median, poisson_tail, scoresheet, scoring_odds, summary


def test_typical_night_is_the_median_count():
    assert poisson_median(0.6) == 0  # most nights he has none
    assert poisson_median(1.2) == 1  # an average of 1.2 is not a bar of 2
    assert poisson_median(3.0) == 3
    for lam in (0.3, 0.9, 1.7, 4.2, 8.0):
        k = poisson_median(lam)
        at_least = poisson_tail(k, lam)
        assert at_least >= 0.5  # he reaches a typical night at least half the time
        assert poisson_tail(k + 1, lam) < 0.5


def test_tails():
    assert poisson_tail(0, 2.0) == 1.0
    assert poisson_tail(1, 2.0) == pytest.approx(1 - math.exp(-2))
    assert binom_tail(0, 10, 0.5) == pytest.approx(1.0)
    assert binom_tail(10, 10, 0.5) == pytest.approx(0.5 ** 10)


def test_grades_like_preview_calls():
    assert grade(3, 2, 1) == "held" and grade(1.5, 2, 1) == "partly" and grade(0.5, 2, 1) == "missed"
    # Lower is better for chances allowed.
    assert grade(2.0, 2.5, 3.0, higher=False) == "held" and grade(2.8, 2.5, 3.0, higher=False) == "partly" and grade(4.0, 2.5, 3.0, higher=False) == "missed"


def test_summary():
    assert summary([]) is None
    assert summary([("hits", "held"), ("shooting", "held"), ("blocks", "partly")]) == "did"
    assert summary([("hits", "held"), ("shooting", "missed")]) == "mixed"
    assert summary([("hits", "held"), ("shooting", "missed"), ("blocks", "missed")]) == "didnt"
    assert summary([("hits", "partly"), ("shooting", "na")]) == "mixed"
    # power play and penalty kill results are shared by the unit, so they count half
    assert summary([("pp", "missed"), ("pk", "missed"), ("shooting", "held")]) == "mixed"
    assert summary([("pp", "held"), ("pk", "held"), ("shooting", "missed")]) == "mixed"


def test_scoring_odds():
    # a defenseman with 3 goals in 80 games rarely scores; a 40-goal pace forward does
    assert scoring_odds(3, 80, 0.08) < RARE_GOAL
    assert scoring_odds(40, 80, 0.25) > RARE_GOAL
    # two goals in two games is mostly the position average, not a sniper
    assert scoring_odds(2, 2, 0.08) < RARE_GOAL


@pytest.mark.skipif(not (SITE_DATA / "jobs").exists(), reason="needs exported report cards")
def test_exported_cards_are_well_formed():
    for p in (SITE_DATA / "jobs").glob("*.json"):
        d = json.loads(p.read_text())
        assert {c["team"] for c in d["players"]} <= {d["away"], d["home"]}
        for c in d["players"]:
            assert len(c["jobs"]) <= 3 and c["pill"] in ("huge", "big", "did", "mixed", "didnt")
            for j in c["jobs"]:
                assert j["verdict"] in ("held", "partly", "missed", "na") and j["text"]
                assert "deserve" not in j["text"].lower()
        assert len(d["stood_out"]) <= 5


def test_scoresheet_levels():
    box = lambda g=0, a=0, pm=0, sh=0, pp=0: {"g": g, "a": a, "pm": pm, "sh": sh, "pp": pp, "ev": g - sh - pp}
    assert scoresheet(box(a=2, pm=1), None) == ("big", "2 assists")
    assert scoresheet(box(g=3, pm=2, sh=1, pp=1), 1.0)[0] == "huge"
    assert "one shorthanded, one on the power play, one at even strength" in scoresheet(box(g=3, pm=2, sh=1, pp=1), 1.0)[1]
    assert scoresheet(box(a=4), None)[0] == "huge"
    # a shorthanded goal counts as two points
    assert scoresheet(box(g=1, sh=1), None)[0] == "big"
    # a goal matters more from someone who rarely scores
    assert scoresheet(box(g=1), None, scoring_odds=0.09)[0] == "good"
    assert scoresheet(box(g=1), None, scoring_odds=0.35) == (None, None)
    # plus-minus never sets the level
    assert scoresheet(box(pm=4), 0.9) == (None, None) and scoresheet(box(pm=-4), 0.1) == (None, None)
    assert scoresheet(box(a=1, pm=2), 0.9) == (None, None)


def test_grid_never_fully_bails_out_missed_jobs():
    assert GRID["huge"]["didnt"] == "big" and GRID["big"]["didnt"] == "mixed" and GRID["good"]["didnt"] == "mixed"
    assert GRID["huge"]["mixed"] == "huge" and GRID["good"]["did"] == "big"
    assert all(GRID[None][s] == (s if s else None) for s in ("did", "mixed", "didnt", None))


@pytest.mark.skipif(not (RAW / "2026" / "box").exists() or not (TABLES / "2026" / "events.parquet").exists(), reason="needs 2026 raw boxscores")
def test_plus_minus_comes_from_the_nhl_boxscore():
    import gzip
    pm = {k: v["pm"] for k, v in box_extras(2026).items()}
    checked = 0
    for f in sorted((RAW / "2026" / "box").glob("*.json.gz")):
        d = json.loads(gzip.decompress(f.read_bytes()))
        if d.get("gameType") != 2 or "playerByGameStats" not in d:
            continue
        for side in ("awayTeam", "homeTeam"):
            for grp in ("forwards", "defense"):
                for p in d["playerByGameStats"][side][grp]:
                    assert pm.get((d["id"], p["playerId"]), 0) == p["plusMinus"], (d["id"], p["playerId"])
                    checked += 1
    assert checked > 100
