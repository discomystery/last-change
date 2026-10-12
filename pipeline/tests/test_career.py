from pipeline.export import career


def test_split_season_totals_and_rates():
    parts = [{"team": "AAA", "gp": 20, "w": 10, "l": 8, "ot": 2, "sa": 500, "ga": 50, "sec": 20 * 3600},
             {"team": "BBB", "gp": 10, "w": 5, "l": 5, "ot": 0, "sa": 300, "ga": 20, "sec": 10 * 3600}]
    tot = career._rates(career._total(parts, True))
    assert tot == {"gp": 30, "w": 15, "l": 13, "ot": 2, "sv": round(1 - 70 / 800, 3), "gaa": round(70 / 30, 2)}


def test_old_goalie_rows_without_records_stay_blank():
    row = career._from_landing({"teamName": {"default": "Nowhere Club"}, "gamesPlayed": 40, "goals": 0, "assists": 1, "points": 1}, True)
    assert row["w"] is None and career._total([row], True)["w"] is None


def test_highs_need_two_seasons_and_count_ties():
    rows = [{"s": 2021, "g": 20, "p": 50}, {"s": 2022, "g": 30, "p": 50}, {"s": 2023, "g": 10, "p": 40}]
    assert career._highs(rows, False) == {"g": [2022], "p": [2021, 2022]}
    assert career._highs(rows[:1], False) == {}


def test_goalie_save_pct_high_needs_a_real_season():
    rows = [{"s": 2021, "gp": 5, "w": 4, "sv": 0.950}, {"s": 2022, "gp": 50, "w": 30, "sv": 0.915}, {"s": 2023, "gp": 45, "w": 25, "sv": 0.905}]
    assert career._highs(rows, True) == {"w": [2022], "sv": [2022]}


def test_before_the_nhl_follows_his_last_club_back():
    landing = {"otherTotals": [
        {"season": 20182019, "leagueAbbrev": "CSSHL", "teamName": "Prep", "gamesPlayed": 30},
        {"season": 20202021, "leagueAbbrev": "WHL", "teamName": "Regina Pats", "gamesPlayed": 15},
        {"season": 20212022, "leagueAbbrev": "WHL", "teamName": "Regina Pats", "gamesPlayed": 62},
        {"season": 20222023, "leagueAbbrev": "WHL", "teamName": "Regina Pats", "gamesPlayed": 57},
        {"season": 20232024, "leagueAbbrev": "AHL", "teamName": "Farm", "gamesPlayed": 20}]}
    assert career._before_nhl(landing, 20232024) == {"team": "Regina Pats", "league": "WHL", "from": 2020, "to": 2022}


def test_draft_details_and_undrafted():
    land = {"draftDetails": {"year": 2003, "teamAbbrev": "ANA", "round": 1, "pickInRound": 28, "overallPick": 28}}
    d = career._draft(land)
    assert (d["year"], d["team"], d["round"], d["pick"]) == (2003, "ANA", 1, 28)
    assert career._draft({}) == {"undrafted": True}
