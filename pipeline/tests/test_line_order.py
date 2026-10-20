"""Forward lines written left wing, center, right wing (`line_order`)."""
import gzip
import json

import polars as pl

from pipeline.metrics import line_order

TID = 12
# Jarvis (1) is listed as a center and plays left wing; Aho (2) takes the draws; Blake (3) is a listed right winger.
# Martinook (4), a listed left winger, moves to the middle of a line with Ehlers (5) and Unger Sörum (6).
LISTED = {1: ("C", "R"), 2: ("C", "L"), 3: ("R", "R"), 4: ("L", "L"), 5: ("L", "L"), 6: ("R", "L"), 7: ("C", "L"), 8: ("L", "L"), 9: ("L", "R")}


def _faceoffs(gid, taker, line, n):
    """n draws, all won by `taker` (home) with `line` on the ice, against an away forward."""
    return [{"game_id": gid, "type": "faceoff", "is_home": True, "p1": taker, "p2": 99, "y_norm": 0.0, "situation_code": "1551",
             "home_on": list(line), "away_on": [99]} for _ in range(n)]


def _tables(tmp_path, monkeypatch, events, previews=None):
    d = tmp_path / "2026"
    d.mkdir()
    pl.DataFrame([{"game_id": g, "date": f"2026-10-{10 + g:02d}"} for g in (1, 2)]).write_parquet(d / "games.parquet")
    pl.DataFrame([{"game_id": g, "player_id": p, "team_id": TID, "pos": pos} for g in (1, 2) for p, (pos, _) in LISTED.items()]
                 + [{"game_id": g, "player_id": 99, "team_id": 1, "pos": "C"} for g in (1, 2)]).write_parquet(d / "players.parquet")
    pl.DataFrame([{"date": "2026-10-11", "player_id": p, "pos": pos, "shoots": hand} for p, (pos, hand) in LISTED.items()]).write_parquet(d / "rosters.parquet")
    schema = {"game_id": pl.Int64, "type": pl.String, "is_home": pl.Boolean, "p1": pl.Int64, "p2": pl.Int64, "y_norm": pl.Float64,
              "situation_code": pl.String, "home_on": pl.List(pl.Int64), "away_on": pl.List(pl.Int64)}
    pl.DataFrame(events, schema=schema).write_parquet(d / "events.parquet")
    if previews:
        folder = tmp_path / "raw" / "nhl_previews" / "2026"
        folder.mkdir(parents=True)
        for gid, lineups in previews.items():
            (folder / f"{gid}.json.gz").write_bytes(gzip.compress(json.dumps({"fetched": "2026-10-11T18:00:00+00:00", "lineups": lineups}).encode()))
        pl.DataFrame([{"game_id": g, "date": f"2026-10-{10 + g:02d}", "home": "CAR", "home_id": TID, "away": "XXX", "away_id": 1} for g in (1, 2)]).write_parquet(d / "games.parquet")
    monkeypatch.setattr(line_order, "TABLES", tmp_path)
    monkeypatch.setattr(line_order, "RAW", tmp_path / "raw")
    line_order._built.clear()


def test_draws_pick_the_center_and_a_listed_winger_takes_the_other_side(tmp_path, monkeypatch):
    _tables(tmp_path, monkeypatch, _faceoffs(1, 2, (1, 2, 3), 6) + _faceoffs(2, 4, (4, 5, 6), 5))
    assert line_order.order(2026, TID, [3, 1, 2]) == [1, 2, 3]  # Jarvis left of Aho, beside Blake (both shoot right)
    # Martinook is the center when he takes this line's draws, and a left-shot pair splits by listed position.
    assert line_order.order(2026, TID, [5, 6, 4]) == [5, 4, 6]


def test_without_draws_handedness_then_listed_position(tmp_path, monkeypatch):
    _tables(tmp_path, monkeypatch, [])
    # No faceoffs: the only listed center goes in the middle; a right shot and a left shot split by hand.
    assert line_order.order(2026, TID, [9, 7, 8]) == [8, 7, 9]


def test_nhl_com_projection_sets_the_wings(tmp_path, monkeypatch):
    _tables(tmp_path, monkeypatch, _faceoffs(1, 2, (1, 2, 3), 6), previews={2: {"CAR": {"lines": [[3, 2, 1]]}}})
    assert line_order.order(2026, TID, [1, 2, 3]) == [3, 2, 1]  # NHL.com has Blake on the left, so he goes left
    assert line_order.order(2026, TID, [1, 2, 3], "2026-10-11") == [1, 2, 3]  # not before that preview's game
    assert line_order.order(2026, TID, [1, 2]) == [1, 2]  # not a trio: as it came
