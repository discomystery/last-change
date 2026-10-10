import json
from datetime import datetime, timedelta, timezone

from pipeline.export import previews


def _site(tmp_path, start):
    dims = {k: {"blend": {"v": 60.0, "pct": 50, "rank": 16, "index": 100}} for k in
            ["volume", "suppression", "quality", "qualityAllowed", "turnover", "breakdowns", "pp", "pk", "pace", "dShots", "inClose", "forecheck", "physical", "depth"]}
    strong = {**dims, "volume": {"blend": {"v": 66.0, "pct": 92, "rank": 3, "index": 116}}}
    weak = {**dims, "suppression": {"blend": {"v": 62.0, "pct": 10, "rank": 29, "index": 110}}}
    (tmp_path / "schedule.json").write_text(json.dumps({"games": [{"id": 1, "start": start, "home": "BBB", "away": "AAA", "venue": "Rink", "final": False, "hs": 0, "as": 0, "end": None}]}))
    (tmp_path / "teams.json").write_text(json.dumps([{"abbr": "AAA", "place": "Aville"}, {"abbr": "BBB", "place": "Btown"}]))
    (tmp_path / "fingerprints.json").write_text(json.dumps({"generated_at": "x", "teams": {"AAA": {"games": 5, "dims": strong}, "BBB": {"games": 5, "dims": weak}}}))
    (tmp_path / "lines.json").write_text(json.dumps({"teams": {}}))
    (tmp_path / "goalies.json").write_text(json.dumps({"league": [], "teams": {}}))
    (tmp_path / "goal_sources.json").write_text(json.dumps({"league_sources": {}, "teams": {}}))


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


def test_snapshot_carries_the_page_data_it_was_made_from(tmp_path, monkeypatch):
    monkeypatch.setattr(previews, "SITE_DATA", tmp_path)
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    _site(tmp_path, (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z"))
    previews.run(now)
    pre = json.loads((tmp_path / "previews" / "1.json").read_text())["pregame"]
    assert set(pre["fingerprints"]["teams"]) == {"AAA", "BBB"} and pre["records"] == {"AAA": "0-0-0", "BBB": "0-0-0"}


def test_records_count_only_games_finished_before_the_start():
    sched = [{"start": "2026-10-01T23:00:00Z", "final": True, "home": "A", "away": "B", "hs": 3, "as": 2, "end": "OT"},
             {"start": "2026-10-03T23:00:00Z", "final": True, "home": "B", "away": "A", "hs": 4, "as": 1, "end": "REG"},
             {"start": "2026-10-05T23:00:00Z", "final": True, "home": "A", "away": "B", "hs": 5, "as": 0, "end": "REG"}]
    assert previews.records(sched, "2026-10-05T23:00:00Z") == {"A": "1-1-0", "B": "1-0-1"}


def test_every_call_is_a_graded_prediction(tmp_path, monkeypatch):
    monkeypatch.setattr(previews, "SITE_DATA", tmp_path)
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    _site(tmp_path, (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z"))
    previews.run(now)
    snap = json.loads((tmp_path / "previews" / "1.json").read_text())
    assert snap["rules"] == previews.RULES_VERSION
    for c in snap["claims"]:
        assert c["check"] and c["call"]
    edge = next(c for c in snap["claims"] if c["kind"] == "edge")
    # The clash already covers shot volume against suppression, so the edge call picks another trait.
    assert edge["metric"] not in ("volume", "suppression")
    assert edge["check"]["gap"] >= 0


def test_edge_call_graded_on_the_usual_margin():
    from types import SimpleNamespace

    from pipeline.export import recaps
    fp = {t: {"dims": {"depth": {"blend": {"v": v, "pct": p, "rank": 1, "index": 100}}}} for t, v, p in (("AAA", 45.0, 90), ("BBB", 39.0, 10))}
    claim = {"kind": "edge", "metric": "depth", "team": "AAA", "opp": "BBB", "check": previews.edge_check("depth", "AAA", "BBB", fp)}
    assert claim["check"]["gap"] == 6.0
    row = lambda b6: {"bottom6_sec": b6, "fwd_sec": 100.0, "sec5": 2400.0}
    places = {"AAA": "Aville", "BBB": "Btown"}
    for a, b, want in ((44, 40, "held"), (42, 40, "partly"), (40, 41, "missed")):
        S = SimpleNamespace(team_id={(1, "AAA"): 10, (1, "BBB"): 20}, rows={(1, 10): row(a), (1, 20): row(b)})
        assert recaps.measure_edge(S, 1, claim, places, fp)["verdict"] == want
    # Notes saved under the first rulebook carry no check; they are graded by the same rule.
    old = {**claim, "kind": "contrast", "check": None}
    assert recaps.measure_edge(S, 1, old, places, fp)["verdict"] == "missed"
    assert recaps.measure_edge(S, 1, {**old, "metric": "pace"}, places, fp)["verdict"] == "na"


def _dims(**over):
    d = {k: {"blend": {"v": 60.0, "pct": 50, "rank": 16, "index": 100}} for k in
         ["volume", "suppression", "quality", "qualityAllowed", "turnover", "breakdowns", "pp", "pk", "pace", "dShots", "inClose", "forecheck", "physical", "depth"]}
    for k, b in over.items():
        d[k] = {"blend": {"rank": 3, **b}}
    return d


def test_strength_against_strength_picks_a_side_from_the_regression():
    places = {"AAA": "Aville", "BBB": "Btown"}
    # A power play at 9.0 (league 7.5) against a kill allowing 6.0: 0.897 * 1.5 - 0.589 * 1.5 > 0, so the power play.
    fp = {"AAA": {"games": 5, "dims": _dims(pp={"v": 9.0, "pct": 90, "index": 120})},
          "BBB": {"games": 5, "dims": _dims(pk={"v": 6.0, "pct": 90, "index": 80})}}
    duel = previews.duel_claims(1, "AAA", "BBB", fp, places)[0]
    assert duel["kind"] == "duel" and duel["lean"] == "offense" and duel["check"]["direction"] == "above"
    assert duel["check"]["baseline"] == 7.5
    # Shot volume 62 (league 60) against a defense allowing 54: the defense wins the lean.
    fp = {"AAA": {"games": 5, "dims": _dims(volume={"v": 62.0, "pct": 80, "index": 103.33})},
          "BBB": {"games": 5, "dims": _dims(suppression={"v": 54.0, "pct": 90, "index": 90})}}
    duel = previews.duel_claims(1, "AAA", "BBB", fp, places)[0]
    assert duel["lean"] == "defense" and duel["check"]["direction"] == "below"


def test_team_words_use_the_nickname_and_they():
    previews.NICKS.clear()
    previews.NICKS.update({"STL": "Blues", "NYR": "Rangers", "MIN": "Wild"})
    w = previews.team_words("STL", {"STL": "St. Louis"}, "X")
    assert w == {"X": "the Blues", "Xp": "the Blues’", "Xc": "St. Louis", "Xn": "Blues"}
    assert previews.team_words("NYR", {"NYR": "NY Rangers"}, "X")["Xc"] == "the Rangers"
    assert previews.team_words("MIN", {"MIN": "Minnesota"}, "X")["Xp"] == "the Wild’s"
    assert previews._fmt("{X} shoot. {Xp} kill holds.", **w) == "The Blues shoot. The Blues’ kill holds."
    previews.NICKS.clear()


def test_goalie_record_call_graded_on_his_usual():
    from pipeline.export import recaps
    claim = {"kind": "history", "check": {"goalie_id": 7, "baseline": 0.905, "record": 0.945}}
    line = lambda saves, shots, started=True: [{"id": 7, "name": "A. Goalie", "started": started, "shots": shots, "saves": saves}]
    assert recaps.measure_history(claim, line(27, 30))["verdict"] == "held"  # .900
    assert recaps.measure_history(claim, line(29, 30))["verdict"] == "missed"  # .967
    assert recaps.measure_history(claim, line(29, 30, started=False))["verdict"] == "na"
