"""Goalie streaks in post-game stories (Sora, 2026-10-30: Kochetkov's nine straight wins to open the season)."""
from pipeline.export import goalie_streaks as gs, previews, stories


def _words():
    previews.NICKS.clear()
    previews.NICKS.update({"CAR": "Hurricanes", "CBJ": "Blue Jackets", "OTT": "Senators", "PHI": "Flyers"})
    W = stories.Words({"CAR": "Carolina", "CBJ": "Columbus", "OTT": "Ottawa", "PHI": "Philadelphia"})
    previews.NICKS.clear()
    return W


def test_runs_count_decisions_and_overtime_losses_end_win_runs():
    r = gs._runs(["L", "W", "O", "W", "W", "W"])
    assert r["win"] == 3 and r["points"] == 5 and r["points_rec"] == [4, 0, 1]
    assert gs._runs(["W", "L", "O", "L"])["winless"] == 3
    assert gs._runs(["W", "W"])["win"] == 2 == gs._runs(["W", "W"])["n"]  # all his decisions: an opening run


def test_best_finds_opening_and_single_season_records_with_every_holder():
    seqs = [(1, "Frederik Andersen", 2021, ["W"] * 8 + ["L"]), (2, "Cam Ward", 2008, ["L"] + ["W"] * 9),
            (3, "Brandon Bussi", 2025, ["W"] * 9), (4, "Anton Khudobin", 2013, ["W"] * 6 + ["T"])]
    best_open, open_by, best_run, run_by = gs._best(seqs)
    assert best_open == 9 and [b[1] for b in open_by] == ["Brandon Bussi"]
    assert best_run == 9 and sorted(b[1] for b in run_by) == ["Brandon Bussi", "Cam Ward"]


KOCH = 8481611


def _fact(**kw):
    base = {"pid": KOCH, "team": "CAR", "dec": "W", "win": 9, "opening": True, "points": 9, "points_rec": [9, 0, 0], "ended": 0,
            "ended_opening": False, "winless": 0, "winless_rec": [0, 0, 0], "record": [], "league": None,
            "unit": {"win": "starts", "points": "starts", "winless": "starts"}, "every_start": True}
    return {**base, **kw}


def _story(monkeypatch, facts, winner="CAR", loser="CBJ"):
    monkeypatch.setattr(gs, "for_game", lambda *a, **k: facts)
    N = {KOCH: "Pyotr Kochetkov", 11: "Frederik Andersen", 12: "Cam Ward", 13: "Brandon Bussi", 14: "Linus Ullmark", 15: "Jack Campbell"}
    said = set()

    def name(pid):
        out = N[pid] if pid not in said else N[pid].split(" ", 1)[1]
        said.add(pid)
        return out
    return gs.story(1, 2026, N, name, _words(), winner, loser, lambda pool, *k: pool[0])


def test_kochetkov_nine_straight_sets_the_opening_record_ties_the_season_record_and_leads(monkeypatch):
    rec = [{"kind": "opening", "how": "set", "mark": 8, "run": 9, "by": [(11, "Frederik Andersen", 2021)], "since": "in franchise history"},
           {"kind": "run", "how": "tied", "mark": 9, "run": 9, "by": [(12, "Cam Ward", 2008), (13, "Brandon Bussi", 2025)],
            "since": "in franchise history"}]
    out = _story(monkeypatch, [_fact(record=rec, league={"since": 2020, "from": 1918, "mark": 11, "by": [(15, "Jack Campbell", 11)]})])
    assert out["sents"] == [
        "Pyotr Kochetkov has won all nine of his starts this season, the longest winning run to open a season by any goalie "
        "in franchise history, one more than Frederik Andersen (2021-22).",
        "He also tied the franchise record for straight wins in one season, shared by Cam Ward (2008-09) and Brandon Bussi (2025-26).",
        "No NHL goalie had opened a season with this many straight wins since Jack Campbell won his first eleven in 2020-21."]
    assert out["heads"] == [(95, "Kochetkov wins his first nine, a Hurricanes record")]


def test_a_plain_run_is_one_modest_sentence_and_heads_only_from_eight(monkeypatch):
    out = _story(monkeypatch, [_fact(win=6, opening=False, points=6)])
    assert out["sents"] == ["Pyotr Kochetkov has won six straight starts."] and out["heads"] == []
    out = _story(monkeypatch, [_fact(win=8, opening=False, points=8, unit={"win": "decisions", "points": "decisions", "winless": "starts"})])
    assert out["sents"] == ["Pyotr Kochetkov has won eight straight decisions."]
    assert out["heads"] == [(84, "Kochetkov wins his eighth straight")]
    assert _story(monkeypatch, [_fact(win=4, opening=True, points=4)])["sents"] == []


def test_a_run_that_ends_and_a_winless_run(monkeypatch):
    out = _story(monkeypatch, [_fact(pid=14, team="OTT", dec="L", win=0, opening=False, ended=6, ended_opening=True)], winner="PHI", loser="OTT")
    assert out["sents"] == ["It was Linus Ullmark’s first loss of the season, after six straight wins to open it."]
    out = _story(monkeypatch, [_fact(pid=14, team="OTT", dec="O", win=0, opening=False, ended=0, winless=7, winless_rec=[0, 6, 1])],
                 winner="PHI", loser="OTT")
    assert out["items"][0]["text"] == "Linus Ullmark is winless in his last seven starts (0-6-1)."
