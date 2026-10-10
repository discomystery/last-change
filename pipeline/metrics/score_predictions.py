"""Score win probabilities against results: hit rate, Brier score, log loss and calibration.

    uv run --project pipeline python -m pipeline.metrics.score_predictions            # backtest the model
    uv run --project pipeline python -m pipeline.metrics.score_predictions --file preds.csv  # score saved predictions

Backtest: each full season is predicted by a model fitted on the other full seasons, and the current season by a
model fitted on all of them, so no game is ever scored by a model that saw it. The figures with lineups (the morning
one, with each team's previous lineup, and the puck-drop one, with tonight's) start in 2024-25, the first season with an earlier season of player impact ratings behind it. A saved-predictions file needs the
columns game_id and p_home (the home team's chance); results come from the games tables.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FINAL_STATES, FULL_SEASONS, REGULAR, TABLES
from pipeline.metrics import win_model


def backtest(params: win_model.Params = win_model.Params()) -> dict:
    seasons = FULL_SEASONS + [CURRENT_SEASON]
    lineup_seasons = [s for s in seasons if s > min(FULL_SEASONS)]
    df = win_model.with_lineups(win_model.pregame(win_model.team_games(seasons), params), win_model.lineups(lineup_seasons, params))
    out = {}
    for test in seasons:
        te = df.filter(pl.col("season") == test)
        if te.is_empty():
            continue
        y = te["home_win"].to_numpy()
        train = df.filter(pl.col("season").is_in([s for s in FULL_SEASONS if s != test]))
        m = win_model.fit(train)
        out[test] = {"model": win_model.score(win_model.predict(m, te.drop("roster", "lineup")), y),
                     "home_rate_only": win_model.score(np.full(len(y), train["home_win"].mean()), y),
                     "home_won": float(y.mean()),
                     "weights": {"home": float(m.intercept_[0]), **{f: float(c) for f, c in zip(win_model.FEATURES, m.coef_[0])}}}
        if test in lineup_seasons:
            out[test]["morning_roster"] = win_model.score(win_model.predict(m, te), y)
            out[test]["puck_drop"] = win_model.score(win_model.predict(m, te, puck_drop=True), y)
    return out


def score_file(path: Path) -> dict:
    preds = pl.read_csv(path).select("game_id", "p_home")
    seasons = sorted({int(str(g)[:4]) for g in preds["game_id"]})
    games = pl.concat([pl.read_parquet(TABLES / str(s) / "games.parquet") for s in seasons]).filter(
        (pl.col("game_type") == REGULAR) & pl.col("state").is_in(FINAL_STATES))
    j = preds.join(games.select("game_id", (pl.col("home_score") > pl.col("away_score")).cast(pl.Int8).alias("home_win")), on="game_id")
    return {"scored": win_model.score(j["p_home"].to_numpy(), j["home_win"].to_numpy()), "not_final_yet": preds.height - j.height}


def _print(report: dict) -> None:
    for season, r in report.items():
        m = r["model"]
        print(f"\n{season}-{(season + 1) % 100:02d}: {m['games']} games, home team won {r['home_won']:.1%}")
        for label, key in (("no lineups", "model"), ("morning model", "morning_roster"), ("at puck drop", "puck_drop"), ("home ice only", "home_rate_only")):
            if key in r:
                b = r[key]
                print(f"  {label + ':':15} picked {b['hit_rate']:.1%}  Brier {b['brier']:.4f}  log loss {b['log_loss']:.4f}")
        for c in m["calibration"]:
            print(f"    favourite given {c['band']:>8}: {c['games']:4d} games, said {c['said']:.0%}, won {c['won']:.0%}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, help="CSV of saved predictions (game_id, p_home)")
    p.add_argument("--json", type=Path, help="also write the report here")
    a = p.parse_args()
    report = score_file(a.file) if a.file else backtest()
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1)) if a.file else _print(report)


if __name__ == "__main__":
    main()
