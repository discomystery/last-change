import json

from pipeline.voice import compare

PACKET = {"kind": "recap", "away": {"abbr": "CAR", "name": "Carolina Hurricanes", "nickname": "Hurricanes", "place": "Carolina"},
          "home": {"abbr": "CHI", "name": "Chicago Blackhawks", "nickname": "Blackhawks", "place": "Chicago"},
          "final": {"CAR": 4, "CHI": 2}, "goals": [{"scorer": "Sebastian Aho", "time_in_period": "12:31"}],
          "goalies": [{"name": "Frederik Andersen", "saves": 31, "shots": 33}]}


def test_guard_passes_backed_facts():
    draft = "# The Hurricanes win in Chicago\n\nSebastian Aho scored at 12:31, and Frederik Andersen made 31 saves in a 4-2 win."
    assert compare.guard(draft, PACKET) == []


def test_guard_flags_invented_name_and_number():
    draft = "# Hurricanes win\n\nJordan Staal scored twice, and Andersen made 38 saves."
    flags = {(f["what"], f["value"]) for f in compare.guard(draft, PACKET)}
    assert any("Staal" in v for _, v in flags)
    assert any(v == "38" for _, v in flags)


def test_guard_allows_whitelisted_value():
    draft = "<!-- ok: 38 -->\n# Hurricanes win\n\nAndersen has 38 career shutouts."
    assert not [f for f in compare.guard(draft, PACKET) if f["value"] == "38"]


def test_parse_draft():
    d = compare.parse_draft("# Head line\n\nFirst para\ncontinues.\n\nSecond.\n\n- Milestone: Aho has 300 goals.\n- Plain item")
    assert d["head"] == "Head line"
    assert d["paras"] == ["First para continues.", "Second."]
    assert d["items"] == [{"tag": "Milestone", "text": "Aho has 300 goals."}, {"tag": "", "text": "Plain item"}]


def test_patterns_threshold(tmp_path):
    rows = [{"run": "r", "game_id": g, "page": "recap", "category": "lead", "key": "k", "note": "n"} for g in (1, 2, 3)]
    rows.append({"run": "r", "game_id": 4, "page": "recap", "category": "tone", "key": "solo", "note": "n"})
    rows += [{"run": "r", "game_id": g, "page": "recap", "category": "tone", "key": "filler", "note": "n"} for g in range(5, 21)]
    (tmp_path / "findings.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (tmp_path / "patterns.json").write_text(json.dumps({"k": {"status": "fixed"}}))
    got = {p["key"]: p for p in compare.patterns(tmp_path)}
    assert got["k"]["pattern"] and got["k"]["status"] == "fixed"
    assert not got["solo"]["pattern"] and got["solo"]["status"] == "open"
