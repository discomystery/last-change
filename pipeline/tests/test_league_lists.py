"""League-wide all-time list items in post-game stories (export/league_lists.py)."""
import polars as pl

from pipeline.export import league_lists
from pipeline.ingest import records


def _setup(monkeypatch, rows, tonight, earlier=()):
    snap = {"fetched": 0, "lists": {"otg3": rows}}
    monkeypatch.setattr(records, "league_snapshots", lambda: [snap])
    monkeypatch.setattr(league_lists, "coverage", lambda s, n, season, logs: "2026-10-10")
    counts = [{"game_id": 7, "date": "2026-10-14", "player_id": p, "goals": 1, "assists": 0, "gwg": 1, "otg": 1} for p in tonight]
    counts += [{"game_id": 3, "date": d, "player_id": p, "goals": 1, "assists": 0, "gwg": 1, "otg": 1} for d, p in earlier]
    df = pl.DataFrame(counts).with_columns(points=pl.col("goals"), otg3=pl.col("otg"))
    monkeypatch.setattr(league_lists, "season_counts", lambda season: df)


def _row(pid, name, n):
    return {"playerId": pid, "skaterFullName": name, "otGoals": n, "goals": 0, "points": 0}


def test_standing_second_only_to(monkeypatch):
    _setup(monkeypatch, [_row(1, "Leon Draisaitl", 20), _row(2, "Sidney Crosby", 18), _row(9, "Sebastian Aho", 18)], [9])
    out = league_lists.moves(7, "2026-10-14", [9], {9: "Sebastian Aho"}, 2026, {})
    assert out[0]["text"] == ("Sebastian Aho has 19 overtime goals since the NHL went to 3-on-3 overtime in 2015-16, "
                              "second only to Leon Draisaitl in that time.")


def test_snapshot_after_the_game_is_wound_back(monkeypatch):
    # the snapshot already counts tonight's goal (coverage after the game), so it is taken off before comparing
    _setup(monkeypatch, [_row(1, "Leon Draisaitl", 20), _row(2, "Sidney Crosby", 18), _row(9, "Sebastian Aho", 19)], [9])
    monkeypatch.setattr(league_lists, "coverage", lambda s, n, season, logs: "2026-10-14")
    out = league_lists.moves(7, "2026-10-14", [9], {9: "Sebastian Aho"}, 2026, {})
    assert "has 19 overtime goals" in out[0]["text"]
