import polars as pl

from pipeline.export import lately


def _games(n, team="AAA"):
    return pl.DataFrame({"game_id": list(range(1, n + 1)), "team": [team] * n, "opp": ["BBB"] * n,
                         "date": [f"2026-10-{i:02d}" for i in range(1, n + 1)], "home": [i % 2 == 0 for i in range(n)],
                         "gf": [3, 1, 2] * (n // 3) + [3] * (n % 3), "ga": [1, 2, 2] * (n // 3) + [1] * (n % 3),
                         "end": ["REG", "REG", "OT"] * (n // 3) + ["REG"] * (n % 3),
                         "xgf_adj": [2.0] * n, "xga_adj": [2.0] * n,
                         "ppg": [1] * n, "ppo": [3] * n, "ppga": [0] * n, "ppa": [2] * n})


def test_last_ten_record_and_results():
    f = lately.team_form(_games(12), "AAA", [-4.4, 4.3])
    assert f["n"] == 10 and len(f["games"]) == 10
    assert [g["res"] for g in f["games"][:3]] == ["OT", "W", "L"]  # games 3-5, oldest first
    r = f["record"]
    assert r["w"] + r["l"] + r["otl"] == 10
    assert f["pp"] == [10, 30] and f["pk"] == [0, 20]
    assert f["chances"]["now"] == 50.0 and f["chances"]["word"] == "usual"


def test_too_few_games_waits():
    assert lately.team_form(_games(2), "AAA", [-4.4, 4.3]) is None


def test_better_only_outside_the_bounce_band():
    g = _games(30)
    g = g.with_columns(pl.when(pl.col("game_id") > 20).then(4.0).otherwise(2.0).alias("xgf_adj"))
    f = lately.team_form(g, "AAA", [-4.4, 4.3])
    assert f["chances"]["word"] == "better" and f["chances"]["gap"] > 4.3
