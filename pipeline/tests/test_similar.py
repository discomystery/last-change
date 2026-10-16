import json

import pipeline.export.similar as similar


def _player(pid, pos, pct, games=60, toi=(800, 120, 60)):
    traits = {k: {"blend": {"ok": True, "pct": pct}} for k in similar.TRAITS}
    edge = {"blend": {k: {"ok": True, "pct": pct} for k in similar.EDGE}}
    return {"id": pid, "group": "D" if pos == "D" else "F", "pos": pos, "traits": traits, "edge": edge,
            "stats": {"season": {"gp": 10}, "last": {"gp": games - 10}},
            "role": {"toi": {"toi5": toi[0], "toi_pp": toi[1], "toi_pk": toi[2]}}}


def test_suggests_the_closest_style_and_keeps_positions_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(similar, "OUT", tmp_path)
    players = [
        _player(1, "C", 90), _player(2, "C", 88), _player(3, "C", 40), _player(4, "L", 89), _player(5, "C", 10),
        _player(6, "D", 90), _player(7, "C", 89, games=12),  # too few games to be suggested
    ]
    for p in players:
        (tmp_path / f"{p['id']}.json").write_text(json.dumps(p))
    (tmp_path / "index.json").write_text("[]")  # not a player file
    similar.run()
    out = json.loads((tmp_path / "similar.json").read_text())
    assert out["1"][0] == 2  # nearest center first
    assert out["1"].index(4) > out["1"].index(2)  # the winger is pushed back
    assert 6 not in out["1"] and 7 not in out["1"]  # no defenseman, no 12-game player
    assert "6" not in out or out["6"] == []  # a defenseman has no other defenseman to match
    assert out["7"][0] in (1, 2)  # a short-sample player still gets suggestions


def test_unusual_traits_weigh_more():
    a = {"group": "F", "pos": "C", "f": {f"t{i}": 50 for i in range(9)} | {"x": 99}}
    near_on_x = {"group": "F", "pos": "C", "f": {f"t{i}": 60 for i in range(9)} | {"x": 99}}
    near_on_rest = {"group": "F", "pos": "C", "f": {f"t{i}": 50 for i in range(9)} | {"x": 70}}
    assert similar.distance(a, near_on_x) is not None
    assert similar.distance(a, near_on_rest) > similar.distance(a, {"group": "F", "pos": "C", "f": {f"t{i}": 55 for i in range(9)} | {"x": 99}})
