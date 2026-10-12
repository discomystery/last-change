import polars as pl

from pipeline.export import penalties


class W:
    def poss(self, t):
        return f"the {t}’s"


def _pen(sec, p1, team, typ, desc, mins, period=1):
    return {"game_id": 1, "period": period, "period_type": "REG", "sec": sec, "team_id": team, "p1": p1,
            "pen_type": typ, "pen_desc": desc, "pen_min": mins}


def _notes(monkeypatch, pens, few=2, many=14):
    df = pl.DataFrame(pens, schema={"game_id": pl.Int64, "period": pl.Int64, "period_type": pl.Utf8, "sec": pl.Int64, "team_id": pl.Int64,
                                    "p1": pl.Int64, "pen_type": pl.Utf8, "pen_desc": pl.Utf8, "pen_min": pl.Int64})
    monkeypatch.setattr(penalties, "rows", lambda season: df)
    monkeypatch.setattr(penalties, "norms", lambda: {"few": few, "many": many, "games": 100, "at_most": {0: 0.01, 1: 0.01, 2: 0.05},
                                                      "at_least": {k: 0.02 for k in range(30)}})
    monkeypatch.setattr(penalties, "_coach", lambda *a: None)
    return penalties.recap_notes(1, 2026, {10: "AAA", 20: "BBB"}, W(), lambda pid: f"P{pid}")


def test_quiet_game_one_player_took_them_all(monkeypatch):
    out = _notes(monkeypatch, [_pen(100, 7, 10, "MIN", "tripping", 2), _pen(900, 7, 10, "MIN", "hooking", 2)])
    assert out == ["Only two penalties were called all night, and P7 took both."]


def test_a_fight_names_both_players(monkeypatch):
    out = _notes(monkeypatch, [_pen(500, 1, 10, "MAJ", "fighting", 5), _pen(500, 2, 20, "MAJ", "fighting", 5)] +
                 [_pen(60 * i, 3 + i, 10, "MIN", "tripping", 2) for i in range(4)])
    assert len(out) == 1 and "P1 and P2" in out[0] and "midway through the first" in out[0]


def test_line_brawl_names_every_pair(monkeypatch):
    brawl = [_pen(2, i, 10 if i % 2 else 20, "MAJ", "fighting", 5) for i in range(1, 7)]
    out = _notes(monkeypatch, brawl + [_pen(2, i, 10, "MIS", "misconduct", 10) for i in (1, 3)] + [_pen(600, 9, 10, "MIN", "slashing", 2)] * 3)
    assert out[0] == "A line brawl broke out early in the first, three fights at once, with two misconducts: P1 with P2, P3 with P4 and P5 with P6."


def test_ejection_and_busy_player(monkeypatch):
    pens = [_pen(100 * i, 4, 10, "MIN", d, 2) for i, d in enumerate(["tripping", "hooking", "slashing"], 1)]
    pens += [_pen(2500, 5, 20, "MIN", "boarding", 2), _pen(2500, 5, 20, "GAM", "game-misconduct", 10, period=3)]
    pens += [_pen(3000 + i, 30 + i, 20, "MIN", "holding", 2, period=3) for i in range(3)]
    out = _notes(monkeypatch, pens)
    assert out[0].startswith("P5 was thrown out with a game misconduct")
    assert out[1] == "P4 took three penalties on his own."


def test_rarity_in_round_numbers():
    assert penalties._every(1 / 87) == "about once every 90 games"
    assert penalties._every(1 / 21) == "about once every 20 games"
    assert penalties._every(1 / 7) == "about once every 7 games"


def test_a_fight_is_always_told(monkeypatch):
    """Sora: fighting is part of hockey, so a fight is never crowded out by other notes."""
    pens = [_pen(500, 1, 10, "MAJ", "fighting", 5), _pen(500, 2, 20, "MAJ", "fighting", 5),
            _pen(2500, 5, 20, "GAM", "game-misconduct", 10, period=3), _pen(2600, 6, 10, "MAT", "match-penalty", 5, period=3)]
    pens += [_pen(3000 + i, 30 + i, 20, "MIN", "holding", 2, period=3) for i in range(14)]
    out = _notes(monkeypatch, pens)
    assert any("P1 and P2" in o for o in out) and len(out) == 2
