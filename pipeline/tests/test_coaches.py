import polars as pl

from pipeline.ingest import coaches


def test_current_names_latest_coach_and_mid_season_change(tmp_path, monkeypatch):
    monkeypatch.setattr(coaches, "TABLES", tmp_path)
    for season, rows in {2025: [(1, "2026-03-01", "CAR", "Old Coach"), (2, "2026-03-01", "SEA", "Last Year")],
                         2026: [(3, "2026-10-01", "CAR", "Rod B"), (4, "2026-10-03", "CAR", "Interim Guy"), (5, "2026-10-05", "CAR", "Interim Guy")]}.items():
        (tmp_path / str(season)).mkdir()
        pl.DataFrame(rows, schema=list(coaches.SCHEMA), orient="row").write_parquet(coaches.path(season))
    now = coaches.current(2026)
    assert now["CAR"] == {"coach": "Interim Guy", "since": "2026-10-03"}
    assert now["SEA"] == {"coach": "Last Year", "since": None}  # no game yet this season: last season's coach
