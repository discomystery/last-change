"""The post-game story's chronology (export/timeline.py)."""
from pipeline.export import timeline


class W:
    nick = {"MTL": "Canadiens", "NYR": "Rangers"}
    places = {"MTL": "Montréal", "NYR": "NY Rangers"}

    def the(self, t):
        return f"the {self.nick[t]}"

    def poss(self, t):
        return self.the(t) + "’"

    def place(self, t):
        return self.places[t] if not self.places[t].startswith("NY ") else self.the(t)


NAMES = {1: "Alex Newhook", 2: "Cole Caufield", 3: "Adam Fox", 4: "Will Cuylle", 5: "Nick Suzuki"}


def _game(goals, end="REG", **kw):
    said = set()

    def name(pid):
        if pid in said:
            return NAMES[pid].split(" ")[-1]
        said.add(pid)
        return NAMES[pid]
    hs = as_ = 0
    out = []
    for per, sec, home, p1, st in goals:
        hs, as_ = hs + home, as_ + (not home)
        out.append({"period": per, "sec": sec, "home": home, "p1": p1, "p2": None, "p3": None, "strength": st, "hs": hs, "as": as_,
                    "extra": st == "en", "goalie": False, "zone": "O", "own_extra": False})
    winner = "NYR" if hs > as_ else "MTL"
    return timeline.Game(goals=out, winner=winner, loser="MTL" if winner == "NYR" else "NYR", home="NYR", away="MTL", end=end, W=W(),
                         name=name, last=lambda p: NAMES[p].split(" ")[-1], said=said, N=NAMES, pk=lambda pool, *k: pool[0], **kw)


def test_goals_are_told_in_order_and_runs_merge():
    # MTL at NYR, 2026-04-02 as it went: two quick MTL goals, two NYR goals to tie, Caufield's second 47 s later
    gm = _game([(2, 130, False, 1, "ev"), (2, 234, False, 2, "ev"), (3, 500, True, 3, "ev"), (3, 785, True, 4, "ev"), (3, 832, False, 2, "ev")])
    out = timeline.paragraph(gm)
    assert out == ["Alex Newhook and Cole Caufield scored less than two minutes apart early in the second to put the Canadiens up 2-0.",
                   "Adam Fox and Will Cuylle scored less than five minutes apart midway through the third to tie it 2-2.",  # no "luck ran out" when the go-ahead came right back
                   "Caufield struck again 47 seconds later to put the Canadiens ahead for good."]


def test_first_goal_rides_on_the_goal_and_a_late_cut_says_held_on():
    gm = _game([(1, 300, True, 3, "pp"), (2, 100, True, 4, "ev"), (3, 1100, False, 5, "ev")])
    gm.attach = {0: {"pre": "scored his first NHL goal"}}
    out = timeline.paragraph(gm)
    assert out[0] == "Adam Fox scored his first NHL goal on the power play early in the first to open the scoring for the Rangers."
    assert out[1] == "Will Cuylle made it 2-0 early in the second, and that one stood up as the winner."  # the official winner in a 2-1 game
    assert out[2] == "Nick Suzuki pulled the Canadiens within one with less than two minutes left, but the Rangers held on."


def test_empty_net_and_overtime_beats():
    gm = _game([(1, 500, False, 1, "ev"), (3, 1150, False, 2, "en")])
    assert timeline.paragraph(gm)[-1].endswith("Cole Caufield sealed it into the empty net with just under a minute left.")
    ot = _game([(2, 100, True, 3, "ev"), (3, 400, False, 1, "ev"), (4, 75, True, 4, "ev")], end="OT")
    assert timeline.paragraph(ot)[-1] == "Will Cuylle won it a little over a minute into overtime."


def test_only_goal_and_hat_trick_words():
    assert timeline.paragraph(_game([(2, 700, True, 3, "sh")])) == ["Adam Fox scored the only goal of the game shorthanded midway through the second."]
    gm = _game([(1, 100, True, 3, "ev"), (2, 100, False, 1, "ev"), (2, 900, True, 3, "ev"), (3, 1000, True, 3, "ev")])
    text = " ".join(timeline.paragraph(gm))
    assert "struck again" in text and any(x in text for x in ("completed his hat trick with less than four minutes left to make it 3-1.", "added insurance with less than four minutes left.", "made it 3-1 with less than four minutes left, and that was that."))


def test_shootout_decider_is_the_goal_the_loser_could_not_match():
    # the winner's LAST goal was named before; here the loser's one goal came after the winner's second
    tries = [{"tid": 1, "pid": 11, "scored": True}, {"tid": 2, "pid": 21, "scored": False},
             {"tid": 1, "pid": 12, "scored": True}, {"tid": 2, "pid": 22, "scored": True},
             {"tid": 1, "pid": 13, "scored": False}, {"tid": 2, "pid": 23, "scored": False}]
    pid, how, scorers = timeline.so_winner(tries, 1)
    assert (pid, how, scorers) == (12, "the shootout winner", [11, 12])
    only = [{"tid": 1, "pid": 11, "scored": True}, {"tid": 2, "pid": 21, "scored": False}, {"tid": 1, "pid": 12, "scored": False},
            {"tid": 2, "pid": 22, "scored": False}, {"tid": 1, "pid": 13, "scored": False}, {"tid": 2, "pid": 23, "scored": False}]
    assert timeline.so_winner(only, 1)[:2] == (11, "the only goal of the shootout")
    long = [{"tid": 1, "pid": 10 + i, "scored": False} for i in range(1, 5)]
    long = [x for i in range(1, 5) for x in ({"tid": 1, "pid": 10 + i, "scored": False}, {"tid": 2, "pid": 20 + i, "scored": False})]
    long += [{"tid": 1, "pid": 15, "scored": True}, {"tid": 2, "pid": 25, "scored": False}]
    assert timeline.so_winner(long, 1)[:2] == (15, "the only goal of the shootout")


def test_ordinals():
    assert [timeline.th(n) for n in (1, 2, 3, 11, 12, 21, 102, 1000)] == ["1st", "2nd", "3rd", "11th", "12th", "21st", "102nd", "1,000th"]


def test_short_sentences_join_in_pairs_and_periods_are_not_repeated():
    # Sora's review, 2026-10-19: clipped one-goal sentences in a row read choppy; join pairs, never two joins in a row
    gm = _game([(1, 300, False, 1, "ev"), (1, 700, True, 3, "ev"), (2, 500, True, 4, "ev"), (2, 900, False, 2, "ev")])
    out = timeline.paragraph(gm)
    assert out[0] == "Alex Newhook opened the scoring for the Canadiens early in the first, but Adam Fox tied it 1-1 for the Rangers midway through the period."
    assert len(out) == 3 and "the period" in out[0]


def test_runs_stay_inside_one_period():
    # CAR at CHI: a goal late in the first and two early in the second were told as "three goals in 4:25 late in the first"
    gm = _game([(1, 1150, True, 3, "ev"), (2, 60, True, 4, "ev"), (2, 140, True, 5, "ev")])
    out = " ".join(timeline.paragraph(gm))
    assert "three goals" not in out and "late in the first" in out


def test_return_rides_on_his_goal_unless_a_star():
    base = {"kind": "return", "goals": 1, "weight": 60}
    assert timeline.rides_on_goal(base)
    assert not timeline.rides_on_goal({**base, "weight": 88})
    assert not timeline.rides_on_goal({**base, "goals": 0})
    assert not timeline.rides_on_goal({**base, "since": "2025-04-01"})


def test_a_long_stretch_without_a_shot_is_told_where_it_began_with_its_periods_named():
    # Boston at Carolina, 2026-10-27: 20:39 without a shot from 7:41 of the second (Sora, 2026-10-29)
    d = {"team": "MTL", "start": 1200 + 461, "end": 2400 + 500, "gap": 1239, "other": 12, "pp": 0, "ot": False, "rare": "season"}
    gm = _game([(2, 400, True, 3, "ev"), (3, 700, True, 4, "ev")], droughts=[d])
    out = timeline.paragraph(gm)
    assert out[1] == ("The Canadiens went more than 20 minutes without a shot on goal, from early in the second to midway through the third, "
                      "while the Rangers put twelve on net.")  # not "from early in the period": the periods stay named
    assert out[2] == "Most teams don’t go that long without a shot even once a season."
    assert out[3].startswith("Will Cuylle made it 2-0")


def test_shot_droughts_from_puck_drop_and_to_the_horn():
    a = {"team": "MTL", "start": 0, "end": 1100, "gap": 1100, "other": 3, "pp": 120, "ot": False, "rare": None}
    b = {"team": "NYR", "start": 2400 + 200, "end": 3600, "gap": 1000, "other": 5, "pp": 0, "ot": True, "rare": None}
    assert timeline._drought(_game([]), a) == ["\x01The Canadiens didn’t get their first shot on goal until late in the first, "
                                               "more than 18 minutes into the game, even with a power play in that stretch."]
    assert timeline._drought(_game([]), b) == ["\x01The Rangers didn’t get a shot on goal over the final 16 minutes of regulation."]
