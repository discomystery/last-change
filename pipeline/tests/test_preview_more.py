"""The fuller preview's wording rules (pipeline/export/preview_more.py)."""
from pipeline.export import preview_more as pm


class W:
    def the(self, t):
        return {"FLA": "the Panthers", "WPG": "the Jets"}[t]

    def place(self, t):
        return {"FLA": "Florida", "WPG": "Winnipeg"}[t]


def dims(**kw):
    return {"dims": {k: {"blend": {"pct": p, "rank": r}} for k, (p, r) in kw.items()}}


def test_same_rank_at_the_top_is_told_once():
    fp = {"FLA": dims(physical=(98, 1), forecheck=(98, 1), rough=(97, 1))}
    s = pm.style("FLA", pm._picks("FLA", fp), W())
    assert s.endswith("and they’re first in the league in all three")


def test_a_low_rank_is_never_told():
    fp = {"WPG": dims(rough=(3, 31), pace=(10, 29))}
    s = pm.style("WPG", pm._picks("WPG", fp), W())
    assert "31st" not in s and "29th" not in s


def test_strengths_and_weaknesses_take_one_but():
    fp = {"WPG": dims(qualityAllowed=(97, 1), suppression=(5, 31))}
    s = pm.style("WPG", pm._picks("WPG", fp), W())
    assert s.startswith("The Jets keep dangerous shots away, but give up a lot of shots.")
    assert s.count(" but ") == 1


def test_bouncy_habits_are_left_out():
    fp = {"WPG": dims(rebounds=(1, 32), turnover=(99, 1))}
    assert pm._picks("WPG", fp) == []


def test_a_shared_habit_is_said_once(monkeypatch):
    monkeypatch.setattr(pm, "scoring", lambda *a: None)
    fp = {"FLA": dims(icing=(98, 1), physical=(97, 2)), "WPG": dims(icing=(90, 4), pace=(5, 31))}
    s = pm.how_they_play("WPG", "FLA", fp, 2026, "2026-10-30", W())
    assert s.startswith("Both teams rarely ice the puck.")
    assert s.count("ice the puck") == 1
