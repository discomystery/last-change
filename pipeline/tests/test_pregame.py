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


def test_previews_under_older_rules_or_methods_are_redone(tmp_path):
    import json

    from pipeline.export import pregame, previews
    now = {"rules": previews.RULES_VERSION, "methods": pregame.METHODS}
    for name, snap in {"live": {"rules": 1}, "live_now": now, "old": {"rebuilt": True, "rules": 1, "methods": pregame.METHODS},
                       "new": {"rebuilt": True, **now}, "old_methods": {"rebuilt": True, "rules": previews.RULES_VERSION, "methods": pregame.METHODS - 1},
                       "legacy": {"rebuilt": True}}.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(snap))
    names = ("live", "live_now", "old", "new", "old_methods", "legacy", "missing")
    assert [pregame._stale(tmp_path / f"{n}.json") for n in names] == [True, False, True, False, True, True, True]


def test_live_original_is_kept_once(tmp_path, monkeypatch):
    import json

    from pipeline.export import pregame
    monkeypatch.setattr(pregame, "RAW", tmp_path / "raw")
    live, rebuilt = tmp_path / "1.json", tmp_path / "2.json"
    live.write_text(json.dumps({"claims": ["before puck drop"]}))
    rebuilt.write_text(json.dumps({"rebuilt": True}))
    pregame.keep_original(live, 2026)
    live.write_text(json.dumps({"claims": ["changed"]}))
    pregame.keep_original(live, 2026)  # a later run must not overwrite the first copy
    pregame.keep_original(rebuilt, 2026)
    kept = tmp_path / "raw" / "previews_live" / "2026"
    assert json.loads((kept / "1.json").read_text())["claims"] == ["before puck drop"] and not (kept / "2.json").exists()
