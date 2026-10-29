from collections import defaultdict

from pipeline.export import matchups as M


def test_label_needs_two_forwards_or_one_defenseman():
    sets = {"L1": {1, 2, 3}, "L2": {4, 5, 6}, "P1": {10, 11}}
    assert M.label(sets, [1, 2, 9], "L") == "L1"
    assert M.label(sets, [1, 4, 9], "L") is None  # one from each line is nobody's line
    assert M.label(sets, [10, 12], "P") == "P1"


def test_short_names_add_an_initial_only_on_a_clash():
    rows = [{"player_id": 1, "first": "Aliaksei", "last": "Protas"}, {"player_id": 2, "first": "Ilya", "last": "Protas"},
            {"player_id": 3, "first": "Alex", "last": "Ovechkin"}]
    assert M.short_names(rows) == {1: "A. Protas", 2: "I. Protas", 3: "Ovechkin"}


def test_matched_share_counts_mixed_time():
    sec = defaultdict(float)
    sec[("all", 1, "L1", "L1")] = 300
    sec[("all", 1, "L1", "L2")] = 100
    sec[("all", 1, "L1", "mixed")] = 100
    assert M.matched_share({"sec": sec}, "all", 1) == 60.0


def test_and_list():
    assert M.and_list(["Aho"]) == "Aho"
    assert M.and_list(["Carrier", "Aho", "Svechnikov"]) == "Carrier, Aho and Svechnikov"
