"""Post-game report cards: the grading rules and the 'typical night' bar."""
import json
import math

import pytest

from pipeline.config import SITE_DATA
from pipeline.export.jobs import binom_tail, grade, poisson_median, poisson_tail, summary


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
    assert summary(["held", "held", "partly"]) == "did"
    assert summary(["held", "missed"]) == "mixed"
    assert summary(["held", "missed", "missed"]) == "didnt"
    assert summary(["partly", "partly"]) == "mixed"


@pytest.mark.skipif(not (SITE_DATA / "jobs").exists(), reason="needs exported report cards")
def test_exported_cards_are_well_formed():
    for p in (SITE_DATA / "jobs").glob("*.json"):
        d = json.loads(p.read_text())
        assert {c["team"] for c in d["players"]} <= {d["away"], d["home"]}
        for c in d["players"]:
            assert 1 <= len(c["jobs"]) <= 3
            for j in c["jobs"]:
                assert j["verdict"] in ("held", "partly", "missed", "na") and j["text"]
                assert "deserve" not in j["text"].lower()
        assert len(d["stood_out"]) <= 5
