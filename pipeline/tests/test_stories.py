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


def test_goals_taken_off_get_a_follow_up():
    pk = lambda pool: pool[0]
    off = [{"period": 1, "sec": 100}]
    g = lambda per, sec, home, st="ev": {"period": per, "sec": sec, "home": home, "strength": st}
    assert stories._wipe_after(off, [g(2, 50, False)], True, False, 1, "REG", pk) == " That one stung in a one-goal loss."
    assert stories._wipe_after(off + [{"period": 1, "sec": 500}], [], True, False, 1, "SO", pk) == " Those stung in a game that went to a shootout."
    assert stories._wipe_after(off, [g(1, 145, True)], True, True, 2, "REG", pk) == " They got it back just 45 seconds later."
    assert stories._wipe_after(off, [g(2, 10, True, "pp")], True, True, 2, "REG", pk) == " The power play gave it back."
    assert stories._wipe_after(off, [g(2, 10, True)], True, True, 2, "REG", pk) == " It didn’t cost them in the end."
    assert stories._wipe_after(off, [g(2, 10, False)], True, False, 4, "REG", pk) == ""  # a rout: the goal didn't decide it


def test_one_second_left_is_singular():
    assert stories._left({"sec": 1199}) == "1 second"
    assert stories._left({"sec": 1180}) == "20 seconds"
    assert stories._when({"period": 2, "sec": 1}) == "1 second into the second period" or "1 second" in stories._when({"period": 1, "sec": 1})


def _players(rows):
    import polars as pl
    return pl.DataFrame(rows, schema={"player_id": pl.Int64, "game_id": pl.Int64, "date": pl.Utf8, "g": pl.Int64, "a1": pl.Int64, "a2": pl.Int64, "season": pl.Int64})


def test_consecutive_hat_tricks_are_counted():
    P = _players([(1, 10, "2026-10-13", 3, 0, 1, 2026), (1, 11, "2026-10-15", 3, 0, 0, 2026), (2, 11, "2026-10-15", 3, 0, 0, 2026)])
    assert stories._hat_run(P, 1, 11) == 2
    assert stories._hat_run(P, 2, 11) == 1


def test_drought_counts_games_played_and_names_a_long_gap():
    rows = [(1, 1, "2026-02-28", 1, 0, 0, 2025)] + [(1, 2 + i, f"2026-03-{1 + i:02d}", 0, 0, 0, 2025) for i in range(5)]
    rows += [(1, 20 + i, f"2026-10-{1 + i:02d}", 0, 1, 0, 2026) for i in range(8)] + [(1, 40, "2026-10-15", 1, 0, 0, 2026)]
    P = _players(rows)
    text = stories._drought(P, 1, 40, "2026-10-15")
    assert text.startswith(", his first since Feb 28") and "13 games played since" in text
    # a short gap in the same season: games, not a date
    P2 = _players([(1, 1, "2026-10-01", 1, 0, 0, 2026)] + [(1, 2 + i, f"2026-10-{2 + i:02d}", 0, 0, 0, 2026) for i in range(11)] + [(1, 30, "2026-10-14", 1, 0, 0, 2026)])
    assert stories._drought(P2, 1, 30, "2026-10-14") == ", his first in eleven games"
    assert stories._drought(_players([(1, 30, "2026-10-14", 1, 0, 0, 2026)]), 1, 30, "2026-10-14") == ""


def test_neutral_clock_for_an_exit():
    from pipeline.export import surprises
    assert surprises.clock_at(1200 + 620) == "10:20 of the second period"
    assert surprises.clock_at(1200) == "the first period"
