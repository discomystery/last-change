import json
import re
from datetime import datetime, timedelta, timezone

from pipeline.export import previews, stories
from pipeline.tests.test_previews import _site


def test_call_bodies_carry_no_numbers(tmp_path, monkeypatch):
    """Sora's rule: the written parts are numbers-light; every figure sits in `nums`, under the call."""
    monkeypatch.setattr(previews, "SITE_DATA", tmp_path)
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    _site(tmp_path, (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z"))
    previews.run(now)
    snap = json.loads((tmp_path / "previews" / "1.json").read_text())
    for c in snap["claims"]:
        assert not re.search(r"\d", c["body"]), c["body"]
        assert not re.search(r"\d", c["head"]), c["head"]
        assert re.search(r"\d", c["nums"]) and c["gist"]


def test_ranks_in_words():
    assert previews.standing(1) == "the league’s best"
    assert previews.standing(4) == "one of the league’s best"
    assert previews.standing(16) == "around the middle of the league"
    assert previews.standing(32) == "the league’s worst"


def test_streaks_and_records():
    res = [{"win": True, "end": "REG"}, {"win": False, "end": "OT"}, {"win": True, "end": "REG"}, {"win": True, "end": "SO"}]
    assert stories.streak(res) == ("won", 2)
    assert stories.streak(res[:2]) == ("winless", 1)
    assert stories.record(res) == "3-0-1"


def test_scores_read_like_a_fan_says_them():
    r = {"us": 2, "them": 3, "end": "OT"}
    assert stories.score_text(r) == "3-2 in overtime"
    assert stories.score_text({"us": 5, "them": 2, "end": "REG"}, win=True) == "5-2 win"
    assert stories.score_text(r, win=True) == "3-2 overtime win"
    assert stories.num(3) == "three" and stories.num(14) == "14"


def test_goal_times_in_words():
    assert stories._when({"period": 1, "sec": 78}) == "1:18 in"
    assert stories._when({"period": 1, "sec": 45}) == "45 seconds in"
    assert stories._when({"period": 2, "sec": 1000}) == "late in the second"
    assert stories._when({"period": 4, "sec": 63}) == "1:03 into overtime"


def test_new_york_teams_read_as_they():
    from pipeline.export.outlook import _agree
    assert _agree("the Islanders has controlled the play and the Rangers is playing") == "The Islanders have controlled the play and the Rangers are playing"
    assert _agree("the Rangers’s best hope") == "The Rangers’ best hope"


def test_shared_surnames_get_initials():
    assert previews._lasts(["Aliaksei Protas", "Ilya Protas", "Jordan Kyrou"]) == ["A. Protas", "I. Protas", "Kyrou"]


def test_goal_streaks_lead():
    assert stories.run_of([1, 0, 2, 1, 1]) == 3
    assert stories.streak_text(4, 4, 10) == "scored in four straight games"
    assert stories.streak_text(5, 5, 5) == "scored in every game this season"
    assert stories.streak_text(3, 7, 10) == "a point in seven straight games, with a goal in each of the last three"
    assert stories.streak_text(2, 6, 10) == "a point in six straight games"
    assert stories.streak_text(3, 3, 9, " against Dallas") == "scored in three straight games against Dallas"


def test_a_clubs_absences_share_one_out_item():
    """Sora, 2026-10-15: Staal and Hall were left out of WPG-CAR; names only, newly out first."""
    outs = [{"score": 76, "pid": 1, "who": "Seth Jarvis", "missed": 8, "last_played": None},
            {"score": 75, "pid": 2, "who": "Taylor Hall", "missed": 0, "last_played": "2026-10-13"},
            {"score": 74, "pid": 3, "who": "Jordan Staal", "missed": 2, "last_played": "2026-10-10"},
            {"score": 73, "pid": 4, "who": "Alexander Nikishin", "missed": 8, "last_played": None}]
    item = stories._merge_out(outs, "The Hurricanes", 8)
    assert item["text"] == ("The Hurricanes are newly without Taylor Hall and Jordan Staal, and have had to make do without "
                            "Seth Jarvis and Alexander Nikishin all season.")
    assert item["pids"] == [1, 2, 3, 4] and item["tag"] == "Out"
    one = stories._merge_out([{**outs[0], "start": "Stuart Skinner"}], "The Jets", 8)
    assert one["text"] == "The Jets have been without Seth Jarvis all season. Stuart Skinner is expected to start."
    mid = stories._merge_out([{**outs[2], "missed": 6}], "The Hurricanes", 8)
    assert mid["text"] == "The Hurricanes have been without Jordan Staal since Oct 10."


def test_a_fill_in_who_scored_in_the_missing_players_spot(tmp_path, monkeypatch):
    """Sora, 2026-10-15: "Jankowski made up for Staal's absence with a goal ..." Facts about tonight only."""
    import polars as pl
    d = tmp_path / "2026"
    d.mkdir()
    pl.DataFrame({"game_id": [9, 9, 9], "team_id": [12, 12, 12], "kind": ["F", "F", "F"], "unit": ["2-3-4", "2-3-5", "6-7-8"],
                  "sec": [400.0, 30.0, 500.0]}).write_parquet(d / "game_units.parquet")
    pl.DataFrame({"game_id": [1, 2, 9, 9], "player_id": [1, 1, 4, 5], "team_id": [12] * 4, "g": [0.0, 0.0, 1.0, 0.0],
                  "a1": [0.0] * 4, "a2": [0.0] * 4, "fow": [6.0, 6.0, 3.0, 0.0], "fol": [4.0, 4.0, 7.0, 0.0]}).write_parquet(d / "player_game.parquet")
    monkeypatch.setattr(stories, "TABLES", tmp_path)
    monkeypatch.setattr(stories, "_out_notes", lambda s, day: {"CAR": [
        {"pid": 1, "games": 2, "last_played": "2026-10-10", "partners": [2, 3], "grp": "F"}]})
    monkeypatch.setattr(stories, "_roles_on", lambda s, day: {12: {1: {"toi_rank": 7}, 4: {"toi_rank": 9}}})
    N = {1: "Jordan Staal", 4: "Mark Jankowski"}
    item = stories._filled_in(9, "2026-10-13", "CAR", 12, 2026, N, lambda pid: N[pid])
    assert item["text"] == "With Jordan Staal out, Mark Jankowski took his spot and scored, though he won only 3 of 10 faceoffs."
    monkeypatch.setattr(stories, "_roles_on", lambda s, day: {12: {1: {"toi_rank": 7}, 4: {"toi_rank": 2}}})
    assert stories._filled_in(9, "2026-10-13", "CAR", 12, 2026, N, lambda pid: N[pid]) is None  # a bigger player moved over
