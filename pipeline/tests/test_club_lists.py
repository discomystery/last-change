"""Franchise all-time list lines in post-game stories (stories._club_lists)."""
import polars as pl

from pipeline.export import stories
from pipeline.ingest import records


class W:
    def poss(self, t):
        return "the Hurricanes’"


def _setup(monkeypatch, lead, logs):
    monkeypatch.setattr(records, "franchise_of", lambda season, abbr: 26)
    monkeypatch.setattr(records, "seasons_of", lambda fid: 30)
    monkeypatch.setattr(records, "leaders", lambda fid: lead)
    monkeypatch.setattr(stories, "_club_logs", lambda: logs)
    monkeypatch.setattr("pipeline.ingest.names.spell", lambda pid, f, l: (f, l))


def _row(pid, points, goals, active=False):
    return {"playerId": pid, "firstName": "P", "lastName": str(pid), "goals": goals, "assists": points - goals,
            "points": points, "gamesPlayed": 500, "activePlayer": active}


def test_passing_a_retired_player(monkeypatch):
    lead = [_row(1, 900, 400), _row(2, 776, 300), _row(9, 775, 316, active=True)]
    logs = {9: [("20252026", "2026-03-01", "CAR", 316, 775), ("20262027", "2026-10-14", "CAR", 0, 2)]}
    _setup(monkeypatch, lead, logs)
    tonight = pl.DataFrame({"player_id": [9], "team": ["CAR"], "pts": [2]})
    out = stories._club_lists("2026-10-14", tonight, {9: "Sebastian Aho"}, W(), 2026)
    assert out and out[0]["text"] == "Sebastian Aho passed P 2 for second on the Hurricanes’ all-time scoring list, with 777 points."


def test_nothing_outside_the_top_ten(monkeypatch):
    lead = [_row(i, 1000 - i, 300) for i in range(1, 12)] + [_row(99, 900, 300, active=True)]
    logs = {99: [("20252026", "2026-03-01", "CAR", 10, 50), ("20262027", "2026-10-14", "CAR", 0, 1)]}
    _setup(monkeypatch, lead, logs)
    tonight = pl.DataFrame({"player_id": [99], "team": ["CAR"], "pts": [1]})
    assert stories._club_lists("2026-10-14", tonight, {99: "Someone"}, W(), 2026) == []


def test_preview_chase_names_the_list(monkeypatch):
    lead = [_row(1, 900, 400), _row(2, 330, 329), _row(9, 900, 328, active=True)]
    logs = {9: [("20252026", "2026-03-01", "WPG", 328, 600)]}
    _setup(monkeypatch, lead, logs)
    monkeypatch.setattr(stories, "_games_between", lambda t, a, b: 0)
    P = pl.DataFrame({"game_id": [5], "team": ["WPG"], "player_id": [9]})
    out = stories._club_chases("WPG", {"game_id": 5, "date": "2026-10-13"}, P, {9: "Kyle Connor"}, W(), "2026-10-15T23:00:00Z", 2026)
    assert out[0]["text"] == "Kyle Connor is two goals from passing P 2 for second on the Hurricanes’ all-time goals list."
