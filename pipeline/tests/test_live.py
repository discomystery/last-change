"""The live feed builds a game from the play-by-play alone: skater counts and empty nets from the situation code."""
import json

import numpy as np

from pipeline.live import build, loop


class Flat:
    def predict_proba(self, x):
        return np.column_stack([np.full(len(x), 0.9), np.full(len(x), 0.1)])


def play(n, period, t, typ, code, details=None):
    return {"eventId": n, "sortOrder": n, "periodDescriptor": {"number": period, "periodType": "REG"}, "timeInPeriod": t,
            "typeDescKey": typ, "situationCode": code, "homeTeamDefendingSide": "left", "details": details or {}}


def pbp(state="LIVE"):
    out = _pbp(state)
    for i, p in enumerate(out["plays"]):
        p["sortOrder"] = i
    return out


def _pbp(state):
    shot = lambda team, who, x, y, **k: {"xCoord": x, "yCoord": y, "eventOwnerTeamId": team, "shootingPlayerId": who, "shotType": "wrist", **k}
    return {
        "id": 2026029999, "gameType": 2, "gameState": state, "awayTeam": {"id": 1, "abbrev": "AAA", "score": 1, "sog": 2},
        "homeTeam": {"id": 2, "abbrev": "BBB", "score": 0, "sog": 1},
        "periodDescriptor": {"number": 2, "periodType": "REG"}, "clock": {"timeRemaining": "15:00", "running": True, "inIntermission": False},
        "rosterSpots": [{"playerId": 11, "teamId": 1, "firstName": {"default": "Al"}, "lastName": {"default": "Away"}, "sweaterNumber": 9, "positionCode": "C"},
                        {"playerId": 22, "teamId": 2, "firstName": {"default": "Bo"}, "lastName": {"default": "Home"}, "sweaterNumber": 4, "positionCode": "D"}],
        "plays": [
            play(1, 1, "00:00", "faceoff", "1551", {"xCoord": 0, "yCoord": 0, "eventOwnerTeamId": 2}),
            # home defends the left end in period 1, so the visitors shoot at +x
            play(2, 1, "05:00", "shot-on-goal", "1551", shot(1, 11, 80, 5)),
            # a delayed penalty: the visitors' goalie comes off until the home side touches the puck (not a pulled goalie)
            play(30, 1, "07:40", "delayed-penalty", "0651", {"eventOwnerTeamId": 2}),
            play(31, 1, "07:55", "hit", "0651", {"eventOwnerTeamId": 1, "hittingPlayerId": 11, "xCoord": 50, "yCoord": 0}),
            play(3, 1, "08:00", "penalty", "1551", {"eventOwnerTeamId": 2, "committedByPlayerId": 22, "duration": 2, "typeCode": "MIN", "descKey": "tripping"}),
            play(4, 1, "08:30", "goal", "1541", shot(1, 11, 82, -3, scoringPlayerId=11, awayScore=1, homeScore=0)),
            play(5, 1, "15:00", "missed-shot", "1551", shot(2, 22, -60, 10)),
            # the visitors pull their goalie: a home shot at the empty net
            play(6, 2, "04:00", "shot-on-goal", "0651", shot(2, 22, -70, 0)),
        ],
    }


def test_live_game_from_play_by_play_alone():
    d = build.game(pbp(), {"model": Flat(), "levels": {"shot_type": ["wrist"], "prev_type": ["none"]}, "numeric": []})
    assert [s[1] for s in d["shots"]] == [0, 0, 1, 1] and all(s[2] == 0.1 for s in d["shots"])
    pp_goal, home_empty = d["shots"][1], d["shots"][3]
    assert pp_goal[3] == 1 and pp_goal[8] == 1  # a power-play goal: 5 skaters against 4
    assert home_empty[7] == 1  # the visitors' net was empty
    assert d["pp"] == [[0, 510, 900]]  # from the situation codes the plays carry
    assert d["str"][0] == [0, 5, 5, 1, 1] and [1200 + 240, 6, 5, 0, 1] in d["str"]
    assert d["live"]["score"] == [1, 0] and d["live"]["sec"] == 1500 and d["players"] == []
    assert d["names"] == {"11": "Away", "22": "Home"}
    assert d["pens"][0][:3] == [480, 1, 22]
    assert d["pulled"] == [[0, 1440, 1500]]


def test_files_only_change_when_their_content_does(tmp_path):
    path = tmp_path / "1.json"
    assert loop.write(path, {"a": 1, "live": {"made": "x"}, "made": "1"})
    assert not loop.write(path, {"a": 1, "live": {"made": "x"}, "made": "2"})
    assert json.loads(path.read_text())["made"] == "1"
    assert loop.write(path, {"a": 2, "live": {"made": "x"}, "made": "3"})
