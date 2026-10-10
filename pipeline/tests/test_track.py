import numpy as np

from pipeline.export import track


def _recap(gid, day, rebuilt, verdicts):
    calls = [{"kind": "clash", "head": "h", "verdict": v} for v in verdicts]
    return {"game_id": gid, "date": day, "start": day + "T23:00:00Z", "away": "AAA", "home": "BBB", "rebuilt": rebuilt, "calls": calls}


def test_every_graded_call_counts_rebuilt_or_not():
    rec = track.call_record([_recap(1, "2026-10-06", True, ["held", "missed", "na"]), _recap(2, "2026-10-12", False, ["held", "partly", None])])
    assert rec["tally"] == {"held": 2, "partly": 1, "missed": 1}
    assert rec["weeks"] == [{"week": "2026-10-05", "held": 1, "partly": 0, "missed": 1}, {"week": "2026-10-12", "held": 1, "partly": 1, "missed": 0}]
    assert rec["games"] == 2 and len(rec["latest"]) == 4 and rec["latest"][0]["game_id"] == 2


def test_calibration_bands_and_ranges():
    p = np.array([0.52, 0.48, 0.72, 0.30])
    y = np.array([1, 1, 1, 0])
    s = track.scored(p, y)
    assert s["games"] == 4 and s["picked"] == 3 and s["home_rate"] == 0.75
    top = next(b for b in s["bands"] if b["band"] == "70%+")
    assert top["games"] == 2 and top["won"] == 1.0 and top["lo"] < 1.0
