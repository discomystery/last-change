"""Strength of schedule: each game's difficulty adds up its parts, ranks run hardest first, and the next ten are
the first ten unplayed games."""
from pipeline.export import schedule_strength as ss


def _sched():
    # Team 1 plays team 2 (strong) at home on day 1, then at team 3 (weak) on day 2 (a back-to-back), and so on.
    games, gid = [], 1
    for d in range(1, 15):
        opp = 2 if d % 2 else 3
        games.append({"game_id": gid, "date": f"2026-11-{d:02d}", "home_id": 1 if d % 2 else opp, "away_id": opp if d % 2 else 1})
        gid += 1
    return games


def test_parts_add_up_and_rest_counts():
    sched = _sched()
    rows = ss.game_rows(sched, played={1, 2}, morning={1: {1: 0.0, 2: 0.5}, 2: {1: 0.0, 3: -0.5}}, now={2: 0.8, 3: -0.8}, home=0.2, b2b=-0.4)
    for r in rows.iter_rows(named=True):
        assert abs(r["hard"] - (r["opp"] + r["venue"] + r["rest"])) < 1e-9
    first = rows.filter((rows["team_id"] == 1) & (rows["game_id"] == 1)).row(0, named=True)
    assert first["opp"] == 0.5 and first["venue"] == -0.2  # played: the opponent as it was that morning; home is easier
    second = rows.filter((rows["team_id"] == 1) & (rows["game_id"] == 2)).row(0, named=True)
    assert second["b2b"] == 1 and second["venue"] == 0.2
    later = rows.filter((rows["team_id"] == 1) & (rows["game_id"] == 3)).row(0, named=True)
    assert later["opp"] == 0.8  # still to come: the opponent's rating now


def test_summary_ranks_hardest_first_and_next_ten():
    sched = _sched()
    rows = ss.game_rows(sched, played=set(), morning={}, now={1: 0.0, 2: 0.8, 3: -0.8}, home=0.2, b2b=-0.4)
    s = ss.summarise(rows)
    assert s.filter(s["team_id"] == 1)["next_games"][0] == ss.NEXT
    # Teams 2 and 3 play each other never and team 1 every night: team 3 faces team 1 (0.0), team 2 too; team 1 faces
    # a mix of 0.8 and -0.8. Ranks are 1..3 with 1 the largest difficulty.
    top = s.sort("left_hard", descending=True)
    assert top["left_rank"].to_list()[0] == 1
