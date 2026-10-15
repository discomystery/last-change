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

