from pipeline.metrics import starters


def test_rows_back_to_back_and_streak():
    team = [("2026-10-01", 2026, 1), ("2026-10-03", 2026, 1), ("2026-10-04", 2026, 2), ("2026-10-05", 2026, 2)]
    gl = {1: [("2026-10-01", 1.0), ("2026-10-03", 0.5)], 2: [("2026-10-04", -1.0), ("2026-10-05", 2.0)]}
    rows = {r["cand"]: r for r in starters._rows(team, gl, "2026-10-06", 2026)}
    assert set(rows) == {1, 2}
    assert rows[2]["last"] == 1 and rows[2]["streak"] == 2 and rows[2]["last_b2b"] == 1
    assert rows[1]["last"] == 0 and rows[1]["b2b"] == 1 and rows[1]["rest"] == 3
    assert rows[2]["hot"] == 1.0 and rows[1]["shs"] == 0.5
    # a later starter's games are never read before the day
    assert starters._rows(team[:2], gl, "2026-10-02", 2026)[0]["hot"] == 1.0
