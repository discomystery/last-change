import gzip
import json

from pipeline.ingest import injuries
from pipeline.ingest.injuries import _injury, classify


def e(status, fantasy=None, kind=None, detail=None, ret=None, **kw):
    return {"status": status, "fantasy": fantasy, "type": kind, "detail": detail, "return": ret, **kw}


def test_classify():
    assert classify(e("Injured Reserve", "IR", "Hip", "Surgery", "2026-12-21"), 2026) == ("ir", None)
    assert classify(e("Injured Reserve", "IR-NR", "Knee"), 2026) == ("long", "on the injured non-roster list")
    assert classify(e("Injured Reserve", "IR-LT", "Lower Body"), 2026) == ("long", "on long-term injured reserve")
    assert classify(e("Injured Reserve", "IR-LT", "Knee", "Surgery", "2027-09-15"), 2026) == ("season", None)
    assert classify(e("Day-To-Day", "Day-To-Day", "Undisclosed"), 2026) == ("day", None)
    assert classify(e("Out", "OUT", "Ankle"), 2026) == ("out", None)
    assert classify(e("Suspension", "OUT", "Suspension"), 2026) == ("suspended", None)
    assert classify(e("Injured Reserve", "IR-NR", "Suspension"), 2026) == ("suspended", None)
    assert classify(e("Injured Reserve", "IR-NR", "Personal"), 2026) == ("personal", None)
    assert classify(e("Out", "OUT", "Contract Dispute"), 2026) is None


def test_injury_words():
    assert _injury(e("Out", kind="Upper Body")) == "upper-body"
    assert _injury(e("Out", kind="Hip", detail="Surgery")) == "hip surgery"
    assert _injury(e("Out", kind="Undisclosed")) is None
    assert _injury(e("Out", kind="Illness")) == "illness"


def test_build_dates_entries_and_clears_players_dropped_from_the_list(tmp_path, monkeypatch):
    monkeypatch.setattr(injuries, "raw_dir", lambda s: tmp_path / "raw")
    monkeypatch.setattr(injuries, "path", lambda s: tmp_path / "injuries.parquet")
    monkeypatch.setattr(injuries, "_matcher", lambda s: lambda team, name: {"Frederik Andersen": 1, "Zach Hyman": 2}.get(name))
    filler = [e("Out", "OUT", "Knee", team="XXX", name=f"Prospect {i}", date="2026-10-01") for i in range(injuries.MIN_ENTRIES)]
    days = {"2026-10-12": [e("Injured Reserve", "IR-NR", "Knee", team="EDM", name="Frederik Andersen", date="2026-09-29"),
                           e("Day-To-Day", "Day-To-Day", "Undisclosed", team="EDM", name="Zach Hyman", date="2026-10-11")],
            "2026-10-13": [e("Injured Reserve", "IR-NR", "Knee", team="EDM", name="Frederik Andersen", date="2026-09-29")]}
    (tmp_path / "raw").mkdir()
    for d, rows in days.items():
        with gzip.open(tmp_path / "raw" / f"{d}.json.gz", "wt") as f:
            json.dump(rows + filler, f)
    out = injuries.build(2026)
    t = injuries.load(2026)
    assert sorted(t.select("date", "player_id", "status").rows()) == [
        ("2026-09-29", 1, "long"), ("2026-10-11", 2, "day"), ("2026-10-13", 2, "cleared")]
    assert len(out["unmatched"]) == injuries.MIN_ENTRIES
    assert t.filter(t["player_id"] == 1)["url"][0] == injuries.PAGE
