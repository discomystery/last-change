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


def depth_table(season: int) -> pl.DataFrame:
    """Per team-game: how much of the forwards' 5v5 ice time went to the six least-used forwards that night."""
    d = TABLES / str(season)
    st = pl.read_parquet(d / "stints.parquet").filter((pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
    pos = pl.read_parquet(d / "players.parquet").select("game_id", "player_id", "team_id", "pos")
    long = pl.concat([st.select("game_id", "duration", pl.col(c).alias("player_id")).explode("player_id") for c in ("home_skaters", "away_skaters")])
    toi = long.group_by("game_id", "player_id").agg(pl.col("duration").sum().alias("sec")).join(pos, on=["game_id", "player_id"]).filter(pl.col("pos").is_in(["C", "L", "R"]))
    toi = toi.with_columns(rank=pl.col("sec").rank("ordinal", descending=True).over("game_id", "team_id"))
    out = toi.group_by("game_id", "team_id").agg(pl.col("sec").filter(pl.col("rank") > 6).sum().cast(pl.Float64).alias("bottom6_sec"), pl.col("sec").sum().cast(pl.Float64).alias("fwd_sec"))
    out.write_parquet(d / "depth.parquet")
    return out


def special_units(season: int) -> dict[int, dict]:
    """Power-play and penalty-kill units over the recent window, with player roles and a shot map."""
    seasons = [season - 1, season]
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    order = games.sort("date", "game_id")
    recent = {}
    for tid in set(games["home_id"]) | set(games["away_id"]):
        built = order.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid))["game_id"].to_list()
        recent[tid] = set(built)
    have = set(pl.read_parquet(d / "stints.parquet")["game_id"].unique().to_list())
    recent = {t: set(sorted(g & have)[-WINDOW_GAMES:]) for t, g in recent.items()}

    unit = defaultdict(lambda: defaultdict(float))  # (team, kind, players) -> sums
    team_sec = defaultdict(float)
    for s in pl.read_parquet(d / "stints.parquet").iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids or not (s["home_goalie"] and s["away_goalie"]):
            continue
        for home in (True, False):
            own, opp = (s["n_home"], s["n_away"]) if home else (s["n_away"], s["n_home"])
            kind = "pp" if (own, opp) == (5, 4) else "pk" if (own, opp) == (4, 5) else None
            tid = ids[gid][0 if home else 1]
            if kind is None or gid not in recent[tid]:
                continue
            key = (tid, kind, tuple(s["home_skaters"] if home else s["away_skaters"]))
            unit[key]["sec"] += s["duration"]
            team_sec[(tid, kind)] += s["duration"]

    # Shots during 5v4: unit results this season, plus individual tendencies over two seasons for role tags.
    ind = defaultdict(lambda: defaultdict(float))
    shots = defaultdict(list)
    for yr in seasons:
        dd = TABLES / str(yr)
        g2 = pl.read_parquet(dd / "games.parquet").filter(pl.col("game_type") == REGULAR)
        id2 = {g["game_id"]: (g["home_id"], g["away_id"]) for g in g2.iter_rows(named=True)}
        ppos = {(r["game_id"], r["player_id"]): r["pos"] for r in pl.read_parquet(dd / "players.parquet").iter_rows(named=True)}
        ev = pl.read_parquet(dd / "events.parquet").filter(pl.col("type").is_in([*UNBLOCKED, "blocked-shot"])).join(pl.read_parquet(dd / "shots_xg_own.parquet"), on=["game_id", "event_id"], how="left").sort("game_id", "sort")
        for e in ev.iter_rows(named=True):
            gid, home = e["game_id"], e["is_home"]
            if gid not in id2 or home is None or not (e["home_goalie"] and e["away_goalie"]):
                continue
            own, opp = (e["home_on"], e["away_on"]) if home else (e["away_on"], e["home_on"])
            if (len(own), len(opp)) != (5, 4):
                continue
            tid, opp_tid = id2[gid][0 if home else 1], id2[gid][1 if home else 0]
            xg = e["xg"] or 0.0
            p = ind[e["p1"]]
            p["att"] += 1
            p["ixg"] += xg
            if e["x_norm"] is not None:
                dist = ((89 - e["x_norm"]) ** 2 + e["y_norm"] ** 2) ** 0.5
                p["close"] += dist <= 15
                p["dist"] += dist
                p["absy"] += abs(e["y_norm"])
                p["located"] += 1
            p["is_d"] = ppos.get((gid, e["p1"])) == "D"
            if e["type"] == "goal" and e["p2"]:
                ind[e["p2"]]["a1"] += 1
            if e["type"] in UNBLOCKED and e["x_norm"] is not None:
                shots[tid].append({"x": int(e["x_norm"]), "y": int(e["y_norm"]), "xg": round(xg, 3), "game_id": gid})
            if yr == season and gid in recent.get(tid, ()) and e["type"] in UNBLOCKED:
                unit[(tid, "pp", tuple(own))]["xg"] += xg
            if yr == season and gid in recent.get(opp_tid, ()) and e["type"] in UNBLOCKED:
                unit[(opp_tid, "pk", tuple(opp))]["xg"] += xg

    def role(pid: int, taken: set) -> str | None:
        p = ind.get(pid)
        if not p or p["att"] < 8:
            return None
        if p.get("is_d"):
            return "Point"
        if p["located"] and p["close"] / p["located"] >= 0.4:
            return "Net-front"
        return None

    league = {k: [0.0, 0.0] for k in ("pp", "pk")}
    for (tid, kind, _), v in unit.items():
        league[kind][0] += v.get("xg", 0.0)
        league[kind][1] += v["sec"]
    out = {}
    for tid in recent:
        entry = {"games": len(recent[tid]), "pp": [], "pk": [], "shots": sorted(shots.get(tid, []), key=lambda s: s["game_id"])[-140:]}
        for kind, count in (("pp", 2), ("pk", 2)):
            rows = pl.DataFrame([{"unit": "-".join(map(str, k[2])), "sec": v["sec"], "xg": v.get("xg", 0.0)} for k, v in unit.items() if k[0] == tid and k[1] == kind] or [{"unit": "", "sec": 0.0, "xg": 0.0}])
            mean = league[kind][0] / league[kind][1] * 3600
            for i, u in enumerate(_pick(rows.filter(pl.col("sec") > 0), count)):
                players = [int(x) for x in u["unit"].split("-")]
                m = u["sec"] / 60
                rate = (u["xg"] / m * 60 * m + mean * 20) / (m + 20)  # pulled toward the league average: 20 minutes of weight
                roles = {}
                if kind == "pp":
                    rated = sorted(players, key=lambda p: -(ind.get(p, {}).get("ixg", 0.0)))
                    if ind.get(rated[0], {}).get("att", 0) >= 8:
                        roles[rated[0]] = "Trigger"
                    passers = sorted((p for p in players if p not in roles), key=lambda p: -(ind.get(p, {}).get("a1", 0.0)))
                    if passers and ind.get(passers[0], {}).get("a1", 0) >= 3:
                        roles[passers[0]] = "Distributor"
                    for p in players:
                        if p not in roles and (r := role(p, set())):
                            roles[p] = r
                entry[kind].append({"label": f"{kind.upper()}{i + 1}", "players": players, "roles": {str(p): roles.get(p) for p in players}, "minutes": round(m, 1),
                                    "share": round(100 * u["sec"] / team_sec[(tid, kind)]) if team_sec[(tid, kind)] else 0, "rate": round(rate, 2)})
        out[tid] = entry
    for kind, higher in (("pp", True), ("pk", False)):
        for i in range(2):
            vals = [e[kind][i]["rate"] for e in out.values() if len(e[kind]) > i]
            for e in out.values():
                if len(e[kind]) > i:
                    v = e[kind][i]["rate"]
                    below = sum(x < v for x in vals) if higher else sum(x > v for x in vals)
                    e[kind][i]["pct"] = round(100 * (below + 0.5) / len(vals))
    return out
