"""Projected starters from NHL.com, the rest-edge call and milestone lines."""
from pipeline.export import previews, stories
from pipeline.ingest import nhl_previews
from pipeline.metrics import starters

BODY = """Hurricanes projected lineup

**Hurricanes projected lineup**

Andrei Svechnikov -- Sebastian Aho -- Seth Jarvis

Jaccob Slavin -- Jalen Chatfield

Frederik Andersen

Pyotr Kochetkov

**Flyers projected lineup**

Owen Tippett -- Sean Couturier -- Travis Konecny

Rasmus Ristolainen -- Travis Sanheim

Emil Andrae

Samuel Ersson

Dan Vladař

**Scratched:** Nobody
"""
KNOWN = {"CAR": {"frederik andersen": 1, "pyotr kochetkov": 2}, "PHI": {"samuel ersson": 3}, "*": {"dan vladar": 4}}


def test_reads_the_first_goalie_listed_for_each_team():
    assert nhl_previews.goalie_names(BODY)[1] == ["Emil Andrae", "Samuel Ersson", "Dan Vladař"]
    # a seventh defenseman listed alone is skipped because he isn't a goalie we know
    assert nhl_previews.starters_from(BODY, "CAR", "PHI", KNOWN) == {"CAR": 1, "PHI": 3}


def test_url_uses_full_names_and_the_date():
    assert nhl_previews.url_for("Carolina Hurricanes", "Montréal Canadiens", "2026-10-11") == \
        "https://www.nhl.com/news/carolina-hurricanes-montreal-canadiens-game-preview-october-11-2026"


def test_nhl_pick_goes_first_and_the_rest_share_what_is_left():
    out = starters._promote([(1, 0.7), (2, 0.2), (3, 0.1)], 2)
    assert out[0] == (2, starters.NHL_CHANCE)
    assert [p for p, _ in out] == [2, 1, 3]
    assert abs(sum(q for _, q in out) - 1) < 0.01


SCHED = [{"date": "2026-10-10", "away": "AAA", "home": "CCC"}, {"date": "2026-10-08", "away": "BBB", "home": "DDD"}]
GAME = {"date": "2026-10-11", "away": "AAA", "home": "BBB"}
PLACES = {"AAA": "Aville", "BBB": "Btown", "CCC": "Ctown", "DDD": "Dtown"}


def test_rest_days_counts_days_off_and_where_they_played():
    r = previews.rest_days(SCHED, GAME)
    assert r["AAA"] == {"days": 0, "at": "CCC", "home": False}
    assert r["BBB"]["days"] == 2


def test_rest_call_backs_the_rested_team_against_its_own_usual():
    c = previews.rest_claim(1, "AAA", "BBB", PLACES, previews.rest_days(SCHED, GAME), 0.48)
    assert c["kind"] == "rest" and c["team"] == "BBB" and c["opp"] == "AAA"
    assert c["check"]["baseline"] == 0.48 and c["check"]["direction"] == "above"
    both_tired = {"AAA": {"days": 0, "at": "CCC", "home": False}, "BBB": {"days": 0, "at": "DDD", "home": False}}
    assert previews.rest_claim(1, "AAA", "BBB", PLACES, both_tired, 0.48) is None
    assert previews.rest_claim(1, "AAA", "BBB", PLACES, previews.rest_days(SCHED, GAME), None) is None


def _career(gp, goals, points):
    games = [(f"2020-01-{1 + i % 28:02d}", 0, 0) for i in range(gp)]
    games[0] = ("2020-01-01", goals, points)
    return games


def test_milestones():
    assert stories.milestone(_career(799, 10, 20), "2026-10-11")[1] == "is set to play his 800th NHL game"
    assert stories.milestone(_career(399, 10, 20), "2026-10-11") is None  # games only count from 500
    assert stories.milestone(_career(450, 399, 600), "2026-10-11")[1] == "is one goal from 400"
    assert stories.milestone(_career(450, 50, 148), "2026-10-11") is None  # 98 assists: too early for an assist milestone
    assert stories.milestone(_career(450, 100, 298), "2026-10-11")[1] == "is two points from 300"
    assert stories.milestone(_career(450, 120, 317), "2026-10-11") is None
    # games on or after the day don't count
    assert stories.milestone(_career(450, 399, 600) + [("2026-10-11", 1, 1)], "2026-10-11")[1] == "is one goal from 400"
