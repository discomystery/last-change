from pipeline.export import intros as I

TEAMS = {
    "EDM": {"abbr": "EDM", "name": "Edmonton Oilers", "place": "Edmonton", "nick": "Oilers", "w": 3, "l": 0, "otl": 1, "gp": 4, "pts": 7},
    "SJS": {"abbr": "SJS", "name": "San Jose Sharks", "place": "San Jose", "nick": "Sharks", "w": 1, "l": 3, "otl": 0, "gp": 4, "pts": 2},
}


def _g(day, team, opp, goals=0, points=0, gid=1):
    return {"gameId": gid, "gameDate": day, "teamAbbrev": team, "opponentAbbrev": opp, "goals": goals, "points": points, "type": 2}


def test_point_streak_against_opponent():
    games = [_g(f"2024-0{m}-01", "EDM", "SJS", points=1, gid=m) for m in range(1, 7)] + [_g("2024-07-01", "EDM", "CGY", gid=9)]
    notes = I.opponent_notes({"id": 1, "last": "McDavid"}, games, I._stints(games), "SJS", TEAMS, True)
    assert any("six straight" in n for n in notes)


def test_first_game_back_against_former_team():
    games = [_g("2024-01-01", "EDM", "CGY", gid=1), _g("2025-11-01", "SJS", "CGY", gid=2)]
    p = {"id": 2, "last": "Nurse", "draft_team": "EDM"}
    notes = I.opponent_notes(p, games, I._stints(games), "EDM", TEAMS, True)
    assert notes and "first time since leaving" in notes[0] and "drafted him" in notes[0]


def test_reunion_only_before_they_have_met():
    mine = [_g(f"{y}-01-01", "EDM", "CGY", gid=y) for y in range(2020, 2026)]
    friend = {"name": "Darnell Nurse", "stints": I._stints([_g(f"{y}-01-01", "EDM", "CGY", gid=y) for y in range(2020, 2026)]
                                                           + [_g("2026-10-01", "SJS", "CGY", gid=99)])}
    assert I.reunions(I._stints(mine), mine, [friend])
    met = mine + [_g("2026-10-05", "EDM", "SJS", gid=100)]
    assert not I.reunions(I._stints(met), met, [friend])


def test_team_hook_uses_record_and_history():
    fp = {"dims": {}}
    hist = [{"season": s, "rank": 30, "pts": 60, "gp": 82, "playoffs": 0} for s in range(2022, 2026)]
    out = I.team_intro("SJS", TEAMS["SJS"], fp, {"SJS": {"share": 0.45, "rank": 28, "gp": 4, "better": 1}}, hist, [])
    assert "no playoffs in four seasons" in out["text"][0]
    assert all("it " not in s.lower().split(".")[0][:3] for s in out["text"])  # teams are "they"
