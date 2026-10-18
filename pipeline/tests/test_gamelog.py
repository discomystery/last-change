import gzip
import json

from pipeline.export import gamelog


def _box(gid, date, home_score, away_score, end="REG"):
    skater = {"playerId": 1, "goals": 1, "assists": 1, "points": 2, "plusMinus": 1, "sog": 3, "hits": 2,
              "blockedShots": 0, "pim": 0, "powerPlayGoals": 1, "toi": "18:30"}
    starter = {"playerId": 30, "starter": True, "decision": "W" if home_score > away_score else "L", "shotsAgainst": 30,
               "saves": 28, "goalsAgainst": 2, "toi": "60:00"}
    backup = {"playerId": 31, "starter": False, "shotsAgainst": 0, "saves": 0, "goalsAgainst": 0, "toi": "00:00"}
    return {"id": gid, "gameDate": date, "gameType": 2, "gameState": "OFF", "gameOutcome": {"lastPeriodType": end},
            "homeTeam": {"abbrev": "CAR", "score": home_score}, "awayTeam": {"abbrev": "WSH", "score": away_score},
            "playerByGameStats": {"homeTeam": {"forwards": [skater], "defense": [], "goalies": [starter, backup]},
                                  "awayTeam": {"forwards": [], "defense": [], "goalies": []}}}


def test_lines_newest_last_and_backups_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(gamelog, "RAW", tmp_path)
    folder = tmp_path / "2026" / "box"
    folder.mkdir(parents=True)
    for b in (_box(2026020002, "2026-10-03", 2, 3, "OT"), _box(2026020001, "2026-10-01", 4, 2)):
        (folder / f"{b['id']}.json.gz").write_bytes(gzip.compress(json.dumps(b).encode()))
    out = gamelog.lines([2026])
    sk = [dict(zip(gamelog.SKATER, r)) for r in out[1]]
    assert [r["gid"] for r in sk] == [2026020001, 2026020002]
    assert sk[0]["toi"] == 1110 and sk[0]["opp"] == "WSH" and sk[0]["home"] == 1
    assert sk[1]["end"] == "OT" and sk[1]["us"] == 2 and sk[1]["them"] == 3
    g = dict(zip(gamelog.GOALIE, out[30][0]))
    assert g["dec"] == "W" and g["sv"] == 28 and g["gs"] == 1
    assert 31 not in out
