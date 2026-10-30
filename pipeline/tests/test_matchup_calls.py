"""Matchup calls (rules 25): forward lines only, a habit that holds up for its sample, both lines together in their
club's last game, and a bar halfway between rotation and the habit."""
from pipeline.export import previews

PLACES = {"CAR": "Carolina", "CBJ": "Columbus"}


def _team(prefix: int, last_ids=None, rows=None, games=5):
    units = [{"label": f"L{k}", "players": [f"A{prefix}{k}{i}" for i in range(3)], "ids": [prefix * 100 + k * 10 + i for i in range(3)]} for k in (1, 2, 3, 4)]
    units += [{"label": f"P{k}", "players": [f"D{prefix}{k}{i}" for i in range(2)], "ids": [prefix * 100 + 50 + k * 10 + i for i in range(2)]} for k in (1, 2, 3)]
    last = [{"label": u["label"], "ids": u["ids"]} for u in units] if last_ids is None else last_ids
    return {"units": units, "last": {"units": last},
            "matchups": {"home": {"games": games, "rows": rows or []}}}


def _row(label, share, expected=(29, 28, 27, 16), minutes=35):
    return {"label": label, "minutes": minutes, "share": list(share), "expected": list(expected)}


def test_bar_is_halfway_and_pairs_are_left_out():
    home = _team(1, rows=[_row("L1", (58, 20, 12, 10)), _row("P1", (80, 10, 5, 5))])
    c = previews.matchup_claim(1, "CBJ", "CAR", {"CAR": home, "CBJ": _team(2)}, PLACES)
    assert c["check"]["unit"] == "L1" and c["check"]["opp_line"] == "L1"
    assert c["check"]["bar"] == 44 and c["check"]["baseline"] == 58 and c["check"]["threshold"] == 29
    assert "at least 44%" in c["call"]


def test_early_habit_needs_a_bigger_lean():
    # 3 home games: a 25-point lean scales to 25 * 3/7 = 10.7, under the bar of 12
    rows = [_row("L1", (54, 20, 16, 10))]
    assert previews.matchup_claim(1, "CBJ", "CAR", {"CAR": _team(1, rows=rows, games=3), "CBJ": _team(2)}, PLACES) is None
    assert previews.matchup_claim(1, "CBJ", "CAR", {"CAR": _team(1, rows=rows, games=10), "CBJ": _team(2)}, PLACES) is not None


def test_a_line_split_up_last_game_gets_no_call():
    rows = [_row("L1", (58, 20, 12, 10))]
    opp = _team(2)
    split = [{"label": "L1", "ids": [opp["units"][0]["ids"][0], 299, 298]}]  # only one of their first line's forwards together
    assert previews.matchup_claim(1, "CBJ", "CAR", {"CAR": _team(1, rows=rows), "CBJ": _team(2, last_ids=split)}, PLACES) is None
    two = [{"label": "L1", "ids": [*opp["units"][0]["ids"][:2], 299]}]  # two of three still together: still their line
    assert previews.matchup_claim(1, "CBJ", "CAR", {"CAR": _team(1, rows=rows), "CBJ": _team(2, last_ids=two)}, PLACES) is not None
