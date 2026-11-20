"""Power plays on the post-game page: which stretches count, and how they join."""
import polars as pl

from pipeline.export import power_plays as P


def _stints(rows):
    return pl.DataFrame([{"period": 1, "start": a, "end": b, "n_home": h, "n_away": w, "home_skaters": list(range(h)),
                          "away_skaters": list(range(10, 10 + w))} for a, b, h, w in rows])


def test_ahead_needs_a_short_side():
    assert P.ahead(5, 4) == 1 and P.ahead(3, 5) == 0
    assert P.ahead(6, 5) is None  # empty net at even strength is not a power play
    assert P.ahead(4, 4) is None
    assert P.ahead(6, 4) == 1  # power play with the goalie pulled


def test_five_on_three_into_five_on_four_is_one_power_play():
    pps = P.stretches(_stints([(0, 100, 5, 5), (100, 148, 5, 3), (148, 220, 5, 4), (220, 300, 5, 5), (300, 303, 4, 5), (303, 400, 5, 5)]))
    assert len(pps) == 1  # the 3-second blip is a line change
    pp = pps[0]
    assert (pp["side"], pp["start"], pp["end"]) == (1, 100, 220)
    assert [s[2:4] for s in pp["stints"]] == [(5, 3), (5, 4)]


def test_infraction_words():
    assert P.infraction("too-many-men-on-the-ice") == "Too many men"
    assert P.infraction("hooking") == "Hooking"
