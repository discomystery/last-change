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
    n_teams, n_games = export_teams_and_schedule(season)
    export_fingerprints(season)
    return {"teams": len(out), "team_list": n_teams, "schedule_games": n_games}


def export_teams_and_schedule(season: int) -> tuple[int, int]:
    """Team names and the full season schedule, for the home page and the team picker."""
    from pipeline.config import NHL_WEB
    from pipeline.ingest import nhl
    from pipeline.ingest.client import get

    standings = get(f"{NHL_WEB}/v1/standings/now").json()["standings"]
    teams = sorted(({"abbr": t["teamAbbrev"]["default"], "name": t["teamName"]["default"], "place": t["placeName"]["default"],
                     "nick": t["teamCommonName"]["default"], "w": t["wins"], "l": t["losses"], "otl": t["otLosses"]} for t in standings), key=lambda t: t["name"])
    (SITE_DATA / "teams.json").write_text(json.dumps(teams, separators=(",", ":")))
    games = [{"id": g["game_id"], "start": g["start_utc"], "date": g["date"], "home": g["home"], "away": g["away"], "venue": g["venue"],
              "final": g["state"] in ("OFF", "FINAL"), "hs": g["home_score"], "as": g["away_score"], "end": g["last_period"]}
             for g in nhl.season_games(season) if g["game_type"] == 2]
    (SITE_DATA / "schedule.json").write_text(json.dumps({"season": season, "games": games}, separators=(",", ":")))
    return len(teams), len(games)


# Traits whose numbers are checked and ready to show. Second chances is held back: MoneyPuck's 2026-27
# file values rebound shots about half as highly as its earlier-season files, so seasons cannot be blended yet.
READY = ["volume", "quality", "rush", "turnover", "point", "suppression", "qualityAllowed", "goalie", "pace", "pp", "pk", "powerKill"]


def export_fingerprints(season: int) -> int:
    from pipeline.metrics import team_style

    out = team_style.run(season)
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    abbr = {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
    teams = {abbr[tid]: {"games": t["games"], "dims": {d: t["dims"][d] for d in READY}} for tid, t in out["teams"].items()}
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season, "ready": READY,
               "stabilization": {d: out["stabilization"][d] for d in READY}, "teams": teams}
    (SITE_DATA / "fingerprints.json").write_text(json.dumps(payload, separators=(",", ":"), default=float))
    return len(teams)
