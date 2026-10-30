from pipeline.export import absences as A

the = lambda t: f"the {t}"


def row(**k):
    base = {"team": "CAR", "games": 10, "missed": 25, "rank": 1, "tied": [], "ahead": [], "median": 8.0, "lost": 1.6, "lost_median": 0.6,
            "now": 4, "now_rank": 1, "now_lost": 0.4, "now_median": 0.15, "now_ahead": [], "names": [(1, "Seth Jarvis"), (2, "Taylor Hall"), (3, "Jordan Staal"), (4, "Alexander Nikishin")]}
    return {**base, **k}


def test_season_rank_for_intros():
    assert A.intro_line(row(), "the Hurricanes", the) == (
        "Taking into account how much value each missing player usually adds, no team has been hit harder by absences: their regulars have "
        "missed 25 games so far, with Seth Jarvis, Taylor Hall and Jordan Staal still out.")
    assert A.intro_line(row(rank=2, ahead=["Islanders"]), "the Hurricanes", the).startswith(
        "Taking into account how much value each missing player usually adds, only the Islanders have been hit harder")
    assert A.than_whom(row(rank=2, ahead=["Islanders"]), the) == "than anyone but the Islanders"
    assert A.intro_line(row(rank=4), "the Hurricanes", the) is None
    assert A.intro_line(row(lost=1.0), "the Hurricanes", the) is None  # under twice what the usual club has lost
    assert A.intro_line(row(games=5), "the Hurricanes", the) is None
    assert A.intro_line(None, "the Hurricanes", the) is None


def test_right_now_for_games():
    assert A.sentence(row(), the) == "Taking into account how much value each missing player usually adds, no team is missing more right now."
    assert A.sentence(row(now_rank=3, now_ahead=["Flyers", "Wild"]), the) == "Taking into account how much value each missing player usually adds, only the Flyers and the Wild are missing more right now."
    assert A.sentence(row(now_rank=4), the) is None
    assert A.sentence(row(now_lost=0.25), the) is None  # under twice the median club's
    assert A.sentence(row(now_lost=0.18, now_median=0.05), the) is None  # under the floor
    assert A.postgame_line(row(), "the Hurricanes", True, the) == (
        "The Hurricanes won without Seth Jarvis, Taylor Hall, Jordan Staal and others. "
        "Taking into account how much value each missing player usually adds, no team was missing more at the time.")
    assert A.postgame_line(row(now_rank=2, now_ahead=["Flyers"], names=[(1, "Seth Jarvis")]), "the Hurricanes", False, the) == (
        "The Hurricanes were without Seth Jarvis. "
        "Taking into account how much value each missing player usually adds, only the Flyers were missing more at the time.")


def test_filling_text():
    x = {"pid": 9, "draws": 12, "before": 0.95, "centers": [1, 2]}
    N = {9: "Jordan Martinook", 1: "Jordan Staal", 2: "Mark Jankowski"}
    assert A.filling_text(x, "the Hurricanes", N) == ("With Jordan Staal and Mark Jankowski out, Jordan Martinook took twelve faceoffs "
                                                      "in the Hurricanes’ last game. He had been taking fewer than one a game.")
