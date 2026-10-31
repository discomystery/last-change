"""Lineup-change notes say what the league reported, and nothing it did not."""
from pipeline.export import changes


def _row(**k):
    base = {"status": "injured", "report_status": None, "report_injury": None, "report_timeline": None, "games_missed": 0, "last_played": "2026-10-01"}
    return base | k


def test_reported_statuses_are_quoted_and_unreported_ones_are_not_guessed():
    assert changes._status_text(_row(report_status="ir", report_injury="lower body", games_missed=3), 5) == \
        "is on injured reserve (lower body injury) and has missed the last 3 games"
    assert changes._status_text(_row(report_status="long", report_timeline="out at least four months"), 5) == \
        "is out at least four months, reported after he played in their last game"
    unreported = changes._status_text(_row(status="off_roster", games_missed=2), 5)
    assert unreported == "has not dressed for the last 2 games" and "injur" not in unreported
    assert changes._status_text(_row(status="scratched", games_missed=5, last_played=None), 5) == "has been a scratch all season"


def test_reasons_that_are_not_injuries_never_get_injury_added():
    # Sora, 2026-10-31: "Nikishin is out (not injury related injury)"
    assert changes._status_text(_row(report_status="out", report_injury="not injury related", games_missed=7), 9) == \
        "is out (not injury related) and has missed the last 7 games"
    assert changes._injury("concussion") == " (concussion)"
    assert changes._injury("maintenance") == " (maintenance)"
    assert changes._injury("undisclosed") == ""


def test_a_line_with_a_fill_in_says_how_its_chances_have_gone():
    import polars as pl
    units = pl.DataFrame({"game_id": [1, 2, 3, 4], "team_id": [9] * 4, "kind": ["F"] * 4,
                          "unit": ["1-2-3", "1-2-3", "1-2-4", "1-2-4"], "sec": [1000.0, 1000.0, 1000.0, 1000.0],
                          "xgf": [1.0, 1.0, 2.0, 1.0], "xga": [1.0, 1.0, 0.5, 0.5]})
    names = {3: "Taylor Hall", 4: "Nikolaj Ehlers"}
    got = changes._how_its_gone(9, 3, "F", units, (1, 2), 4, [3, 4], [1, 2], names)
    assert got == " That line has had 75% of the chances at 5-on-5 in 33:20 together, up from 50% with Hall."
    assert changes._how_its_gone(9, 3, "F", units, (1, 2), 4, [3], [1, 2], names) == ""  # too little time together
