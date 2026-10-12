from pipeline.metrics import drift


def _m(quick_goal_rate, quick_share=0.076, distance=35.0):
    return {"shots": 100000, "quick_shots": int(100000 * quick_share), "quick_goal_rate": quick_goal_rate, "quick_share": quick_share,
            "distance": distance, "distance_sd": 20.0, "goals": 6800.0, "goals_per_xg": 1.0, "no_previous": 0.0, "no_location": 0.0}


def test_flags_the_old_recording_and_passes_normal_seasons(monkeypatch, tmp_path):
    seasons = {2023: _m(0.032), 2024: _m(0.024), 2025: _m(0.029), 2026: _m(0.027), 2022: _m(0.098, quick_share=0.065)}
    monkeypatch.setattr(drift, "measures", lambda s: seasons[s])
    monkeypatch.setattr(drift, "TABLES", tmp_path)
    for s in (2023, 2024, 2025):
        (tmp_path / str(s)).mkdir()
        (tmp_path / str(s) / "shot_features.parquet").touch()
    assert drift.check(2026)["status"] == "ok"
    old = drift.check(2022)
    assert old["status"] == "changed"
    assert {c["name"] for c in old["checks"] if not c["ok"]} == {"quick_goal_rate", "quick_share"}
    assert "goal rate of shots within 1 s" in drift.alarm_text(old)


def test_too_early_to_judge(monkeypatch):
    monkeypatch.setattr(drift, "measures", lambda s: {"shots": 300})
    assert drift.check(2026, [2025])["status"] == "early"
