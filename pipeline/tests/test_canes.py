import json

import pipeline.export.canes as canes


def _player(pid, team, pct, group="F", games=60, **over):
    traits = {k: {"blend": {"ok": True, "pct": over.get(k, pct)}} for k in canes.TRAITS}
    edge = {"blend": {k: {"ok": True, "pct": over.get("e_" + k, pct)} for k in canes.EDGE + canes.SYSTEM}}
    return {"id": pid, "name": f"P{pid}", "team": team, "group": group, "pos": "C", "age": 25, "traits": traits, "edge": edge,
            "stats": {"season": {"gp": games // 2}, "last": {"gp": games - games // 2}}}


def test_fit_and_type(tmp_path, monkeypatch):
    out = tmp_path / "players"
    out.mkdir()
    # Carolina: big shooters who never block; others are average, except one who plays exactly like them.
    rows = [_player(i, "CAR", 50, shooting=90, blocks=10) for i in range(1, 6)]
    rows += [_player(100 + i, t, 50) for i, t in enumerate(["BOS", "BOS", "BOS", "NYR", "NYR", "NYR", "TOR", "TOR"])]
    rows += [_player(200, "TOR", 50, shooting=95, blocks=5)]
    for r in rows:
        (out / f"{r['id']}.json").write_text(json.dumps(r))
    monkeypatch.setattr(canes, "OUT", out)
    monkeypatch.setattr(canes, "SITE_DATA", tmp_path)
    canes.run()
    d = json.loads((tmp_path / "canes.json").read_text())
    prof = d["profiles"]["F"]
    assert prof["shooting"]["w"] > 0 and prof["blocks"]["dir"] == -1 and prof["hits"]["w"] == 0
    score = {p["id"]: p["score"] for p in d["players"]}
    assert score[200] > max(score[100 + i] for i in range(8))  # shoots more than the Canes: still a fit
    car = next(c for c in d["typey"] if c["team"] == "CAR")
    assert car["F"]["tight"] > 0.5  # five identical players are far more alike than chance
