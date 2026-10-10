import polars as pl

from pipeline.ingest import coaches


def test_coach_as_of_uses_latest_game_before(tmp_path, monkeypatch):
    monkeypatch.setattr(coaches, "TABLES", tmp_path)
    (tmp_path / "2026").mkdir()
    pl.DataFrame({"game_id": [1, 2, 3], "date": ["2026-10-07", "2026-10-09", "2026-10-11"], "team": ["CAR"] * 3,
                  "coach": ["Old Coach", "New Coach", "Later Coach"]}, schema=coaches.SCHEMA).write_parquet(coaches.path(2026))
    assert coaches.as_of(2026, "2026-10-10T23:00:00Z") == {"CAR": "New Coach"}
    assert coaches.as_of(2026, "2026-10-01") == {}
