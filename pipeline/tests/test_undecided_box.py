"""A boxscore saved at the horn (FINAL, no goalie decisions yet) is fetched again until the NHL fills them in."""
import gzip
import json
from datetime import datetime, timezone

from pipeline.ingest import nhl

NOW = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)


def _save(tmp_path, state, decision):
    box = {"gameState": state, "playerByGameStats": {"homeTeam": {"goalies": [{"playerId": 1, "decision": decision}]},
                                                      "awayTeam": {"goalies": [{"playerId": 2}]}}}
    folder = tmp_path / "2026" / "box"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026020227.json.gz").write_bytes(gzip.compress(json.dumps(box).encode()))


def test_undecided(tmp_path, monkeypatch):
    monkeypatch.setattr(nhl, "RAW", tmp_path)
    start = "2026-11-01T18:00:00Z"
    assert not nhl.undecided(2026, 2026020227, start, NOW)  # nothing saved: the normal fetch gets it
    _save(tmp_path, "FINAL", None)
    assert nhl.undecided(2026, 2026020227, start, NOW)
    assert not nhl.undecided(2026, 2026020227, "2026-10-01T18:00:00Z", NOW)  # too old to keep asking
    _save(tmp_path, "FINAL", "W")
    assert not nhl.undecided(2026, 2026020227, start, NOW)
    _save(tmp_path, "OFF", None)
    assert not nhl.undecided(2026, 2026020227, start, NOW)
