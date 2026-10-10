"""Score win probabilities against results: hit rate, Brier score, log loss and calibration.

    uv run --project pipeline python -m pipeline.metrics.score_predictions            # backtest the model
    uv run --project pipeline python -m pipeline.metrics.score_predictions --file preds.csv  # score saved predictions

Backtest: each full season is predicted by a model fitted on the other full seasons, and the current season by a
model fitted on all of them, so no game is ever scored by a model that saw it. A saved-predictions file needs the
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
    df = win_model.pregame(win_model.team_games(seasons), params)
    out = {}
    for test in seasons:
        train = df.filter(pl.col("season").is_in([s for s in FULL_SEASONS if s != test]))
        te = df.filter(pl.col("season") == test)
        if te.is_empty():
            continue
        y = te["home_win"].to_numpy()
        m = win_model.fit(train)
        out[test] = {"model": win_model.score(win_model.predict(m, te), y),
                     "home_rate_only": win_model.score(np.full(len(y), train["home_win"].mean()), y),
                     "home_won": float(y.mean()),
                     "weights": {"home": float(m.intercept_[0]), **{f: float(c) for f, c in zip(win_model.FEATURES, m.coef_[0])}}}
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
        m, b = r["model"], r["home_rate_only"]
        print(f"\n{season}-{(season + 1) % 100:02d}: {m['games']} games, home team won {r['home_won']:.1%}")
        print(f"  model:          picked {m['hit_rate']:.1%}  Brier {m['brier']:.4f}  log loss {m['log_loss']:.4f}")
        print(f"  home ice only:  picked {b['hit_rate']:.1%}  Brier {b['brier']:.4f}  log loss {b['log_loss']:.4f}")
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
