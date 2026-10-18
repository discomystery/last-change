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


def _write(tmp_path, boxes):
    folder = tmp_path / "2026" / "box"
    folder.mkdir(parents=True, exist_ok=True)
    for b in boxes:
        (folder / f"{b['id']}.json.gz").write_bytes(gzip.compress(json.dumps(b).encode()))


def test_box_without_starter_flags_takes_the_busiest_goalie(tmp_path, monkeypatch):
    monkeypatch.setattr(gamelog, "RAW", tmp_path)
    b = _box(2026020003, "2026-10-05", 3, 2)
    b["gameState"] = "FINAL"
    relief = {"playerId": 32, "decision": None, "shotsAgainst": 5, "saves": 5, "goalsAgainst": 0, "toi": "12:00"}
    goalies = b["playerByGameStats"]["homeTeam"]["goalies"]
    for g in goalies:
        g.pop("starter")
    goalies[0]["toi"] = "48:00"
    goalies.append(relief)
    _write(tmp_path, [b])
    out = gamelog.lines([2026])
    assert dict(zip(gamelog.GOALIE, out[30][0]))["gs"] == 1
    assert dict(zip(gamelog.GOALIE, out[32][0]))["gs"] == 0
    rows = gamelog.net(2026)["CAR"]
    assert rows[0][7] == 30 and rows[0][8] == [32]


def test_net_newest_first_per_team(tmp_path, monkeypatch):
    monkeypatch.setattr(gamelog, "RAW", tmp_path)
    _write(tmp_path, [_box(2026020001, "2026-10-01", 4, 2), _box(2026020002, "2026-10-03", 2, 3, "OT")])
    net = gamelog.net(2026)
    assert [r[0] for r in net["CAR"]] == [2026020002, 2026020001]
    assert net["CAR"][0][2:7] == ["WSH", 1, 2, 3, "OT"]
    assert net["CAR"][0][7] == 30 and net["CAR"][0][8] == []
    assert "WSH" not in net  # no goalie lines on that side
