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


def test_goalie_rink_storylines(monkeypatch):
    from pipeline.metrics import venues
    rec = {"starts": 5, "w": 0, "l": 3, "o": 1, "winless": 5, "last_win": None}
    monkeypatch.setattr(venues, "record", lambda *a: rec)
    assert venues.storyline(1, "A. Goalie", "TOR", "in Toronto", "2026-10-11")["text"] == \
        "A. Goalie has never won in Toronto: he is 0-3-1 there in his career."
    rec.update(w=2, winless=6, last_win="2019-02-01", starts=9)
    assert "last 6 starts there, and his last win there was in 2019" in venues.storyline(1, "A. Goalie", "TOR", "in Toronto", "2026-10-11")["text"]
    rec.update(w=1, winless=1, starts=3)
    assert venues.storyline(1, "A. Goalie", "TOR", "in Toronto", "2026-10-11") is None
