"""Post-game stories updated after the game (Sora, 2026-10-29): the injury news on a player who left, and where each
club landed in the standings once the night's table is in."""
from types import SimpleNamespace

import polars as pl

from pipeline.export import previews, recaps, stories, timeline
from pipeline.ingest import standings_daily as sd


def _words():
    previews.NICKS.clear()
    previews.NICKS.update({"BOS": "Bruins", "CAR": "Hurricanes", "PHI": "Flyers", "NJD": "Devils"})
    W = stories.Words({"BOS": "Boston", "CAR": "Carolina", "PHI": "Philadelphia", "NJD": "New Jersey"})
    previews.NICKS.clear()
    return W


def _row(t, div, seq, gp, pts, conf="E"):
    return {"teamAbbrev": t, "conferenceAbbrev": conf, "conferenceName": "Eastern", "divisionAbbrev": div[0],
            "divisionName": div, "divisionSequence": seq, "conferenceSequence": None, "wildcardSequence": None,
            "gamesPlayed": gp, "points": pts, "regulationWins": 0, "clinchIndicator": None}


def test_carolina_takes_first_in_both_and_boston_gives_up_the_best_points_percentage():
    W = _words()
    was = stories._table_marks((_row("CAR", "Metropolitan", 1, 13, 20), _row("BOS", "Atlantic", 1, 12, 19),
                                _row("PHI", "Metropolitan", 2, 13, 10), _row("NJD", "Metropolitan", 3, 12, 9)))
    now = stories._table_marks((_row("CAR", "Metropolitan", 1, 14, 22), _row("BOS", "Atlantic", 1, 13, 19),
                                _row("PHI", "Metropolitan", 3, 13, 10), _row("NJD", "Metropolitan", 2, 13, 11)))
    assert stories._standing("CAR", "BOS", now, was, W)[0] == "taking sole possession of first in the NHL in both points and points percentage"
    assert stories._standing("BOS", "CAR", now, was, W)[0] == "giving up the best points percentage in the NHL to the Hurricanes"
    assert stories._standing("NJD", "PHI", now, was, W)[0] == "climbing out of last place in the NHL"
    assert stories._standing("PHI", "NJD", now, was, W)[0] == "sinking to last in the NHL"


def test_division_moves_and_the_bottom():
    W = _words()
    rows = lambda seqs: tuple(_row(t, "Metropolitan", s, 10, p) for t, s, p in seqs) + (_row("BOS", "Atlantic", 1, 10, 30), _row("XYZ", "Atlantic", 2, 10, 2))
    was = stories._table_marks(rows([("CAR", 2, 14), ("NJD", 1, 15), ("PHI", 3, 12)]))
    now = stories._table_marks(rows([("CAR", 1, 16), ("NJD", 2, 15), ("PHI", 3, 13)]))
    assert stories._standing("CAR", "NJD", now, was, W)[0] == "climbing into first in the Metropolitan"
    assert stories._standing("NJD", "CAR", now, was, W)[0] == "losing first place in the Metropolitan to the Hurricanes"
    assert stories._standing("PHI", "BOS", now, was, W) == ("still last in the Metropolitan", "are still last in the Metropolitan")


def test_a_loser_that_still_climbed_reads_with_but():
    W = _words()
    G = pl.DataFrame({"date": ["2026-10-28"], "game_id": [1]})
    orig = stories.results, stories.record
    stories.results, stories.record = (lambda played, t, season: []), (lambda res: "8-5-0")
    try:
        line = stories.record_line(G, "2026-10-28", "CAR", "NJD", W, 2026, 1, set(),
                                   {"CAR": ("third in the Metropolitan", "are third in the Metropolitan"),
                                    "NJD": ("climbing to second in the Metropolitan", "climbed to second in the Metropolitan")})
    finally:
        stories.results, stories.record = orig
    assert line.endswith("The Devils fell to 8-5-0 but climbed to second in the Metropolitan.") or \
        line.endswith("The Devils dropped to 8-5-0 but climbed to second in the Metropolitan.")


def test_injury_news_wording():
    assert timeline.injury_news({"status": "ir", "days": 2, "injury": "lower body", "timeline": None}) == \
        " Two days later, he was placed on injured reserve with a lower-body injury."
    assert timeline.injury_news({"status": "day", "days": 1, "injury": "upper-body", "timeline": "day to day"}) == \
        " The next day, he was listed as day to day with an upper-body injury."
    assert timeline.injury_news({"status": "long", "days": 2, "injury": None, "timeline": "out 4-6 weeks"}) == \
        " Two days later, he was ruled out for 4-6 weeks."
    assert timeline.injury_news(None) == ""


def test_injury_news_takes_the_most_telling_report_from_the_next_two_days():
    av = pl.DataFrame({"date": ["2026-10-26"] * 4, "player_id": [9] * 4,
                       "report_status": ["day", "day", "ir", "ir"],
                       "report_date": ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"],
                       "report_injury": [None, "upper-body", "neck injury", None], "report_timeline": [None] * 4})
    S = SimpleNamespace(games={5: {"date": "2026-10-26"}}, _availability=lambda: av)
    got = recaps.Season.injury_news(S, 5, 9)
    assert got == {"status": "ir", "days": 2, "injury": "upper-body", "timeline": None}
    # a report from the morning of the game (he played through it) is not the news
    S2 = SimpleNamespace(games={5: {"date": "2026-10-26"}}, _availability=lambda: av.head(1))
    assert recaps.Season.injury_news(S2, 5, 9) is None


def test_a_standings_table_missing_the_nights_games_is_not_kept():
    played = {"CAR": ["2026-10-25", "2026-10-27"], "BOS": ["2026-10-27"]}
    rows = [{"teamAbbrev": "CAR", "gamesPlayed": 1}, {"teamAbbrev": "BOS", "gamesPlayed": 0}]
    assert not sd._complete(rows, "2026-10-27", played)
    assert sd._complete(rows, "2026-10-26", played)
