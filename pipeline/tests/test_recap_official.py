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
