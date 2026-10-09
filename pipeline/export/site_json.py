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
    export_lines(season)
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


# Traits whose numbers are checked and ready to show.
READY = ["volume", "quality", "rush", "rebounds", "turnover", "point", "suppression", "qualityAllowed", "breakdowns", "goalie", "pace", "forecheck", "physical", "depth", "pp", "pk", "powerKill", "discipline"]


def export_fingerprints(season: int) -> int:
    from pipeline.metrics import team_style

    out = team_style.run(season)
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    abbr = {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
    from pipeline.metrics.team_style import INDEXED

    def dim(t, d):
        est = t["dims"][d]
        if d in INDEXED:  # carry the as-recorded rate alongside the arena-adjusted score
            est = {mode: {**est[mode], "raw": t["dims"][INDEXED[d]][mode]["v"], "raw_rank": t["dims"][INDEXED[d]][mode]["rank"]} for mode in est}
        return est

    teams = {abbr[tid]: {"games": t["games"], "dims": {d: dim(t, d) for d in READY}} for tid, t in out["teams"].items()}
    from pipeline.metrics import rink_bias
    (SITE_DATA / "arena_factors.json").write_text(json.dumps({"seasons": "2023-24 to 2025-26", "home_road": rink_bias.home_road(), "arenas": rink_bias.factors().to_dicts()}, separators=(",", ":")))
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season, "ready": READY,
               "stabilization": {d: out["stabilization"][d] for d in READY}, "teams": teams}
    (SITE_DATA / "fingerprints.json").write_text(json.dumps(payload, separators=(",", ":"), default=float))
    return len(teams)


def export_xg_check(season: int) -> int:
    """For each team's most recent game: every goal and the best chances, with our rating beside MoneyPuck's."""
    import pickle

    from pipeline.metrics import xg_model

    bundle = pickle.loads(xg_model.MODEL_PATH.read_bytes())
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").sort("game_id")
    feats = pl.read_parquet(d / "shot_features.parquet").filter(pl.col("type") != "blocked-shot")
    feats = feats.with_columns(ours=pl.Series(xg_model.predict(bundle, feats)))
    events = pl.read_parquet(d / "events.parquet").select("game_id", "event_id", "p1", "period")
    mp = pl.read_parquet(d / "shots_xg.parquet").select("game_id", "event_id", pl.col("xGoal").alias("mp"))
    players = pl.read_parquet(d / "players.parquet")
    shots = feats.join(events, on=["game_id", "event_id"]).join(mp, on=["game_id", "event_id"], how="left")
    built = set(shots["game_id"].unique().to_list())
    latest = {}
    for g in games.filter(pl.col("game_id").is_in(list(built))).iter_rows(named=True):
        latest[g["home"]] = latest[g["away"]] = g
    before = {"none": "after a whistle", "shot-on-goal": "after a save", "missed-shot": "after a missed shot", "blocked-shot": "after a blocked shot",
              "goal": "after a goal", "hit": "after a hit", "giveaway": "after a giveaway", "takeaway": "after a takeaway", "faceoff": "off a faceoff",
              "penalty": "after a penalty", "delayed-penalty": "on a delayed penalty"}
    out = []
    for gid in sorted({g["game_id"] for g in latest.values()}):
        g = games.filter(pl.col("game_id") == gid).to_dicts()[0]
        name = {r["player_id"]: f'{r["first"][0]}. {r["last"]}' for r in players.filter(pl.col("game_id") == gid).iter_rows(named=True)}
        s = shots.filter(pl.col("game_id") == gid)
        chances = s.filter(~pl.col("goal")).sort("ours", descending=True).head(8)
        rows = []
        for r in pl.concat([s.filter(pl.col("goal")), chances]).sort("sec").iter_rows(named=True):
            clock = r["sec"] - (r["period"] - 1) * 1200
            strength = "empty net" if r["empty_net"] else f'{r["own_skaters"]}-on-{r["opp_skaters"]}'
            rows.append({"goal": r["goal"], "team": g["home"] if r["is_home"] else g["away"], "who": name.get(r["p1"], "Unknown"),
                         "when": f'P{r["period"]} {clock // 60}:{clock % 60:02d}', "shot": (r["shot_type"] or "shot").replace("-", " "), "feet": round(r["dist"]),
                         "strength": strength, "before": f'{round(r["prev_gap"])}s {before.get(r["prev_type"], "after play")}' if r["prev_type"] != "none" else "after a whistle",
                         "ours": round(r["ours"], 3), "mp": None if r["mp"] is None else round(r["mp"], 3)})
        tot = {side: {"ours": round(s.filter(pl.col("is_home") == (side == "home"))["ours"].sum(), 2),
                      "mp": round(s.filter(pl.col("is_home") == (side == "home"))["mp"].sum() or 0.0, 2)} for side in ("home", "away")}
        out.append({"id": gid, "date": g["date"], "home": g["home"], "away": g["away"], "hs": g["home_score"], "as": g["away_score"], "totals": tot, "shots": rows})
    (SITE_DATA / "xg_check.json").write_text(json.dumps({"games": out}, separators=(",", ":")))
    return len(out)


def export_lines(season: int) -> int:
    """Usual lines and pairs for every team, how the last game differed, and notes on changed special-teams roles."""
    from pipeline.metrics import units

    d = TABLES / str(season)
    units.game_units(season)
    usual = units.usual(season)
    games = pl.read_parquet(d / "games.parquet")
    abbr = {**dict(zip(games["home_id"], games["home"])), **dict(zip(games["away_id"], games["away"]))}
    info = {g["game_id"]: g for g in games.iter_rows(named=True)}
    names = {}
    for s_ in (season - 1, season):
        for r in pl.read_parquet(TABLES / str(s_) / "players.parquet").iter_rows(named=True):
            names[r["player_id"]] = f'{r["first"][0]}. {r["last"]}'
    now = units.special_usage(season)
    before = units.special_usage(season - 1).group_by("player_id").agg(pl.col("gp").sum(), (pl.col("pp_share") * pl.col("gp")).sum() / pl.col("gp").sum(), (pl.col("pk_share") * pl.col("gp")).sum() / pl.col("gp").sum())
    usage = now.join(before, on="player_id", how="left", suffix="_last")
    words = {"pk": ("killing penalties", "shorthanded"), "pp": ("playing on the power play", "power-play")}
    out = {}
    for tid, e in usual.items():
        place = abbr[tid]
        notes = []
        for r in usage.filter((pl.col("team_id") == tid) & (pl.col("gp") >= 3) & (pl.col("gp_last").fill_null(0) >= 20)).iter_rows(named=True):
            for kind, started, stopped in (("pk", 0.25, 0.30), ("pp", 0.40, 0.45)):
                cur, old = r[f"{kind}_share"], r[f"{kind}_share_last"]
                doing, label = words[kind]
                if cur >= started and old < 0.08:
                    notes.append({"player": names[r["player_id"]], "kind": kind, "change": "new", "now": round(100 * cur), "before": round(100 * old), "games": r["gp"],
                                  "text": f'{names[r["player_id"]]} has started {doing}: on the ice for {round(100 * cur)}% of the team\u2019s {label} time in {r["gp"]} games, up from {round(100 * old)}% last season.'})
                elif old >= stopped and cur < 0.05:
                    notes.append({"player": names[r["player_id"]], "kind": kind, "change": "gone", "now": round(100 * cur), "before": round(100 * old), "games": r["gp"],
                                  "text": f'{names[r["player_id"]]} is no longer {doing}: {round(100 * cur)}% of the team\u2019s {label} time in {r["gp"]} games, down from {round(100 * old)}% last season.'})
        g = info[e["last_game"]]
        usual_sets = {u["label"]: u["unit"] for u in e["units"]}
        last_sets = {u["unit"] for u in e["last"]}
        fmt = lambda unit: [names.get(int(i), "?") for i in unit.split("-")]
        out[place] = {
            "games": e["games"],
            "units": [{"label": u["label"], "players": fmt(u["unit"]), "ids": [int(i) for i in u["unit"].split("-")], "minutes": u["minutes"], "toi": round(u["sec_pg"] / 60, 1),
                       "xgf60": round(u["xgf60"], 2), "xga60": round(u["xga60"], 2), "share": round(u["share"], 1), "oz": None if u["oz"] is None else round(u["oz"]),
                       "pct": {k: u[f"{k}_pct"] for k in ("xgf60", "xga60", "share", "sec_pg", "oz")}, "in_last_game": u["unit"] in last_sets} for u in e["units"]],
            "last": {"date": g["date"], "opponent": g["away"] if g["home"] == place else g["home"], "at_home": g["home"] == place,
                     "units": [{"label": u["label"], "players": fmt(u["unit"]), "minutes": round(u["sec"] / 60, 1), "usual": u["unit"] in usual_sets.values()} for u in e["last"]]},
            "notes": sorted(notes, key=lambda n: -abs(n["now"] - n["before"])),
        }
    special = units.special_units(season)
    for tid, sp in special.items():
        if abbr[tid] not in out:
            continue
        for kind in ("pp", "pk"):
            for u in sp[kind]:
                u["players"] = [{"id": p, "name": names.get(p, "?"), "role": u["roles"].get(str(p))} for p in u["players"]]
                del u["roles"]
        out[abbr[tid]]["special"] = {"pp": sp["pp"], "pk": sp["pk"], "shots": [{"x": s_["x"], "y": s_["y"], "xg": s_["xg"], "p": s_["p"], "who": names.get(s_["p"], "?")} for s_ in sp["shots"]]}
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season, "window": units.WINDOW_GAMES, "teams": out}
    (SITE_DATA / "lines.json").write_text(json.dumps(payload, separators=(",", ":"), default=float))
    return len(out)
