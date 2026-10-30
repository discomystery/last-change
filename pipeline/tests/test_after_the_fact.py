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
    # one sentence for both clubs (voice loop, round two)
    assert line.startswith("The Hurricanes ") and ", third in the Metropolitan, while the Devils " in line
    assert line.endswith(("the Devils fell to 8-5-0 but climbed to second in the Metropolitan.",
                          "the Devils dropped to 8-5-0 but climbed to second in the Metropolitan."))


def test_a_winner_that_moved_reads_as_a_verb():
    W = _words()
    G = pl.DataFrame({"date": ["2026-10-28"], "game_id": [1]})
    orig = stories.results, stories.record
    stories.results, stories.record = (lambda played, t, season: []), (lambda res: "8-3-1")
    try:
        line = stories.record_line(G, "2026-10-28", "TBL", "WPG", W, 2026, 1, set(),
                                   {"TBL": ("climbing to third in the Atlantic", "climbed to third in the Atlantic"),
                                    "WPG": ("seventh in the Central", "are seventh in the Central")})
    finally:
        stories.results, stories.record = orig
    assert " to 8-3-1 and climbed to third in the Atlantic, while the WPG " in line and line.endswith("8-3-1, seventh in the Central.")


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


def _line(standing):
    W = _words()
    G = pl.DataFrame({"date": ["2026-10-28"], "game_id": [1]})
    orig = stories.results, stories.record
    stories.results, stories.record = (lambda played, t, season: t), (lambda res: {"CAR": "10-2-2", "BOS": "9-3-1", "NJD": "8-5-0"}[res])
    try:
        return stories.record_line(G, "2026-10-28", "CAR", standing["_loser"], W, 2026, 1, set(),
                                   {k: v for k, v in standing.items() if k != "_loser"})
    finally:
        stories.results, stories.record = orig


def test_a_place_taken_straight_from_the_club_they_beat_is_one_sentence():
    W = _words()
    was = stories._table_marks((_row("CAR", "Metropolitan", 1, 13, 20), _row("BOS", "Atlantic", 1, 12, 19),
                                _row("PHI", "Metropolitan", 2, 13, 10), _row("NJD", "Metropolitan", 3, 12, 9)))
    now = stories._table_marks((_row("CAR", "Metropolitan", 1, 14, 22), _row("BOS", "Atlantic", 1, 13, 19),
                                _row("PHI", "Metropolitan", 3, 13, 10), _row("NJD", "Metropolitan", 2, 13, 11)))
    got = {t: stories._standing(t, o, now, was, W) for t, o in (("CAR", "BOS"), ("BOS", "CAR"))}
    swap = stories._swap("CAR", "BOS", now, was, (), None, False, got, set(), W)
    assert swap[0] == "took the best points percentage in the NHL from"
    line = _line({**got, "_swap": swap, "_loser": "BOS"})
    assert line.count("Bruins") == 1 and ("who fell to 9-3-1." in line or "who dropped to 9-3-1." in line)
    assert line.endswith("That leaves the Hurricanes first in the NHL in both points and points percentage.")


def test_division_first_and_places_swapped_with_the_club_they_beat():
    W = _words()
    rows = lambda seqs: tuple(_row(t, "Metropolitan", s, 10, p) for t, s, p in seqs) + (_row("BOS", "Atlantic", 1, 10, 30), _row("XYZ", "Atlantic", 2, 10, 2))
    was = stories._table_marks(rows([("CAR", 2, 14), ("NJD", 1, 15), ("PHI", 3, 12)]))
    now = stories._table_marks(rows([("CAR", 1, 16), ("NJD", 2, 15), ("PHI", 3, 13)]))
    got = {t: stories._standing(t, o, now, was, W) for t, o in (("CAR", "NJD"), ("NJD", "CAR"))}
    swap = stories._swap("CAR", "NJD", now, was, (), None, False, got, set(), W)
    assert swap == ("took first place in the Metropolitan from", None)
    assert _line({**got, "_swap": swap, "_loser": "NJD"}).split(" and took ")[1].startswith("first place in the Metropolitan from the Devils, who ")
    was = stories._table_marks(rows([("CAR", 3, 12), ("NJD", 2, 13), ("PHI", 1, 20)]))
    now = stories._table_marks(rows([("CAR", 2, 14), ("NJD", 3, 13), ("PHI", 1, 20)]))
    swap = stories._swap("CAR", "NJD", now, was, (), None, False, {}, set(), W)
    assert swap == ("took second place in the Metropolitan from", None)
    # a club that didn't take the loser's place keeps the two lines
    now = stories._table_marks(rows([("CAR", 3, 14), ("NJD", 2, 15), ("PHI", 1, 20)]))
    assert stories._swap("CAR", "NJD", now, was, (), None, False, {}, set(), W) is None


def test_a_club_pushed_to_the_bottom_by_the_club_that_passed_it():
    line = _line({"CAR": ("climbing to seventh in the Metropolitan", "climbed to seventh in the Metropolitan"),
                  "NJD": ("sinking to the bottom of the Metropolitan", "sank to the bottom of the Metropolitan"),
                  "_swap": ("took seventh place in the Metropolitan from", None), "_loser": "NJD"})
    assert line.endswith("8-5-0 and sank to the bottom of the division.")
