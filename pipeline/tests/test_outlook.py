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


def test_a_key_absence_and_return_are_named_in_the_note():
    places = {"AAA": "Alpha", "BBB": "Beta"}
    steps = [{"input": "home", "shift": 0.03}, {"input": "xgd", "shift": 0.0}, {"input": "b2b", "shift": 0.0}]
    body = outlook._why(steps, "BBB", "AAA", "BBB", places, {"AAA": {"without": "Star", "maybe": True}, "BBB": {"back": "Ace"}})
    assert "Alpha may be without Star" in body and "gets Ace back" in body
    assert body.startswith("The two have been about even")
    assert "injur" not in body


def test_reasons_keep_one_contrast_word_and_name_each_team_once_a_sentence():
    import itertools
    places, nicks = {"AAA": "Alpha", "BBB": "Beta"}, {"AAA": "the Ants", "BBB": "the Bees"}
    contrast = re.compile(r"\b(even though|though|while|but)\b")
    for xgd, b2b, home, wo, back in itertools.product([0.0, 0.03, -0.1], [0, 0.02, -0.02], [0.02, 0.06], [None, "AAA", "BBB"], [None, "AAA", "BBB"]):
        steps = [{"input": "xgd", "shift": xgd}, {"input": "b2b", "shift": b2b}, {"input": "home", "shift": home}, {"input": "roster", "shift": 0.03}]
        lu = {}
        if wo:
            lu.setdefault(wo, {})["without"] = "Star"
        if back:
            lu.setdefault(back, {})["back"] = "Ace"
        for s in re.split(r"(?<=\.) ", outlook._why(steps, "AAA", "BBB", "BBB", places, lu, nicks)):
            assert len(contrast.findall(s.lower())) <= 1, s
            assert s.count("Alpha") <= 1 and s.count("Beta") <= 1, s


def test_an_edge_body_that_only_repeats_its_headline_is_caught():
    from pipeline.export import previews
    assert previews.echoes("The Ants hound the puck, the Bees sit back", "The Ants hound the puck, but the Bees sit back.")
    assert not previews.echoes("Bruisers against finesse", "The Ants hit a lot, but the Bees mostly don’t.")


def test_no_contrast_sentence_carries_an_and_chain():
    """"Even though Florida will be without Barkov, the Panthers have been out-chancing opponents lately and have
    home-ice advantage, and Winnipeg will be without Morrissey" (found by the self-check, 2026-10-30)."""
    import itertools
    from pipeline.export import textcheck
    places, nicks = {"AAA": "Alpha", "BBB": "Beta"}, {"AAA": "the Ants", "BBB": "the Bees"}
    for xgd, b2b, home, wo, back in itertools.product([0.0, 0.03, -0.1], [0, 0.02, -0.02], [0.02, 0.06], [None, "AAA", "BBB"], [None, "AAA", "BBB"]):
        steps = [{"input": "xgd", "shift": xgd}, {"input": "b2b", "shift": b2b}, {"input": "home", "shift": home}, {"input": "roster", "shift": 0.03}]
        lu = {}
        if wo:
            lu.setdefault(wo, {})["without"] = "Star"
        if back:
            lu.setdefault(back, {})["back"] = "Ace"
        body = outlook._why(steps, "AAA", "BBB", "BBB", places, lu, nicks)
        assert not [f for f in textcheck.check_text(body) if f["level"] == "error"], body
    body = outlook._why([{"input": "xgd", "shift": 0.06}, {"input": "home", "shift": 0.03}, {"input": "roster", "shift": 0.02}],
                        "AAA", "BBB", "BBB", places, {"AAA": {"without": "Star"}, "BBB": {"without": "Ace"}}, nicks)
    assert body == ("Even though Beta will be without Ace, the Bees have been out-chancing opponents lately and have "
                    "home-ice advantage. On top of that, Alpha will be without Star."), body
