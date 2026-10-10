"""Win probability: ratings only see earlier days, back-to-backs are caught, and the breakdown adds up."""
import numpy as np
import polars as pl

from pipeline.metrics import win_model


def _games(rows):
    return pl.DataFrame(rows, schema=["game_id", "season", "date", "home_id", "away_id", "home_score", "away_score", "h_xgd", "a_xgd"], orient="row")


def test_ratings_use_only_earlier_days():
    g = _games([
        (1, 2025, "2025-10-01", 1, 2, 5, 0, 3.0, -3.0),
        (2, 2025, "2025-10-01", 3, 4, 0, 1, 0.0, 0.0),
        (3, 2025, "2025-10-02", 1, 3, 2, 1, 9.0, -9.0),  # its own result must not feed its own rating
    ])
    df = win_model.pregame(g, win_model.Params(decay=1.0, carry=1.0, prior_games=1.0))
    first, third = df.row(0, named=True), df.row(2, named=True)
    assert first["h_xgd"] == 0 and first["a_xgd"] == 0
    assert third["h_xgd"] == 3.0 / 2  # one game of +3 plus one phantom average game
    assert third["h_b2b"] == 1 and third["a_b2b"] == 1 and first["h_b2b"] == 0


def test_explanation_adds_up_to_the_probability():
    rng = np.random.default_rng(0)
    df = pl.DataFrame({"xgd": rng.normal(0, 0.5, 2000), "b2b": rng.integers(-1, 2, 2000)})
    y = (rng.random(2000) < 1 / (1 + np.exp(-(0.15 + 0.8 * df["xgd"].to_numpy())))).astype(int)
    m = win_model.fit(df.with_columns(home_win=pl.Series(y)))
    row = {"xgd": 0.4, "b2b": -1, "lineup": -0.3}
    steps = win_model.explain(m, row)
    morning = win_model.predict(m, pl.DataFrame([row]))[0]
    at_drop = win_model.predict(m, pl.DataFrame([row]), puck_drop=True)[0]
    assert abs(0.5 + sum(s["shift"] for s in steps[:-1]) - morning) < 1e-9
    assert abs(0.5 + sum(s["shift"] for s in steps) - at_drop) < 1e-9
    assert steps[0]["input"] == "home" and steps[0]["shift"] > 0
    assert steps[-1]["input"] == "lineup" and at_drop < morning  # a weaker lineup than usual lowers the chance


def test_score_on_a_coin_flip():
    s = win_model.score(np.full(4, 0.5), np.array([1, 0, 1, 0]))
    assert s["brier"] == 0.25 and abs(s["log_loss"] - np.log(2)) < 1e-9


def _lineup_tables(tmp_path, monkeypatch):
    """One season, team 1 dresses a star (worth +1 expected goal per 60) in its first three games, then plays without him."""
    d = tmp_path / "2025"
    d.mkdir()
    games, pg = [], []
    for n in range(1, 6):
        gid = 2025020000 + n
        games.append({"game_id": gid, "date": f"2025-10-0{n}", "game_type": 2})
        roster = [100 + i for i in range(18)] + ([999] if n <= 3 else [118])
        for p in roster:
            pg.append({"game_id": gid, "player_id": p, "team_id": 1, "pos": "C", "sec5": 900})
        for p in range(200, 219):
            pg.append({"game_id": gid, "player_id": p, "team_id": 2, "pos": "C", "sec5": 900})
    pl.DataFrame(games).write_parquet(d / "games.parquet")
    pl.DataFrame(pg).write_parquet(d / "player_game.parquet")
    monkeypatch.setattr(win_model, "TABLES", tmp_path)
    monkeypatch.setattr(win_model, "_rates", lambda season, have: {999: 1.0})
    win_model._lineup_pass.cache_clear()


def test_a_missing_star_counts_at_once_and_fades_from_the_rating(tmp_path, monkeypatch):
    _lineup_tables(tmp_path, monkeypatch)
    lu = win_model.lineups([2025]).filter(pl.col("team_id") == 1).sort("game_id")
    lineup, roster = lu["lineup"].to_list(), lu["roster"].to_list()
    star = win_model.SEC5_PER_GAME / 19 / 3600 * 1.0  # his share of a night's 5v5 time (19 equal skaters), times his rating
    assert abs(lineup[0] - star) < 1e-9  # first game: the rating has seen no lineups yet (only average ones)
    assert lineup[3] < 0 < roster[3]  # the night he is first out: puck drop knows, the morning (last lineup) does not
    assert roster[4] == lineup[4] < 0  # next game the morning figure knows too
    assert lineup[4] > lineup[3]  # and the gap shrinks as games without him enter the rating
    nxt = win_model.next_rosters([2025])
    assert lineup[4] < nxt[1] < 0 and nxt[2] == 0
    win_model._lineup_pass.cache_clear()


def test_explanation_with_last_lineup_adds_up():
    rng = np.random.default_rng(1)
    df = pl.DataFrame({"xgd": rng.normal(0, 0.5, 2000), "b2b": rng.integers(-1, 2, 2000)})
    y = (rng.random(2000) < 1 / (1 + np.exp(-(0.15 + 0.8 * df["xgd"].to_numpy())))).astype(int)
    m = win_model.fit(df.with_columns(home_win=pl.Series(y)))
    row = {"xgd": 0.4, "b2b": 0, "roster": 0.2, "lineup": -0.1}
    steps = win_model.explain(m, row)
    assert [s["input"] for s in steps] == ["home", "xgd", "roster", "b2b", "lineup"]
    morning = win_model.predict(m, pl.DataFrame([row]))[0]
    at_drop = win_model.predict(m, pl.DataFrame([row]), puck_drop=True)[0]
    assert abs(0.5 + sum(s["shift"] for s in steps[:-1]) - morning) < 1e-9
    assert abs(0.5 + sum(s["shift"] for s in steps) - at_drop) < 1e-9
    assert steps[2]["shift"] > 0 > steps[-1]["shift"]
