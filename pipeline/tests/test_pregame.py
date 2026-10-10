import polars as pl

from pipeline.export import pregame


def test_cut_keeps_only_games_from_earlier_days(tmp_path, monkeypatch):
    tables = tmp_path / "data" / "tables"
    for season in (2025, 2026):
        (tables / str(season)).mkdir(parents=True)
    pl.DataFrame({"game_id": [1, 2, 3], "date": ["2026-10-01", "2026-10-02", "2026-10-03"]}).write_parquet(tables / "2026" / "games.parquet")
    pl.DataFrame({"game_id": [1, 1, 2, 3], "x": [1, 2, 3, 4]}).write_parquet(tables / "2026" / "events.parquet")
    pl.DataFrame({"game_id": [9], "x": [0]}).write_parquet(tables / "2025" / "events.parquet")
    pl.DataFrame({"date": ["2026-10-02", "2026-10-03"], "player_id": [7, 7]}).write_parquet(tables / "2026" / "rosters.parquet")
    for name in ("raw", "models"):
        (tmp_path / "data" / name).mkdir()
    monkeypatch.setattr(pregame, "TABLES", tables)
    monkeypatch.setattr(pregame, "DATA", tmp_path / "data")
    root = tmp_path / "cut"
    root.mkdir()
    assert pregame.cut_tables(2026, "2026-10-03", root) == 2
    assert pl.read_parquet(root / "tables" / "2026" / "events.parquet")["game_id"].to_list() == [1, 1, 2]
    assert pl.read_parquet(root / "tables" / "2026" / "games.parquet")["game_id"].to_list() == [1, 2]
    # Dated records with no game (the daily roster history) keep only earlier days.
    assert pl.read_parquet(root / "tables" / "2026" / "rosters.parquet")["date"].to_list() == ["2026-10-02"]
    # Earlier seasons are linked, never copied or cut.
    assert (root / "tables" / "2025").is_symlink() and (root / "raw").is_symlink()


def test_only_rebuilt_previews_under_older_rules_are_redone(tmp_path):
    import json

    from pipeline.export import pregame, previews
    for name, snap in {"live": {"rules": 1}, "old": {"rebuilt": True, "rules": 1}, "new": {"rebuilt": True, "rules": previews.RULES_VERSION},
                       "legacy": {"rebuilt": True}}.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(snap))
    assert [pregame._stale(tmp_path / f"{n}.json") for n in ("live", "old", "new", "legacy", "missing")] == [False, True, False, True, True]
