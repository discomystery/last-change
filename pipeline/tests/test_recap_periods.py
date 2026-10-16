from types import SimpleNamespace

from pipeline.export.recaps import periods


def game(last, away_score, home_score):
    return {"away": "MTL", "home": "WSH", "away_id": 8, "home_id": 15, "last_period": last,
            "away_score": away_score, "home_score": home_score}


def test_regulation_has_three_periods():
    S = SimpleNamespace(by_period={(1, 8, 1): ("REG", 2, 8), (1, 15, 1): ("REG", 2, 8), (1, 15, 3): ("REG", 1, 10)})
    out = periods(S, 1, game("REG", 2, 3))
    assert [p["label"] for p in out] == ["1st", "2nd", "3rd"]
    assert out[1] == {"label": "2nd", "goals": {"MTL": 0, "WSH": 0}, "shots": {"MTL": 0, "WSH": 0}}
    assert out[2]["goals"] == {"MTL": 0, "WSH": 1}


def test_shootout_adds_overtime_and_shootout_columns():
    # A scoreless overtime with no shots still gets its column; the shootout winner's goal is final minus goals in play.
    S = SimpleNamespace(by_period={(2, 8, 1): ("REG", 1, 9), (2, 15, 2): ("REG", 1, 7)})
    out = periods(S, 2, game("SO", 1, 2))
    assert [p["label"] for p in out] == ["1st", "2nd", "3rd", "OT", "SO"]
    assert out[3]["shots"] == {"MTL": 0, "WSH": 0}
    assert out[4] == {"label": "SO", "goals": {"MTL": 0, "WSH": 1}, "shots": None}
