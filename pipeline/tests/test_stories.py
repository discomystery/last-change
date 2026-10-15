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
    """Sora, 2026-10-15: Staal and Hall were left out of WPG-CAR because two "Out" items a game was the cap."""
    rep = {2: {"status": "injured", "report_status": "out", "report_injury": "upper-body", "report_timeline": None},
           3: {"status": "injured", "report_status": "long", "report_injury": "calf tear", "report_timeline": "out 1-2 months"}}
    N = {1: "Seth Jarvis", 2: "Taylor Hall", 3: "Jordan Staal"}
    outs = [{"tag": "Out", "text": "Seth Jarvis has not played this season.", "score": 76, "pid": 1},
            {"tag": "Out", "text": "Taylor Hall is out.", "score": 75, "pid": 2},
            {"tag": "Out", "text": "Jordan Staal is out long term.", "score": 74, "pid": 3}]
    item = stories._merge_out(outs, rep, N)
    assert item["text"] == ("Seth Jarvis has not played this season. Taylor Hall (upper-body injury) and "
                            "Jordan Staal (calf tear, out 1-2 months) are also out.")
    assert item["pids"] == [1, 2, 3] and item["pid"] == 1
    assert stories._merge_out(outs[:1], rep, N) == outs[0]
