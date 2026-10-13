from pipeline.ingest import game_stats

GAME = {"game_id": 2026020035, "away": "WPG", "home": "DET"}


def test_reads_the_power_play_line():
    rail = {"teamGameStats": [{"category": "sog", "awayValue": 27, "homeValue": 36},
                              {"category": "powerPlay", "awayValue": "3/4", "homeValue": "1/5"}]}
    assert game_stats.parse(GAME, rail) == [
        {"game_id": 2026020035, "team": "WPG", "pp_goals": 3, "pp_opps": 4},
        {"game_id": 2026020035, "team": "DET", "pp_goals": 1, "pp_opps": 5}]


def test_nothing_until_the_line_is_posted():
    assert game_stats.parse(GAME, {}) == []
    assert game_stats.parse(GAME, {"teamGameStats": [{"category": "powerPlay", "awayValue": "", "homeValue": "1/5"}]}) == []
