import polars as pl

from pipeline.export import pregame


def test_cut_keeps_only_games_from_earlier_days(tmp_path, monkeypatch):
    tables = tmp_path / "data" / "tables"
    for season in (2025, 2026):
        (tables / str(season)).mkdir(parents=True)
    pl.DataFrame({"game_id": [1, 2, 3], "date": ["2026-10-01", "2026-10-02", "2026-10-03"]}).write_parquet(tables / "2026" / "games.parquet")
    pl.DataFrame({"game_id": [1, 1, 2, 3], "x": [1, 2, 3, 4]}).write_parquet(tables / "2026" / "events.parquet")
    pl.DataFrame({"game_id": [9], "x": [0]}).write_parquet(tables / "2025" / "events.parquet")
    for name in ("raw", "models"):
        (tmp_path / "data" / name).mkdir()
    monkeypatch.setattr(pregame, "TABLES", tables)
    monkeypatch.setattr(pregame, "DATA", tmp_path / "data")
    root = tmp_path / "cut"
    root.mkdir()
    assert pregame.cut_tables(2026, "2026-10-03", root) == 2
    assert pl.read_parquet(root / "tables" / "2026" / "events.parquet")["game_id"].to_list() == [1, 1, 2]
    assert pl.read_parquet(root / "tables" / "2026" / "games.parquet")["game_id"].to_list() == [1, 2]
    # Earlier seasons are linked, never copied or cut.
    assert (root / "tables" / "2025").is_symlink() and (root / "raw").is_symlink()
