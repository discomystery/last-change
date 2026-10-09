import json
from datetime import datetime, timedelta, timezone

from pipeline.export import previews


def _site(tmp_path, start):
    dims = {k: {"blend": {"v": 60.0, "pct": 50, "rank": 16, "index": 100}} for k in
            ["volume", "suppression", "quality", "qualityAllowed", "turnover", "breakdowns", "pp", "pk", "pace", "point", "forecheck", "physical", "depth"]}
    strong = {**dims, "volume": {"blend": {"v": 66.0, "pct": 92, "rank": 3, "index": 116}}}
    weak = {**dims, "suppression": {"blend": {"v": 62.0, "pct": 10, "rank": 29, "index": 110}}}
    (tmp_path / "schedule.json").write_text(json.dumps({"games": [{"id": 1, "start": start, "home": "BBB", "away": "AAA", "venue": "Rink", "final": False}]}))
    (tmp_path / "teams.json").write_text(json.dumps([{"abbr": "AAA", "place": "Aville"}, {"abbr": "BBB", "place": "Btown"}]))
    (tmp_path / "fingerprints.json").write_text(json.dumps({"generated_at": "x", "teams": {"AAA": {"games": 5, "dims": strong}, "BBB": {"games": 5, "dims": weak}}}))
    (tmp_path / "lines.json").write_text(json.dumps({"teams": {}}))


def test_claims_written_then_frozen_at_puck_drop(tmp_path, monkeypatch):
    monkeypatch.setattr(previews, "SITE_DATA", tmp_path)
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    start = (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z")
    _site(tmp_path, start)
    assert previews.run(now)["written"] == 1
    snap = json.loads((tmp_path / "previews" / "1.json").read_text())
    clash = snap["claims"][0]
    assert clash["kind"] == "clash" and clash["team"] == "AAA" and clash["check"]["direction"] == "above"
    assert len(snap["claims"]) >= 2
    # After puck drop the data changes, but the snapshot must not.
    (tmp_path / "fingerprints.json").write_text((tmp_path / "fingerprints.json").read_text().replace('"pct": 92', '"pct": 40'))
    out = previews.run(now + timedelta(hours=9))
    assert out == {"written": 0, "frozen": 1, "indexed": 1}
    assert json.loads((tmp_path / "previews" / "1.json").read_text()) == snap
