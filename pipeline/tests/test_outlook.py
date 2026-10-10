import re

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
    assert outlook._route(fp, "AAA", "BBB")[0] == "if its power play gets going"
