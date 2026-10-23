"""The player page's bio line: what the NHL landing gives, trimmed and spelled for the page."""
from pipeline.export.players import _bio
from pipeline.ingest.careers import _trim_landing, stale

LANDING = {"playerId": 8480039, "heightInInches": 74, "heightInCentimeters": 188, "weightInPounds": 189, "weightInKilograms": 86,
           "birthDate": "1999-01-15", "birthCity": {"default": "Nove Mesto na Morave", "cs": "Nové Město na Moravě"},
           "birthStateProvince": None, "birthCountry": "CZE", "shootsCatches": "R",
           "draftDetails": {"year": 2017, "teamAbbrev": "CAR", "round": 1, "pickInRound": 12, "overallPick": 12}}


def test_bio_from_landing():
    b = _bio(_trim_landing(LANDING))
    assert b == {"ht": 74, "cm": 188, "wt": 189, "kg": 86, "born": "1999-01-15", "city": "Nové Město na Moravě", "country": "CZE",
                 "draft": {"year": 2017, "team": "CAR", "round": 1, "overall": 12}}


def test_region_only_in_north_america_and_undrafted():
    b = _bio(_trim_landing({**LANDING, "birthCity": {"default": "Mosinee"}, "birthStateProvince": {"default": "Wisconsin"},
                            "birthCountry": "USA", "draftDetails": None}))
    assert (b["city"], b["region"], b["draft"]) == ("Mosinee", "Wisconsin", None)
    assert "region" not in _bio(_trim_landing({**LANDING, "birthStateProvince": {"default": "Moravia"}}))


def test_landing_without_bio_is_fetched_again():
    old = {k: v for k, v in _trim_landing(LANDING).items() if k != "bio"}
    assert _bio(old) is None
    assert stale({"fetched": "2026-10-01", "landing": old})
    assert not stale({"fetched": "2026-10-01", "landing": _trim_landing(LANDING)})
