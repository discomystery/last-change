"""The fuller preview's wording rules (pipeline/export/preview_more.py) and its timing words (stories.when_before)."""
from pipeline.export import preview_more as pm
from pipeline.export.stories import when_before


class W:
    def the(self, t):
        return {"FLA": "the Panthers", "WPG": "the Jets"}[t]

    def poss(self, t):
        return self.the(t) + "’"

    def place(self, t):
        return {"FLA": "Florida", "WPG": "Winnipeg"}[t]


def dims(**kw):
    return {"dims": {k: {"blend": {"pct": p, "rank": r}} for k, (p, r) in kw.items()}}


def test_three_top_ranks_in_one_family_read_as_an_identity():
    fp = {"FLA": dims(physical=(98, 1), forecheck=(98, 1), rough=(97, 1))}
    s = pm.style("FLA", pm._picks("FLA", fp), W())
    assert s.startswith("The Panthers are the league’s most physical team: first in")


def test_one_identity_a_team_and_its_rank_is_not_repeated():
    fp = {"FLA": dims(forecheck=(99, 1), physical=(97, 2))}
    s = pm.style("FLA", pm._picks("FLA", fp), W())
    assert s.count("than anyone") + s.count("league’s") == 1
    assert "first in forecheck" not in s


def test_a_low_rank_is_never_told():
    fp = {"WPG": dims(rough=(3, 31), pace=(10, 29))}
    s = pm.style("WPG", pm._picks("WPG", fp), W())
    assert "31st" not in s and "29th" not in s


def test_strengths_and_weaknesses_take_one_but():
    fp = {"WPG": dims(qualityAllowed=(97, 1), suppression=(5, 31))}
    s = pm.style("WPG", pm._picks("WPG", fp), W())
    assert s.startswith("The Jets keep dangerous shots away better than anyone, but give up a lot of shots.")
    assert s.count(" but ") == 1


def test_a_figure_mid_list_goes_in_brackets_and_replaces_the_rank():
    fp = {"WPG": dims(volume=(95, 2), longShifts=(4, 30))}
    stats = {"volume": {"hi": ("32.7 shots a game", "second most")}}
    s = pm.style("WPG", pm._picks("WPG", fp), W(), stats)
    assert "(32.7 shots a game, second most)" in s or "32.7 shots a game (second most)" in s
    assert "second in shot volume" not in s


def test_bouncy_habits_are_left_out():
    fp = {"WPG": dims(rebounds=(1, 32), turnover=(99, 1))}
    assert pm._picks("WPG", fp) == []


def test_a_shared_habit_is_said_once(monkeypatch):
    monkeypatch.setattr(pm, "scoring", lambda *a: None)
    monkeypatch.setattr(pm, "stat_words", lambda *a: {})
    fp = {"FLA": dims(icing=(98, 1), physical=(97, 2)), "WPG": dims(icing=(90, 4), pace=(5, 31))}
    s = pm.how_they_play("WPG", "FLA", fp, 2026, "2026-10-30", W())
    assert s.startswith("Both teams rarely ice the puck.")
    assert s.count("ice the puck") == 1


def test_what_the_styles_mean_together():
    fp = {"FLA": dims(pp=(95, 2)), "WPG": dims(pk=(10, 29))}
    lines = pm.consequences("WPG", "FLA", fp, W(), set())
    assert lines[0][1] == "the Panthers’ power play could do real damage when the Jets are shorthanded"
    assert pm.consequences("WPG", "FLA", fp, W(), {"pp"}) == []  # a call already says it


def test_a_call_is_told_in_the_pairing_words():
    assert pm.meet_line("duel", "pp", "FLA", "WPG", W()) == "The Panthers’ power play against the Jets’ penalty killers is strength against strength."
    assert pm.meet_line("edge", "pp", "FLA", "WPG", W()) is None


def test_consequences_join_the_paragraph(monkeypatch):
    monkeypatch.setattr(pm, "scoring", lambda *a: None)
    monkeypatch.setattr(pm, "stat_words", lambda *a: {})
    fp = {"FLA": dims(pp=(95, 2), forecheck=(96, 2)), "WPG": dims(pk=(10, 29), longShifts=(10, 29))}
    s = pm.how_they_play("WPG", "FLA", fp, 2026, "2026-10-30", W(), key=(1,))
    assert "power play could do real damage" in s and "forecheck could keep" in s
    # each meeting line rides on the habit it comes from, right after that club's list (Sora, 2026-10-30)
    assert "They also have a dangerous power play, so their power play could do real damage when the Jets are shorthanded" in s \
        or "They also have a leaky penalty kill, so the Panthers’ power play could do real damage when they’re shorthanded" in s


def test_a_line_whose_habits_were_not_told_gets_no_lead_in(monkeypatch):
    monkeypatch.setattr(pm, "scoring", lambda *a: None)
    monkeypatch.setattr(pm, "stat_words", lambda *a: {})
    fp = {"FLA": dims(physical=(97, 2)), "WPG": dims(pace=(5, 31))}
    s = pm.how_they_play("WPG", "FLA", fp, 2026, "2026-10-30", W(), gists=[("The Panthers’ power play could do real damage.", {"pp", "pk"})], key=(1,))
    assert "That means" not in s and "Put that together" not in s


def test_earlier_games_are_told_by_day():
    assert when_before("2026-10-29", "2026-10-30") == "last night"
    assert when_before("2026-10-27", "2026-10-30") == "on Tuesday"
    assert when_before("2026-10-21", "2026-10-30") == "on Oct 21"
    assert when_before("2026-10-29", None) == "last time out"
