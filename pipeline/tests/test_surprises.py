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


def test_a_call_that_held_big_is_not_a_surprise_and_a_flipped_one_names_the_call():
    from pipeline.export import surprises as sr
    held = {"id": "1-1", "kind": "clash", "metric": "quality", "team": "STL", "opp": "SJS", "head": "St. Louis gets to the dangerous areas", "verdict": "held", "check": {}}
    missed = {"id": "1-2", "kind": "edge", "metric": "pp", "team": "BOS", "opp": "WPG", "head": "Boston’s power play outclasses Winnipeg’s",
              "verdict": "missed", "check": {"team": "BOS", "opp": "WPG"}}
    cands = [{"kind": "chances", "score": 2.25, "head": "St. Louis got to the dangerous areas", "body": "b1", "sig": ("quality", "STL", True)},
             {"kind": "special", "score": 2.0, "head": "Winnipeg’s power play came alive", "body": "b2", "sig": ("pp", "WPG", True)},
             {"kind": "shots", "score": 2.0, "head": "San Jose buried St. Louis in shots", "body": "b3", "sig": ("shots", "SJS", True)}]
    kept, beyond = sr.against_calls(cands, [held, missed])
    assert beyond == {"1-1": {"head": "St. Louis got to the dangerous areas", "body": "b1", "score": 2.25}}
    assert [c["head"] for c in kept] == ["Winnipeg’s power play came alive", "San Jose buried St. Louis in shots"]
    assert kept[0]["flipped"] == "Boston’s power play outclasses Winnipeg’s" and "flipped" not in kept[1]
    assert all("sig" not in c for c in sr.pick(kept))
