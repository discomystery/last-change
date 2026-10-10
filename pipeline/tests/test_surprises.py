from pipeline.export import surprises


def test_classify_tells_a_break_from_a_bigger_version_of_the_habit():
    # Usually 55% of the shots (league middle 50, night-to-night spread 8).
    assert surprises.classify(55, 40, 50, 8) == "break"
    assert surprises.classify(55, 70, 50, 8) == "further"
    assert surprises.classify(58, 51, 50, 8) == "muted"
    assert surprises.classify(50.5, 70, 50, 8) == "unusual"


def test_pick_one_per_kind_and_never_empty():
    c = lambda kind, score: {"kind": kind, "score": score, "tag": "x", "head": kind, "body": ""}
    shown = surprises.pick([c("shots", 3.0), c("shots", 2.5), c("goalie", 2.0), c("lineup", 1.2), c("chances", 0.4)])
    assert [s["kind"] for s in shown] == ["shots", "goalie"]
    quiet = surprises.pick([c("lineup", 1.2), c("chances", 0.4)])
    assert [s["kind"] for s in quiet] == ["lineup"] and quiet[0]["mild"]
    assert surprises.pick([c("chances", 0.4)])[0]["kind"] == "form"
