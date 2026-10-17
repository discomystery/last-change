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
    assert out == ["Alex Newhook and Cole Caufield scored 1:44 apart early in the second to put the Canadiens up 2-0.",
                   "Adam Fox and Will Cuylle scored 4:45 apart midway through the third to tie it 2-2.",
                   "Caufield struck again 47 seconds later to put the Canadiens ahead for good."]


def test_first_goal_rides_on_the_goal_and_a_late_cut_says_held_on():
    gm = _game([(1, 300, True, 3, "pp"), (2, 100, True, 4, "ev"), (3, 1100, False, 5, "ev")])
    gm.attach = {0: {"pre": "scored his first NHL goal"}}
    out = timeline.paragraph(gm)
    assert out[0] == "Adam Fox scored his first NHL goal on the power play early in the first to open the scoring for the Rangers."
    assert out[1] == "Will Cuylle made it 2-0 early in the second, the eventual winner."  # the official winner in a 2-1 game
    assert out[2] == "Nick Suzuki pulled the Canadiens within one with 1:40 left, but the Rangers held on."


def test_empty_net_and_overtime_beats():
    gm = _game([(1, 500, False, 1, "ev"), (3, 1150, False, 2, "en")])
    assert timeline.paragraph(gm)[1] == "Cole Caufield sealed it into the empty net with 50 seconds left."
    ot = _game([(2, 100, True, 3, "ev"), (3, 400, False, 1, "ev"), (4, 75, True, 4, "ev")], end="OT")
    assert timeline.paragraph(ot)[-1] == "Will Cuylle won it 1:15 into overtime."


def test_only_goal_and_hat_trick_words():
    assert timeline.paragraph(_game([(2, 700, True, 3, "sh")])) == ["Adam Fox scored the only goal of the game shorthanded midway through the second."]
    gm = _game([(1, 100, True, 3, "ev"), (2, 100, False, 1, "ev"), (2, 900, True, 3, "ev"), (3, 1000, True, 3, "ev")])
    out = timeline.paragraph(gm)
    assert "struck again" in out[2] and out[3] in ("Fox completed his hat trick with 3:20 left to make it 3-1.", "Fox added insurance with 3:20 left.", "Fox made it 3-1 with 3:20 left, and that was that.")


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
