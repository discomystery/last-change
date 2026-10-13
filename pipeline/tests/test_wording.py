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


def test_places_for_where_a_game_was_played():
    # "5-1 in the Rangers" and "Round two in the Rangers" (Sora, 2026-10-13): a team name is not a place
    assert wording.at("NY Rangers") == "in New York"
    assert wording.at("NY Islanders") == "on Long Island"
    assert wording.at("Carolina") == "in Carolina"
    assert wording.town("NY Islanders") == "Long Island"


def test_nickname_once_is_sparing():
    places = {"Hurricanes": "Carolina", "Lightning": "Tampa Bay"}
    sents = ["The Hurricanes won the first meeting, 5-1 in Carolina.", "The Hurricanes are 5-2-0.", "The Lightning have won four straight.",
             "The Hurricanes’ best way to tip it is a power play."]
    out = wording.nickname_once(sents, "x", places=places)
    changed = [i for i, (a, b) in enumerate(zip(sents, out)) if a != b]
    assert len(changed) == 1 and changed[0] != 0  # one swap, never the first mention
    assert "Canes" in out[changed[0]]
    assert sents[2] == out[2]  # the Lightning are named once: no nickname
    # never in a sentence that also has the full name or the place
    assert wording.nickname_once(["The Hurricanes won.", "The Hurricanes beat Carolina’s rivals."], "k",
                                 places={"Hurricanes": "Carolina"}) == ["The Hurricanes won.", "The Hurricanes beat Carolina’s rivals."]
    # possessives follow the nickname: "the Avalanche’s" becomes "the Avs’", "the Oilers’" becomes "the Oil’s"
    assert wording.nickname_para("The Avalanche won. The Avalanche’s kill held.", "k") == "The Avalanche won. The Avs’ kill held."
    assert wording.nickname_para("The Oilers won. The Oilers’ kill held.", "k") == "The Oilers won. The Oil’s kill held."
    # a club with no common nickname keeps its name
    assert wording.nickname_para("The Kings won. The Kings scored.", "k") == "The Kings won. The Kings scored."
    assert wording.nickname_para("The Rangers won. The Rangers scored.", "k") == "The Rangers won. The Rangers scored."
