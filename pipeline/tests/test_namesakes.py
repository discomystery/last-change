"""Two players with one name on a club (Vancouver's Elias Pettersson, forward #40 and defenseman #25)."""
from pipeline.ingest import nhl_previews

FWD, DMAN = 40, 25
BODY = """**Canucks projected lineup**

Jake DeBrusk -- Elias Pettersson -- Linus Karlsson

Pius Suter -- Filip Chytil -- Brock Boeser

Elias Pettersson -- Filip Hronek

Marcus Pettersson -- Tyler Myers

Thatcher Demko

**Lightning projected lineup**

Brandon Hagel -- Anthony Cirelli -- Nikita Kucherov

Thatcher Demko
"""
PEOPLE = {"Jake DeBrusk": (1, "L"), "Linus Karlsson": (2, "R"), "Pius Suter": (3, "C"), "Filip Chytil": (4, "C"), "Brock Boeser": (5, "R"),
          "Filip Hronek": (6, "D"), "Marcus Pettersson": (7, "D"), "Tyler Myers": (8, "D"), "Thatcher Demko": (9, "G")}


def _known():
    team = {nhl_previews.fold(n): v for n, v in PEOPLE.items()}
    team[nhl_previews.fold("Elias Pettersson")] = (DMAN, "D")  # whoever was read last used to win
    return {"VAN": team, "*": dict(team), "=VAN": {nhl_previews.fold("Elias Pettersson"): [(DMAN, "D"), (FWD, "C")]}}


def test_a_shared_name_goes_to_the_player_whose_position_fits_the_row():
    got = nhl_previews.lineups_from(BODY, "VAN", "TBL", _known())["VAN"]
    assert got["lines"] == [[1, FWD, 2], [3, 4, 5]]
    assert got["pairs"] == [[DMAN, 6], [7, 8]]
    assert got["goalies"] == [9] and got["missed"] == 0


def test_a_scratched_namesake_is_the_one_not_in_the_lineup():
    body = BODY.replace("Elias Pettersson -- Filip Hronek", "Tyler Myers -- Filip Hronek").replace(
        "Thatcher Demko\n", "Thatcher Demko\n\n***Scratched:** Elias Pettersson*\n", 1)
    got = nhl_previews.lineups_from(body, "VAN", "TBL", _known())["VAN"]
    assert got["scratched"] == [DMAN] and got["lines"][0] == [1, FWD, 2]


def test_saved_lineups_are_read_again():
    bad = {"VAN": {"lines": [[1, FWD, 2], [3, 4, 5], [FWD, 6]], "pairs": [[7, 8]], "extra_d": [], "goalies": [9],
                   "scratched": [], "injured": [], "missed": 0},
           "TBL": {"lines": [[11, 12, 13]], "pairs": [], "extra_d": [], "goalies": [], "scratched": [], "injured": [], "missed": 0}}
    fixed = nhl_previews.repair(bad, _known())
    assert fixed["VAN"]["lines"] == [[1, FWD, 2], [3, 4, 5]] and fixed["VAN"]["pairs"] == [[DMAN, 6], [7, 8]]
    assert fixed["TBL"] == bad["TBL"]
    flipped = {"VAN": {**bad["VAN"], "lines": [[1, DMAN, 2], [3, 4, 5]], "pairs": [[DMAN, 6], [7, 8]]}}
    assert nhl_previews.repair(flipped, _known())["VAN"]["lines"][0] == [1, FWD, 2]
    assert nhl_previews.repair(fixed, _known()) == fixed


def test_status_facts_skip_a_name_two_players_share():
    people = {FWD: ("VAN", "Elias", "Pettersson"), DMAN: ("VAN", "Elias", "Pettersson"), 7: ("VAN", "Marcus", "Pettersson"), 3: ("VAN", "Pius", "Suter")}
    assert nhl_previews._who("Elias Pettersson and Pius Suter are game-time decisions", people) == [3]
    assert nhl_previews._who("Marcus Pettersson is a game-time decision", people) == [7]
    assert nhl_previews._who("Pettersson is a game-time decision", people) == []
