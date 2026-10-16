"""Record-book notes on player pages (export/record_book.py)."""
from pipeline.export import record_book


def test_tied_with_one_ahead():
    t = record_book.sentence("Sebastian Aho", 18, [("Leon Draisaitl", 20)], ["Sidney Crosby", "Brad Marchand"],
                             record_book.WHAT["otg3"], record_book.WHERE["otg3"])
    assert t == ("Sebastian Aho is tied for second in overtime goals since the NHL went to 3-on-3 overtime in 2015-16, with 18, "
                 "level with Sidney Crosby and Brad Marchand, behind Leon Draisaitl (20).")


def test_far_down_names_the_next_one_up():
    ahead = [(f"P{i}", 900 - i) for i in range(31)]
    t = record_book.sentence("Connor McDavid", 824, ahead, [], "assists", " in NHL history")
    assert t == "Connor McDavid ranks 32nd in assists in NHL history, with 824, 46 behind P30."


def test_leaders_and_active():
    assert record_book.sentence("Alex Ovechkin", 930, [], [], "goals", " in NHL history") == "Alex Ovechkin has the most goals in NHL history, with 930."
    t = record_book.sentence("Sidney Crosby", 1765, [("A", 2857), ("B", 1921), ("C", 1887)], [], "points", " in NHL history", active_first=True)
    assert t.endswith("That’s the most of any active player.")


def test_club_lists():
    assert record_book.sentence("Sidney Crosby", 1765, [], [], "points", club="the Penguins’") == \
        "Sidney Crosby is the Penguins’ all-time leader in points, with 1,765."
    assert record_book.sentence("Sebastian Aho", 724, [("Ron Francis", 1175), ("Eric Staal", 775)], [], "points", club="the Hurricanes’") == \
        "Sebastian Aho ranks third on the Hurricanes’ all-time scoring list, with 724 points, behind Ron Francis (1,175) and Eric Staal (775)."
    assert record_book.sentence("Kyle Connor", 330, [("A", 400), ("B", 380), ("C", 331)], [], "goals", club="the Jets’") == \
        "Kyle Connor ranks fourth on the Jets’ all-time goals list, with 330 goals, one behind C."



def _x(a, ahead=(), level=(), below=()):
    return {"tag": "NHL history", "stat": "Goals", "scope": "NHL all-time", "below": list(below),
            "args": dict(a=a, ahead=list(ahead), level=list(level), what="goals", where=" in NHL history")}


def _board(t):
    return [f"{'T-' if r['tied'] else ''}{r['rank']} {r['who']}" for r in t["rows"]] + [f"+{r['who']}" for r in t["tied"]]


def test_board_leader_shows_two_below():
    assert _board(record_book.tile(_x(930, below=[("Gretzky", 894), ("Howe", 801), ("Jagr", 766)]), "Ovechkin")) == \
        ["1 Ovechkin", "2 Gretzky", "3 Howe"]


def test_board_third_shows_one_two_three():
    t = record_book.tile(_x(724, ahead=[("Francis", 1175), ("Staal", 775)], below=[("Brind’Amour", 473)]), "Aho")
    assert _board(t) == ["1 Francis", "2 Staal", "3 Aho"]


def test_board_far_down_next_up_and_below():
    ahead = [(f"P{i}", 900 - i) for i in range(31)]
    t = record_book.tile(_x(824, ahead=ahead, below=[("Q", 820), ("R", 810)]), "McDavid")
    assert _board(t) == ["31 P30", "32 McDavid", "33 Q"]


def test_board_ties_one_name_and_more():
    ahead = [(f"P{i}", 30 - i) for i in range(8)]
    t = record_book.tile(_x(17, ahead=ahead, level=["A", "B", "C"], below=[("D", 16)]), "McDavid")
    assert _board(t) == ["8 P7", "T-9 McDavid", "+A", "+B", "+C"]
    t = record_book.tile(_x(18, ahead=[("Draisaitl", 20)], level=["Crosby", "Marchand"], below=[("Scheifele", 17)]), "Aho")
    assert _board(t) == ["1 Draisaitl", "T-2 Aho", "+Crosby", "+Marchand"]
