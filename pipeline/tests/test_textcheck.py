from pipeline.export import grammar, textcheck


def errors(text):
    return [f["why"] for f in textcheck.check_text(text) if f["level"] == "error"]


def test_catches_the_slips_readers_noticed():
    assert errors("Tied it with the goalie pulled and 1 seconds left.")
    assert errors("The Panthers have won three straight, the last a 8-3 rout of Seattle.")
    assert errors("He scored in his 21th NHL game.")
    assert errors("Crosby and Rakell and Rust each had a point.")
    assert errors("Nobody had a bigger night, even in a loss, even with Hagel’s hat trick in the game.")
    assert errors("The Rangers deserved this one.")
    assert errors("He played 9.3 minutes.")
    assert errors("Columbus's power play scored twice.")


def test_leaves_good_sentences_alone():
    for s in ["Rasmus Andersson and Noah Hanifin scored 2:56 apart to put the Golden Knights up 2-0.",
              "The Panthers have won three straight, the last an 8-3 rout of Seattle.",
              "He scored in his 21st NHL game, an 11-game point streak.",
              "Crosby, Rakell and Rust each had a point.",
              "Tom Wilson added the empty-netter with 1 second left to make it 3-1.",
              "Brett Howden added an empty-net goal with 2:13 left to make it 6-2."]:
        assert errors(s) == [], s


def test_story_level_findings():
    story = {"head": "Test", "paras": ["Trevor Connelly stretched the lead to 4-1. Shea Theodore stretched the lead to 5-1."]}
    whys = [f["why"] for f in textcheck.check_story(story)]
    assert "same phrase twice in one story" in whys


def test_grammar_helpers():
    assert grammar.and_list(["A", "", "B", "C"]) == "A, B and C"
    assert grammar.num(0) == "no" and grammar.num(0, zero="zero") == "zero" and grammar.num(13) == "13"
    assert grammar.ordinal_word(8) == "eighth" and grammar.ordinal_word(23) == "23rd"
    assert grammar.count(1, "goal") == "one goal" and grammar.count(3, "save") == "three saves"
    assert grammar.possessive("Matthews") == "Matthews’" and grammar.possessive("Carolina") == "Carolina’s"
    assert grammar.seconds_left(0) == "less than a second" and grammar.seconds_left(133) == "2:13"
