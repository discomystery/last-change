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
