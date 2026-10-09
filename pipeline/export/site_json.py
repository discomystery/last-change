"""Write the JSON the website reads. Phase 2 exports a real-data check: each team's last-game lines."""
import json
from datetime import datetime, timezone

import polars as pl

from pipeline.build import join_xg, validate
from pipeline.config import SITE_DATA, TABLES
from pipeline.metrics.lines import game_units


def run(season: int) -> dict:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").sort("game_id")
    players = pl.read_parquet(d / "players.parquet")
    built = set(pl.read_parquet(d / "events.parquet")["game_id"].unique().to_list())
    teams = {}
    for g in games.filter(pl.col("game_id").is_in(list(built))).iter_rows(named=True):
        for side, abbr in (("home", g["home"]), ("away", g["away"])):
            teams[abbr] = (g, side)  # later games overwrite earlier ones: ends as the most recent
    out = []
    for abbr, (g, side) in sorted(teams.items()):
        units = game_units(season, g["game_id"])
        roster = players.filter(pl.col("game_id") == g["game_id"])
        name = {r["player_id"]: f'{r["first"][0]}. {r["last"]}' for r in roster.iter_rows(named=True)}

        def rows(size, top):
            picked = sorted(((k, v) for k, v in units[side].items() if len(k) == size), key=lambda kv: -kv[1]["sec"])[:top]
            return [{"players": [name[i] for i in k], "seconds": v["sec"], "cf": v["cf"], "ca": v["ca"],
                     "xgf": round(v["xgf"], 2), "xga": round(v["xga"], 2), "gf": v["gf"], "ga": v["ga"]} for k, v in picked]

        opp = g["away"] if side == "home" else g["home"]
        out.append({"team": abbr, "game_id": g["game_id"], "date": g["date"], "opponent": opp, "at_home": side == "home",
                    "score": [g[f"{side}_score"], g["away_score" if side == "home" else "home_score"]],
                    "five_on_five_seconds": units["five_on_five_seconds"], "lines": rows(3, 4), "pairs": rows(2, 3)})
    checks = validate.run(season)
    checks.pop("worst_toi_games", None)
    xg = join_xg.run(season)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season,
               "checks": {**checks, "xg_match_rate_pct": xg["match_rate_pct"], "xg_shots": xg["moneypuck_shots"]}, "teams": out}
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "last_game_lines.json").write_text(json.dumps(payload, separators=(",", ":")))
    return {"teams": len(out), "file": "site/public/data/last_game_lines.json"}
