from pipeline.export import recaps


def test_official_counts_replace_shots_and_saves_but_not_goals():
    nums = {"BUF": {"shots": 29}, "PHI": {"shots": 22}}
    glines = [{"id": 1, "shots": 29, "saves": 24, "ga": 5, "xga": 3.1, "gsax": -1.9, "sv": 0.828}]
    recaps.use_official(nums, glines, {"sog": {"BUF": 30}, "goalies": {1: (30, 25)}})
    assert nums["BUF"]["shots"] == 30 and nums["PHI"]["shots"] == 22
    assert glines[0]["saves"] == 25 and glines[0]["ga"] == 5 and glines[0]["sv"] == 0.833


def test_no_box_changes_nothing():
    glines = [{"id": 1, "shots": 29, "saves": 24, "ga": 5}]
    recaps.use_official({}, glines, {})
    assert glines[0]["shots"] == 29


def test_goalie_blame_softens_when_goals_came_off_passes():
    from pipeline.export import surprises
    gl = {"team": "SJS", "full": "Anton Forsberg", "name": "A. Forsberg", "ga": 5, "shots": 25, "xga": 1.7, "gsax": -3.3, "_set_up": 3}
    places = {"SJS": "San Jose", "LAK": "Los Angeles"}
    won = {"SJS": False, "LAK": True}
    item = surprises.goalie_night(gl, places, {"SJS": 2, "LAK": 5}, won)
    assert "lit up" not in item["head"] and "came off a pass" in item["body"]
    item = surprises.goalie_night({**gl, "_set_up": 1}, places, {"SJS": 2, "LAK": 5}, won)
    assert item["head"] == "Anton Forsberg got lit up"
