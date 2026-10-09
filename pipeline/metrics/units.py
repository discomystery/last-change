"""Forward lines, defense pairs and special-teams usage across a season.

Lines are numbered by 5-on-5 ice time together over a team's last ten games (fewer early in a season).
"""
from collections import defaultdict

import polars as pl

from pipeline.config import REGULAR, TABLES

UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
WINDOW_GAMES = 10
SHRINK_MINUTES = 120  # early-season line results are pulled toward the average for that line number


def game_units(season: int) -> pl.DataFrame:
    """One row per (game, team, forward trio or defense pair): 5v5 seconds together and what happened."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    pos = {(r["game_id"], r["player_id"]): r["pos"] for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    acc: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))

    def keys(gid, skaters):
        fwd = tuple(sorted(i for i in skaters if pos.get((gid, i)) in ("C", "L", "R")))
        dmen = tuple(sorted(i for i in skaters if pos.get((gid, i)) == "D"))
        return [("F", fwd), ("D", dmen)] if len(fwd) == 3 and len(dmen) == 2 else []

    for s in pl.read_parquet(d / "stints.parquet").iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids or not (s["n_home"] == 5 and s["n_away"] == 5 and s["home_goalie"] and s["away_goalie"]):
            continue
        for home, skaters in ((True, s["home_skaters"]), (False, s["away_skaters"])):
            for kind, unit in keys(gid, skaters):
                acc[(gid, ids[gid][0 if home else 1], kind, unit)]["sec"] += s["duration"]

    xg = pl.read_parquet(d / "shots_xg_own.parquet")
    events = pl.read_parquet(d / "events.parquet").filter(pl.col("type").is_in([*UNBLOCKED, "blocked-shot", "faceoff"])).join(xg, on=["game_id", "event_id"], how="left")
    for e in events.iter_rows(named=True):
        gid = e["game_id"]
        if gid not in ids or e["is_home"] is None or not (len(e["home_on"]) == 5 and len(e["away_on"]) == 5 and e["home_goalie"] and e["away_goalie"]):
            continue
        for home, skaters in ((True, e["home_on"]), (False, e["away_on"])):
            mine = e["is_home"] == home
            for kind, unit in keys(gid, skaters):
                u = acc[(gid, ids[gid][0 if home else 1], kind, unit)]
                if e["type"] == "faceoff":
                    if e["zone"] in ("O", "D"):  # zone is from the winner's side
                        u["oz_fo" if (e["zone"] == "O") == mine else "dz_fo"] += 1
                    continue
                u["cf" if mine else "ca"] += 1
                if e["type"] in UNBLOCKED:
                    u["xgf" if mine else "xga"] += e["xg"] or 0.0
                if e["type"] == "goal":
                    u["gf" if mine else "ga"] += 1
    cols = ["sec", "cf", "ca", "xgf", "xga", "gf", "ga", "oz_fo", "dz_fo"]
    out = pl.DataFrame([{"game_id": k[0], "team_id": k[1], "kind": k[2], "unit": "-".join(map(str, k[3])), **{c: float(v.get(c, 0.0)) for c in cols}} for k, v in acc.items()])
    out = out.join(games.select("game_id", "date"), on="game_id")
    out.write_parquet(d / "game_units.parquet")
    return out


def _pick(units: pl.DataFrame, count: int) -> list[dict]:
    """Most ice time first, skipping any group that shares a player with one already chosen."""
    chosen, used = [], set()
    for row in units.sort("sec", descending=True).iter_rows(named=True):
        players = set(row["unit"].split("-"))
        if players & used:
            continue
        chosen.append(row)
        used |= players
        if len(chosen) == count:
            break
    return chosen


def usual(season: int) -> dict[int, dict]:
    """Per team: numbered lines and pairs over the recent window, the last game's groups, and peer ratings."""
    gu = pl.read_parquet(TABLES / str(season) / "game_units.parquet")
    teams = {}
    for (tid,), t in gu.group_by("team_id"):
        dates = t.select("game_id", "date").unique().sort("date", "game_id")
        window = dates.tail(WINDOW_GAMES)["game_id"].to_list()
        last = dates["game_id"][-1]
        recent = t.filter(pl.col("game_id").is_in(window)).group_by("kind", "unit").agg(pl.exclude("game_id", "date", "team_id").sum())
        in_last = t.filter(pl.col("game_id") == last)
        entry = {"games": len(window), "last_game": last, "units": [], "last": []}
        for kind, count, tag in (("F", 4, "L"), ("D", 3, "P")):
            for i, u in enumerate(_pick(recent.filter(pl.col("kind") == kind), count)):
                entry["units"].append({"label": f"{tag}{i + 1}", **u})
            entry["last"] += [{"label": f"{tag}{i + 1}", "unit": u["unit"], "sec": u["sec"]} for i, u in enumerate(_pick(in_last.filter(pl.col("kind") == kind), count))]
        teams[tid] = entry

    # Rate each unit against the same-numbered unit on other teams, after pulling small samples toward that group's average.
    by_label = defaultdict(list)
    for tid, entry in teams.items():
        for u in entry["units"]:
            by_label[u["label"]].append(u)
    for label, group in by_label.items():
        mins = sum(u["sec"] for u in group) / 60
        mean_f, mean_a = sum(u["xgf"] for u in group) / mins * 60, sum(u["xga"] for u in group) / mins * 60
        for u in group:
            m = u["sec"] / 60
            u["minutes"] = round(m, 1)
            u["xgf60"] = (u["xgf"] / m * 60 * m + mean_f * SHRINK_MINUTES) / (m + SHRINK_MINUTES) if m else mean_f
            u["xga60"] = (u["xga"] / m * 60 * m + mean_a * SHRINK_MINUTES) / (m + SHRINK_MINUTES) if m else mean_a
            u["share"] = 100 * u["xgf60"] / (u["xgf60"] + u["xga60"])
            fo = u["oz_fo"] + u["dz_fo"]
            u["oz"] = 100 * u["oz_fo"] / fo if fo else None
        for stat, higher in (("xgf60", True), ("xga60", False), ("share", True), ("sec_pg", True), ("oz", True)):
            for u in group:
                u["sec_pg"] = u["sec"] / teams_games(teams, u)
            vals = [u[stat] for u in group if u[stat] is not None]
            for u in group:
                if u[stat] is None:
                    u[f"{stat}_pct"] = None
                    continue
                below = sum(v < u[stat] for v in vals) if higher else sum(v > u[stat] for v in vals)
                u[f"{stat}_pct"] = round(100 * (below + 0.5) / len(vals))
    return teams


def teams_games(teams: dict, unit: dict) -> int:
    return next(e["games"] for e in teams.values() if unit in e["units"])


def special_usage(season: int) -> pl.DataFrame:
    """Per player and team: share of the team's power-play and penalty-kill time he was on for, in games he dressed."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    dressed = pl.read_parquet(d / "toi_check.parquet").filter(~pl.col("goalie") & (pl.col("shift_toi") > 0)).select("game_id", "player_id")
    player = defaultdict(lambda: defaultdict(float))
    team = defaultdict(lambda: defaultdict(float))
    for s in pl.read_parquet(d / "stints.parquet").iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids or not (s["home_goalie"] and s["away_goalie"]):
            continue
        for home in (True, False):
            own, opp = (s["n_home"], s["n_away"]) if home else (s["n_away"], s["n_home"])
            kind = "pp" if (own, opp) == (5, 4) else "pk" if (own, opp) == (4, 5) else None
            if kind is None:
                continue
            tid = ids[gid][0 if home else 1]
            team[(gid, tid)][kind] += s["duration"]
            for pid in s["home_skaters"] if home else s["away_skaters"]:
                player[(gid, tid, pid)][kind] += s["duration"]
    team_of = {}
    for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True):
        team_of[(r["game_id"], r["player_id"])] = r["team_id"]
    tot = defaultdict(lambda: defaultdict(float))
    for r in dressed.iter_rows(named=True):
        gid, pid = r["game_id"], r["player_id"]
        tid = team_of.get((gid, pid))
        if tid is None or gid not in ids:
            continue
        k = (tid, pid)
        tot[k]["gp"] += 1
        for kind in ("pp", "pk"):
            tot[k][kind] += player.get((gid, tid, pid), {}).get(kind, 0.0)
            tot[k][f"team_{kind}"] += team.get((gid, tid), {}).get(kind, 0.0)
    return pl.DataFrame([{"team_id": k[0], "player_id": k[1], "gp": int(v["gp"]), "pp_share": v["pp"] / v["team_pp"] if v["team_pp"] else 0.0,
                          "pk_share": v["pk"] / v["team_pk"] if v["team_pk"] else 0.0, "pp_min": v["pp"] / 60, "pk_min": v["pk"] / 60} for k, v in tot.items()])
