from pipeline.export import absences as A

the = lambda t: f"the {t}"


def row(**k):
    base = {"team": "CAR", "games": 10, "missed": 25, "rank": 1, "tied": [], "ahead": [], "median": 8.0, "lost": 1.6, "lost_median": 0.6,
            "now": 4, "now_rank": 3, "now_tied": 0, "names": [(1, "Seth Jarvis"), (2, "Taylor Hall"), (3, "Jordan Staal"), (4, "Alexander Nikishin")]}
    return {**base, **k}


def test_rank_words():
    assert A.sentence(row(), the) == "Their regulars have missed 25 games this season, and no team has been hit harder by absences."
    assert A.sentence(row(rank=3, ahead=["Islanders", "Senators"]), the) == (
        "Their regulars have missed 25 games this season, and only the Islanders and the Senators have been hit harder by absences.")
    assert A.than_whom(row(rank=2, ahead=["Islanders"]), the) == "than anyone but the Islanders"


def test_only_when_it_stands_out():
    assert A.stands_out(row(rank=4)) is None
    assert A.stands_out(row(games=5)) is None
    assert A.stands_out(row(lost=1.0)) is None  # under twice what the usual club has lost
    assert A.stands_out(row(missed=5)) is None
    assert A.stands_out(row(rank=9, now=6, now_rank=1)) == "now"
    assert A.sentence(row(rank=9, now=6, now_rank=1), the) == "No team has more regulars out right now."
    assert A.stands_out(row(rank=9, now=6, now_rank=1, now_tied=1)) is None


def test_intro_names_three():
    assert A.intro_line(row(), "the Hurricanes", the) == ("They’ve also been hit harder by absences than any other team: their regulars have "
                                                          "missed 25 games so far, with Seth Jarvis, Taylor Hall and Jordan Staal still out.")
    assert A.intro_line(None, "the Hurricanes", the) is None


def test_filling_text():
    x = {"pid": 9, "draws": 12, "before": 0.95, "centers": [1, 2]}
    N = {9: "Jordan Martinook", 1: "Jordan Staal", 2: "Mark Jankowski"}
    assert A.filling_text(x, "the Hurricanes", N) == ("With Jordan Staal and Mark Jankowski out, Jordan Martinook took twelve faceoffs "
                                                      "in the Hurricanes’ last game. He had been taking fewer than one a game.")
