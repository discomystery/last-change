from pipeline.export import absences as A

the = lambda t: f"the {t}"


def row(**k):
    base = {"team": "CAR", "games": 10, "missed": 25, "rank": 1, "tied": [], "ahead": [], "median": 8.0,
            "now": 4, "now_rank": 3, "now_tied": 0, "names": [(1, "Seth Jarvis"), (2, "Taylor Hall"), (3, "Jordan Staal"), (4, "Alexander Nikishin")]}
    return {**base, **k}


def test_rank_words():
    assert A.sentence(row(), the) == "Their regulars have missed 25 games this season, the most in the league."
    assert A.rank_clause(row(rank=1, tied=["Oilers"]), the) == "tied with the Oilers for the most in the league"
    assert A.rank_clause(row(rank=2, ahead=["Wild"]), the) == "second only to the Wild"
    assert A.rank_clause(row(rank=3, ahead=["Wild", "Flyers"]), the) == "the third-most in the league"


def test_only_when_it_stands_out():
    assert A.stands_out(row(rank=4)) is None
    assert A.stands_out(row(games=5)) is None
    assert A.stands_out(row(missed=14, median=8.0)) is None  # under twice the usual club
    assert A.stands_out(row(rank=9, now=6, now_rank=1)) == "now"
    assert A.sentence(row(rank=9, now=6, now_rank=1), the) == "No team has more regulars out right now."
    assert A.stands_out(row(rank=9, now=6, now_rank=1, now_tied=1)) is None


def test_intro_names_three():
    assert A.intro_line(row(), "the Hurricanes", the) == ("They’ve also been missing a lot of regulars: 25 games so far, the most in the league, "
                                                          "with Seth Jarvis, Taylor Hall and Jordan Staal still out.")
    assert A.intro_line(None, "the Hurricanes", the) is None


def test_filling_text():
    x = {"pid": 9, "draws": 12, "before": 0.95, "centers": [1, 2]}
    N = {9: "Jordan Martinook", 1: "Jordan Staal", 2: "Mark Jankowski"}
    assert A.filling_text(x, "the Hurricanes", N) == ("With Jordan Staal and Mark Jankowski out, Jordan Martinook took twelve faceoffs "
                                                      "in the Hurricanes’ last game. He had been taking fewer than one a game.")
