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
    assert stories._wipe_after(off + [{"period": 3, "sec": 500}], [], True, False, 1, "SO", pk) == " Those disallowed goals stung: the game went to a shootout."
    assert stories._wipe_after(off + [{"period": 1, "sec": 500}], [], True, False, 1, "SO", pk) == ""  # first-period wipes didn't decide an overtime game
    assert stories._wipe_after(off, [g(1, 145, True)], True, True, 2, "REG", pk) == " They got it back 45 seconds later."
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


# --- the turn of the game (outside coverage of COL 5-2 PIT and BOS 4-2 ANA, 2026-10-16) ------------------------------

class _W:
    nick = {"BOS": "Bruins", "ANA": "Ducks", "COL": "Avalanche", "PIT": "Penguins"}

    def the(self, t):
        return f"the {self.nick[t]}"


def _g(period, sec, home, p1, hs, as_, strength="ev"):
    return {"period": period, "sec": sec, "home": home, "p1": p1, "hs": hs, "as": as_, "strength": strength, "p2": None}


# BOS (away) at ANA: two Anaheim goals 21 seconds apart, then three Boston power-play goals and an insurance goal
BOS_ANA = [_g(1, 1162, True, 1, 1, 0), _g(1, 1183, True, 2, 2, 0), _g(2, 195, False, 3, 2, 1, "pp"),
           _g(3, 206, False, 4, 2, 2, "pp"), _g(3, 498, False, 5, 2, 3, "pp"), _g(3, 980, False, 6, 2, 4)]
SIDE = lambda g: "ANA" if g["home"] else "BOS"


def test_the_first_goal_back_sparks_a_comeback():
    assert stories.spark(BOS_ANA, SIDE, "BOS", "ANA") == (3, 1)
    # Lehkonen scored both goals that erased Pittsburgh's 2-0 lead
    col = [_g(1, 508, False, 1, 0, 1), _g(1, 537, False, 2, 0, 2), _g(1, 661, True, 9, 1, 2), _g(2, 25, True, 9, 2, 2), _g(2, 264, True, 7, 3, 2)]
    assert stories.spark(col, lambda g: "COL" if g["home"] else "PIT", "COL", "PIT") == (9, 2)


def test_late_insurance_and_pouring_it_on():
    assert stories.insurance(BOS_ANA, SIDE, "BOS")["p1"] == 6
    assert stories.late_rush(BOS_ANA, SIDE, "BOS")
    # a two-goal lead cut back to one is no insurance
    assert stories.insurance(BOS_ANA + [_g(3, 1100, True, 8, 3, 4)], SIDE, "BOS") is None


def test_slow_start_needs_the_shots_to_turn():
    periods = [{"goals": {"PIT": 2, "COL": 1}, "shots": {"PIT": 14, "COL": 5}}, {"goals": {"PIT": 0, "COL": 3}, "shots": {"PIT": 10, "COL": 18}},
               {"goals": {"PIT": 0, "COL": 1}, "shots": {"PIT": 7, "COL": 9}}]
    assert stories.slow_start(periods, "COL", "PIT", _W()) == \
        "The Avalanche had five shots in the first period and trailed 2-1, then outshot the Penguins 27-17 the rest of the way."
    periods[1]["shots"] = {"PIT": 14, "COL": 15}
    assert stories.slow_start(periods, "COL", "PIT", _W()) is None


def test_power_plays_that_turned_the_game():
    nums = {"BOS": {"pp_goals": 3, "pp_opps": 4}, "ANA": {"pp_goals": 0, "pp_opps": 5}}
    names = {3: "Frédéric Brunet", 4: "David Pastrňák", 5: "Elias Lindholm"}
    first, last = stories.special_teams(nums, BOS_ANA, SIDE, "BOS", "ANA", _W(), names.get, True)
    assert "Frédéric Brunet, David Pastrňák and Elias Lindholm all scored on the power play as the Bruins turned a two-goal hole into the lead" in first[0]
    assert first[0].endswith("and the Ducks went 0-for-5 on theirs.") and not last
    # no comeback: the power play and the kill are told on their own
    first, last = stories.special_teams(nums, BOS_ANA, SIDE, "BOS", "ANA", _W(), names.get, False)
    assert first == ["Special teams did the work: the Bruins scored three power-play goals on four chances and killed all five Ducks power plays."] \
        or first == ["Special teams did the work: the Bruins went 3-for-4 on the power play and killed all five Ducks power plays."]
    quiet = {"BOS": {"pp_goals": 1, "pp_opps": 2}, "ANA": {"pp_goals": 0, "pp_opps": 3}}
    assert stories.special_teams(quiet, BOS_ANA, SIDE, "BOS", "ANA", _W(), names.get, False) == ([], [])


def test_first_goal_and_goalie_first_details():
    pre, post = stories.first_goal_detail({"undrafted": True, "nth": 8, "debut": False}, {"shot": "wrist"}, "MacKinnon")
    assert f"T.J. Hughes{pre} scored his first NHL goal{post}." == \
        "T.J. Hughes, undrafted and in his eighth NHL game, scored his first NHL goal, set up by MacKinnon."
    assert stories.first_goal_detail({"nth": 1, "debut": True}, {"shot": "tip-in"}, "McNabb") == ("", ", tipping in a feed from McNabb")
    win = {"kind": "first_win", "start_n": 4, "seasons": 8, "started": True}
    assert stories.goalie_first_text(win, "Michael DiPietro", 25) == \
        "Michael DiPietro finally got his first NHL win, making 25 saves in his fourth start, eight seasons after his NHL debut."
    assert stories.goalie_first_text({**win, "start_n": 1, "seasons": 0}, "A", 30) == "A got his first NHL win, making 30 saves in his first NHL start."
    assert stories.goalie_first_text({"kind": "win_since", "since": "2025-03-03"}, "A", 20) == "It was A’s first NHL win since Mar 3, 2025."


def test_season_defining_runs_lead_the_story():
    """Sora, 2026-10-18: the Flyers' first win after a 0-6-3 start was missing from the headline and the story."""
    W = stories.Words({"PHI": "Philadelphia", "OTT": "Ottawa"})
    P, O = stories._cap(W.the("PHI")), stories._cap(W.the("OTT"))  # "The Flyers", unless another test changed the names
    pk = lambda pool, *k: pool[0]
    loss = lambda end="REG": {"win": False, "end": end}
    win = {"win": True, "end": "REG"}
    phi = [loss()] * 6 + [loss("OT")] * 3 + [win]
    heads, lead, told = stories._run_story(phi, [win] * 6 + [loss()] * 3, "PHI", "OTT", W, pk)
    assert max(h[0] for h in heads) >= 95 and heads[0][1] == f"{P} finally win one"
    assert lead == [f"{P} won for the first time this season, at the tenth try, after starting 0-6-3."]
    assert told == {"PHI"}
    # the loser's winless start: told at the close, and the headline still carries the result
    heads, lead, told = stories._run_story([win] * 5, phi[:-1], "OTT", "PHI", W, pk)
    assert heads[0][1] == f"{O} keep {W.the('PHI')} winless" and lead[0].startswith(f"{P} are still looking")
    # a short run is not the story
    assert stories._run_story([loss(), loss(), win], [win, win, loss()], "PHI", "OTT", W, pk) == ([], [], set())
    assert stories.defining_run(phi[:-1]) and not stories.defining_run([win, loss(), loss()])


def test_a_delayed_penalty_goal_is_not_a_power_play_goal(tmp_path, monkeypatch):
    """Outside notes, 2026-10-18: Fantilli's goal at 19:12 of the first (CBJ at MIN, 2026020135) came 6-on-5 with the
    Columbus net empty during a delayed penalty (code 0651) and read "on the power play"."""
    import polars as pl
    d = tmp_path / "2026"
    d.mkdir()
    row = dict(event_id=1, period_type="REG", p2=None, p3=None, home_goalie=30, away_goalie=31, zone="O", shot_type="wrist")
    pl.DataFrame([
        row | dict(game_id=2026020135, sort=1, period=1, sec=1152, type="goal", team_id=29, p1=7, situation_code="0651"),
        row | dict(game_id=2026020135, sort=2, period=1, sec=1152, type="penalty", team_id=30, p1=8, situation_code="0651"),
        row | dict(game_id=2026020135, sort=3, period=2, sec=1500, type="goal", team_id=29, p1=7, situation_code="1541"),
    ]).write_parquet(d / "events.parquet")
    monkeypatch.setattr(stories, "TABLES", tmp_path)
    goals = stories._goals(2026020135, 2026, home_id=30)
    assert [g["strength"] for g in goals] == ["dp", "pp"]
    assert not goals[0]["own_extra"]  # first period: not "with the goalie pulled"


def test_set_up_wins_needs_most_goals_off_low_rated_passes():
    from pipeline.export.stories import set_up_wins
    g = lambda p2, xg, home=True, strength="ev": {"home": home, "p2": p2, "xg": xg, "strength": strength}
    assert set_up_wins([g(1, 0.04), g(2, 0.06), g(None, 0.3)], True)
    assert not set_up_wins([g(1, 0.04), g(None, 0.05), g(None, 0.3)], True)  # one set-up goal is not enough
    assert not set_up_wins([g(1, 0.04), g(2, 0.06)], False)  # the other side's goals
    assert not set_up_wins([g(1, 0.04), g(2, 0.25), g(3, 0.3)], True)
