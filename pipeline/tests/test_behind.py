import polars as pl

from pipeline.export import behind


def _row(**kw):
    r = {"team_id": 1, "ab": "AAA", "gp": 12, "pts": 19, "gd": 9, "ev": -1.6, "st": 5.2, "fin": 11.6, "gk": -8.2, "en": 2,
         "pp": 0.79, "play": 0.6, "gap": 0.19, "close": 1.0}
    r.update(kw)
    return r


EMPTY = pl.DataFrame({"player_id": [], "team_id": [], "g": [], "xg": [], "p": [], "gax": []},
                     schema={"player_id": pl.Int64, "team_id": pl.Int64, "g": pl.Int64, "xg": pl.Float64, "p": pl.Int64, "gax": pl.Float64})
TEAM = {"abbr": "AAA", "place": "Aville", "nick": "Aces"}


def test_parts_add_up_to_goal_difference():
    d = behind.team_games(2025)
    so = (d["last_period"] == "SO").cast(pl.Int64) * (d["gf"] - d["ga"]).sign()  # the shootout winner's extra goal
    assert ((d["gf"] - d["ga"] - so) == d["gd"]).all()
    gd = d["gd"].cast(pl.Float64)
    total = d["ev"] + d["st"] + d["fin"] + d["gk"] + d["en"]
    assert (gd - total).abs().max() < 1e-6


def test_reasons_follow_the_side_of_the_gap():
    r = _row()
    assert [k for _, k in behind.reasons(r, 1)] == ["fin"]  # goaltending pulled the other way
    assert [k for _, k in behind.reasons(r, -1)] == ["gk"]


def test_big_gap_names_the_reason_and_small_gap_says_so():
    out = behind.team_checks(TEAM, _row(), EMPTY, [], None, {})
    assert out["verdict"] == "ahead" and out["why"] == ["fin"]
    assert "Most of the gap is shooting" in out["checks"][0]["text"]
    assert "Goaltending has cost them" in out["checks"][1]["text"]
    near = behind.team_checks(TEAM, _row(pp=0.62, gap=0.02, fin=1.0, gk=1.0), EMPTY, [], None, {})
    assert near["verdict"] == "level" and near["checks"][0]["text"].startswith("Aville’s record (.620) is about")


def test_goalie_split_and_names_only_for_what_is_said():
    goalies = [{"id": 5, "season": {"starts": 7, "gsax": 4.2}}, {"id": 6, "season": {"starts": 6, "gsax": -4.3}}]
    out = behind.team_checks(TEAM, _row(pp=0.6, gap=0.0, fin=0.0, gk=0.0), EMPTY, goalies, None, {5: "Al One", 6: "Bo Two", 7: "Cy Three"})
    assert out["tags"] == ["Goalies pulling apart"]
    assert {n["name"] for n in out["names"]} == {"Al One", "Bo Two"}


def test_no_luck_or_prediction_words():
    for kw in ({}, {"gap": -0.15, "pp": 0.45, "play": 0.6, "fin": -6.0, "gk": 3.0}):
        out = behind.team_checks(TEAM, _row(**kw), EMPTY, [], None, {})
        text = " ".join(c["text"] for c in out["checks"]).lower()
        for word in ("luck", "deserve", "will ", "should "):
            assert word not in text
