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


def _pbp(tmp_path, plays, gid=2026020107):
    import gzip
    d = tmp_path / "2026" / "pbp"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{gid}.json.gz").write_bytes(gzip.compress(json.dumps({"homeTeam": {"id": 21}, "plays": plays}).encode()))


def test_league_reviewed_goals_taken_off(tmp_path, monkeypatch):
    """DAL at COL 2026-10-14 as the feed has it: an offside challenge by Dallas, then a league review of Colorado's
    kicked goal (no reason given, neutral-zone restart on Colorado's side, Colorado shot two seconds before)."""
    monkeypatch.setattr(stories, "RAW", tmp_path)
    p1 = {"number": 1}

    def play(t, kind, **det):
        return {"typeDescKey": kind, "timeInPeriod": t, "periodDescriptor": p1, "homeTeamDefendingSide": "right", "details": det}
    plays = [play("01:55", "stoppage", reason="chlg-vis-off-side"), play("01:55", "faceoff", xCoord=-20),
             play("09:27", "shot-on-goal", xCoord=-66, eventOwnerTeamId=21),
             play("09:29", "stoppage", reason="referee-or-linesman", secondaryReason="video-review"), play("09:29", "faceoff", xCoord=-20),
             # an end-zone restart: the review found no goal, so nothing came off
             play("12:00", "shot-on-goal", xCoord=80, eventOwnerTeamId=25),
             play("12:01", "stoppage", reason="video-review", secondaryReason="video-review"), play("12:01", "faceoff", xCoord=69),
             # a penalty review restarting in the neutral zone is not a goal
             play("15:00", "shot-on-goal", xCoord=-60, eventOwnerTeamId=21),
             play("15:02", "penalty"), play("15:02", "stoppage", reason="video-review"), play("15:02", "faceoff", xCoord=-20)]
    _pbp(tmp_path, plays)
    out = stories._overturned(2026020107, 2026)
    assert [(x["home"], x["why"], x["sec"]) for x in out] == [(True, "for offside", 115), (True, "after a video review", 569)]
