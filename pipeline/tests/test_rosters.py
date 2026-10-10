import polars as pl

from pipeline.ingest import rosters


def _setup(tmp_path, monkeypatch, listing):
    (tmp_path / "2026").mkdir()
    pl.DataFrame({"game_id": [1], "game_type": [2], "home": ["AAA"], "away": ["BBB"]}).write_parquet(tmp_path / "2026" / "games.parquet")
    monkeypatch.setattr(rosters, "TABLES", tmp_path)
    monkeypatch.setattr(rosters, "OFFLINE", False)
    calls = []

    def rows(team, season, day):
        calls.append((team, day))
        return [{"date": day, "team": team, "player_id": pid, "first": "A", "last": "B", "pos": "C", "shoots": "L", "number": 9,
                 "birth_date": "2000-01-01"} for pid in listing[day][team]]
    monkeypatch.setattr(rosters, "_rows", rows)
    return calls


def test_one_snapshot_per_day_and_team_history(tmp_path, monkeypatch):
    listing = {"2026-10-01": {"AAA": [1, 2], "BBB": [3]}, "2026-10-05": {"AAA": [1], "BBB": [3, 2]}}
    calls = _setup(tmp_path, monkeypatch, listing)
    assert rosters.snapshot(2026, "2026-10-01")["players"] == 3
    assert rosters.snapshot(2026, "2026-10-01")["skipped"]  # same day again: nothing fetched
    assert len(calls) == 2
    rosters.snapshot(2026, "2026-10-05")
    # Player 2 was traded from AAA to BBB between the two days; the history keeps both.
    assert rosters.team_on(2026, 2, "2026-10-03") == "AAA"
    assert rosters.team_on(2026, 2, "2026-10-05") == "BBB"
    assert rosters.team_on(2026, 2, "2026-09-30") is None  # before anything was recorded
    assert rosters.team_on(2026, 99, "2026-10-05") is None


def test_offline_runs_never_fetch(tmp_path, monkeypatch):
    calls = _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(rosters, "OFFLINE", True)
    assert rosters.snapshot(2026, "2026-10-01") == {"skipped": "offline"}
    assert calls == []
