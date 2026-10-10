"""Line ice time is counted over the games a group actually played together, and a shuffled lineup is measured."""
import polars as pl

from pipeline.metrics import units


def _rows(team, games, trios):
    """trios: per game, a list of (unit, seconds)."""
    out = []
    for gid, (day, groups) in zip(games, trios):
        for unit, sec in groups:
            out.append({"game_id": gid, "team_id": team, "kind": "F", "unit": unit, "sec": float(sec), "cf": 0.0, "ca": 0.0, "xgf": sec / 3600 * 2.5,
                        "xga": sec / 3600 * 2.5, "gf": 0.0, "ga": 0.0, "oz_fo": 1.0, "dz_fo": 1.0, "date": day})
        out.append({"game_id": gid, "team_id": team, "kind": "D", "unit": f"{team}01-{team}02", "sec": 2000.0, "cf": 0.0, "ca": 0.0, "xgf": 1.0, "xga": 1.0,
                    "gf": 0.0, "ga": 0.0, "oz_fo": 1.0, "dz_fo": 1.0, "date": day})
    return out


def test_shuffled_lines(tmp_path, monkeypatch):
    days = [f"2026-10-0{i}" for i in range(1, 6)]
    # Team 1 keeps the same four lines every game.
    steady = [(d, [("1-2-3", 700), ("4-5-6", 600), ("7-8-9", 500), ("10-11-12", 400)]) for d in days]
    # Team 2 plays a line together in only two of five games, and mixes everything else.
    mixed = []
    for i, d in enumerate(days):
        groups = [("21-22-23", 600)] if i < 2 else [("21-24-25", 300), ("22-23-26", 300)]
        groups += [(f"{30 + 3 * i}-{31 + 3 * i}-{32 + 3 * i}", 100), (f"{50 + 3 * i}-{51 + 3 * i}-{52 + 3 * i}", 100), ("27-28-29", 30)]
        mixed.append((d, groups))
    rows = _rows(1, range(1, 6), steady) + _rows(2, range(11, 16), mixed)
    (tmp_path / "2026").mkdir()
    pl.DataFrame(rows).write_parquet(tmp_path / "2026" / "game_units.parquet")
    monkeypatch.setattr(units, "TABLES", tmp_path)
    teams = units.usual(2026)

    one = {u["label"]: u for u in teams[1]["units"]}
    assert one["L1"]["gp"] == 5 and one["L1"]["sec_pg"] == 700
    assert teams[1]["settled"]["F"] == 1.0

    two = {u["label"]: u for u in teams[2]["units"]}
    assert two["L1"]["unit"] == "21-22-23" and two["L1"]["gp"] == 2
    assert two["L1"]["sec_pg"] == 600  # not 1,200 / 5 = 240
    # A group that never reached two minutes in a game falls back to its average over the window.
    tiny = next(u for u in teams[2]["units"] if u["unit"] == "27-28-29")
    assert tiny["gp"] == 0 and tiny["sec_pg"] == 30
    assert teams[2]["settled"]["F"] < 0.5 and teams[2]["settled"]["F_rank"] == 2
