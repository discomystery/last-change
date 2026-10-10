import re
import polars as pl

from pipeline.export import outlook

DIMS = ["volume", "suppression", "quality", "qualityAllowed", "turnover", "breakdowns", "pp", "pk"]


def _fp(pcts):
    return {"dims": {k: {"blend": {"pct": pcts.get(k, 50)}} for k in DIMS}}


def test_notes_never_carry_the_chance():
    fp = {"AAA": _fp({"pp": 95}), "BBB": _fp({"pk": 5})}
    places = {"AAA": "Aville", "BBB": "Btown"}
    for p in (0.2, 0.4, 0.45, 0.5, 0.55, 0.65, 0.8):
        steps = [{"input": "home", "shift": 0.04}, {"input": "xgd", "shift": p - 0.54}, {"input": "b2b", "shift": 0.0}]
        notes = outlook.notes(1, "AAA", "BBB", {"p_home": p, "steps": steps}, fp, places)
        assert set(notes) == {"AAA", "BBB"}
        for n in notes.values():
            assert not re.search(r"\d", n["head"] + n["body"]), n  # words only: no figure a bettor could read


def test_away_route_uses_its_strongest_edge():
    fp = {"AAA": _fp({"pp": 95}), "BBB": _fp({"pk": 5})}
    assert outlook._route(fp, "AAA", "BBB")[0] == "if their power play gets going"


def test_morning_rosters_use_the_game_record_when_played_and_the_latest_lineup_otherwise():
    df = pl.DataFrame({"game_id": [1, 2], "home_id": [10, 10], "away_id": [20, 30]})
    done = pl.DataFrame({"game_id": [1, 1], "team_id": [10, 20], "r": [0.3, -0.1]})
    out = outlook._with_rosters(df, done, {10: 0.05, 20: 0.0, 30: 0.2})
    assert out["roster"].to_list() == [0.3 - -0.1, 0.05 - 0.2]
