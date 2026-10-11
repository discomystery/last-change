from pipeline.export import jobs, outlook, wording


def test_new_york_clubs_read_as_nicknames():
    say = wording.places({"NYR": "NY Rangers", "CAR": "Carolina"})
    assert say == {"NYR": "the Rangers", "CAR": "Carolina"}
    assert wording.tidy(f"{say['NYR']}’s power play was busy. Coming in, {say['NYR']} was at 8.") == \
        "The Rangers’ power play was busy. Coming in, the Rangers were at 8."
    assert wording.tidy(f"Played out 100 times, {say['NYR']} wins 62.") == "Played out 100 times, the Rangers win 62."


def test_articles_and_signs():
    assert [outlook._a(p) for p in (8, 18, 31, 72, 83)] == ["an 8%", "an 18%", "a 31%", "a 72%", "an 83%"]
    assert jobs._signed(-0.04) == "+0.0" and jobs._signed(-1.26) == "-1.3" and jobs._signed(0.5) == "+0.5"
