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


def _faceoff_specialist(draws: dict, stays: dict, swaps: list, toi: dict) -> dict | None:
    """A player sent out mainly to take power-play draws, who then changes for a regular.

    He must take a large share of the team's power-play faceoffs, stay out for much shorter stretches than the
    regulars, and usually be replaced one-for-one in the middle of the power play.
    """
    total = sum(draws.values())
    if total < 8:
        return None
    regulars = sorted(toi, key=lambda p: -toi[p])[:6]
    # Check every heavy draw-taker, not just the busiest: over a full season the top centre takes the most draws
    # and is a regular, while the specialist sits second.
    for pid in sorted(draws, key=lambda p: -draws[p]):
        if draws[pid] / total < 0.25:
            break
        mine = [x for x in stays.get(pid, []) if x > 0]
        others = [x for p in regulars if p != pid for x in stays.get(p, []) if x > 0]
        exits = [on for off, on, _ in swaps if off == pid]
        if not mine or not others or len(exits) < max(3, 0.3 * len(mine)):
            continue
        avg, usual = sum(mine) / len(mine), sum(others) / len(others)
        if avg > 0.65 * usual:
            continue
        by = defaultdict(int)
        for on in exits:
            by[on] += 1
        return {"player": pid, "draw_pct": round(100 * draws[pid] / total), "draws": draws[pid], "avg_stay": round(avg), "usual_stay": round(usual),
                "replaced_by": sorted(by.items(), key=lambda kv: -kv[1])[:3], "exits": len(exits)}
    return None


def _quarterback_units(spans: list, toi: dict, is_d: dict, *, total_sec: float, specialist: dict | None = None, swaps: list = ()) -> tuple[list[frozenset], list[int], dict]:
    """Two power-play units, each anchored on its quarterback. Returns (member sets, anchors, stand-ins).

    If the team uses a faceoff specialist, his time is credited to the regular who replaces him with each unit,
    and he is reported alongside that player rather than as a member.
    """
    if not toi:
        return [], [], {}

    def pick_anchor(times: dict, pool_sec: float, exclude: set) -> int | None:
        cands = {p: t for p, t in times.items() if p not in exclude}
        if not cands:
            return None
        dmen = {p: t for p, t in cands.items() if is_d.get(p)}
        # Use a defenseman when one clearly runs things; a five-forward unit is anchored on its busiest player.
        if dmen and max(dmen.values()) >= 0.25 * pool_sec:
            return max(dmen, key=dmen.get)
        return max(cands, key=cands.get)

    a1 = pick_anchor(toi, total_sec, set())
    off = defaultdict(float)
    off_sec = 0.0
    for sec, on in spans:
        if a1 not in on:
            off_sec += sec
            for p in on:
                off[p] += sec
    a2 = pick_anchor(off, off_sec, {a1}) if off_sec else None
    anchors = [a for a in (a1, a2) if a is not None]
    spec = specialist["player"] if specialist else None
    members, stand_for = [], {}
    for k, a in enumerate(anchors):
        other = anchors[1 - k] if len(anchors) == 2 else None
        shared = defaultdict(float)
        for sec, on in spans:
            if a in on and other not in on:
                for p in on:
                    if p != a:
                        shared[p] += sec
        if spec is not None and spec in shared:
            # Who comes on for the specialist while this quarterback is out?
            incoming = defaultdict(int)
            for off_p, on_p, after in swaps:
                if off_p == spec and a in after and other not in after:
                    incoming[on_p] += 1
            if incoming:
                heir = max(incoming, key=incoming.get)
                shared[heir] += shared.pop(spec)
                stand_for[(k, heir)] = spec
            else:
                shared.pop(spec)
        mates = sorted((p for p in shared if p not in anchors), key=lambda p: -shared[p])[:4]
        members.append(frozenset([a, *mates]))
    stand_for = {key: v for key, v in stand_for.items() if key[1] in members[key[0]]}
    return members, anchors, stand_for


def special_units(season: int) -> dict[int, dict]:
    """Power-play and penalty-kill units over the recent window, with player roles and a shot map.

    Power play: a unit is built around its quarterback, the defenseman running it from the blue line (the way a fan
    tells the units apart). PP1's quarterback is the defenseman with the most power-play time; PP2's is the one with
    the most time while the first is off. Each unit is its quarterback plus the four skaters most often out with him.
    A forward who stays out for both units is listed on both. Each moment is credited to the unit whose quarterback
    is on the ice. Penalty kill: the two forwards and two defensemen who kill most, then the next two of each.
    """
    seasons = [season - 1, season]
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    stints = pl.read_parquet(d / "stints.parquet")
    have = set(stints["game_id"].unique().to_list())
    recent = {}
    for tid in set(games["home_id"]) | set(games["away_id"]):
        mine = games.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid)).sort("date", "game_id")["game_id"].to_list()
        recent[tid] = set([g for g in mine if g in have][-WINDOW_GAMES:])

    is_d = {r["player_id"]: r["pos"] == "D" for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    spans = defaultdict(list)  # (team, kind) -> [(seconds, skaters)]
    swaps = defaultdict(list)  # team -> [(player off, player on, skaters after)] for one-for-one changes mid power play
    stays = defaultdict(lambda: defaultdict(list))  # team -> player -> lengths of each unbroken power-play appearance
    open_run, last_pp = {}, {}
    for s in stints.sort("game_id", "start").iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids or not (s["home_goalie"] and s["away_goalie"]):
            last_pp.pop(ids.get(gid, (None, None))[0], None)
            last_pp.pop(ids.get(gid, (None, None))[1], None)
            continue
        for home in (True, False):
            own, opp = (s["n_home"], s["n_away"]) if home else (s["n_away"], s["n_home"])
            kind = "pp" if (own, opp) == (5, 4) else "pk" if (own, opp) == (4, 5) else None
            tid = ids[gid][0 if home else 1]
            if kind and gid in recent[tid]:
                spans[(tid, kind)].append((s["duration"], frozenset(s["home_skaters"] if home else s["away_skaters"])))
            if gid not in recent[tid]:
                continue
            now = frozenset(s["home_skaters"] if home else s["away_skaters"]) if kind == "pp" else None
            prev = last_pp.get(tid)
            carried = prev is not None and prev[0] == gid and prev[1] == s["start"]
            runs = open_run.setdefault(tid, {})
            if now is None or not carried:
                for pid, sec in runs.items():
                    stays[tid][pid].append(sec)
                runs.clear()
            if now is not None:
                if carried:
                    off, on = prev[2] - now, now - prev[2]
                    if len(off) == 1 and len(on) == 1:
                        swaps[tid].append((next(iter(off)), next(iter(on)), now))
                    for pid in off:
                        stays[tid][pid].append(runs.pop(pid, 0.0))
                for pid in now:
                    runs[pid] = runs.get(pid, 0.0) + s["duration"]
                last_pp[tid] = (gid, s["end"], now)
            else:
                last_pp.pop(tid, None)

    # Who takes the power-play faceoffs.
    draws = defaultdict(lambda: defaultdict(int))
    fo = pl.read_parquet(d / "events.parquet").filter(pl.col("type") == "faceoff")
    for e in fo.iter_rows(named=True):
        gid = e["game_id"]
        if gid not in ids or not (e["home_goalie"] and e["away_goalie"]):
            continue
        for home in (True, False):
            own, opp = (e["home_on"], e["away_on"]) if home else (e["away_on"], e["home_on"])
            tid = ids[gid][0 if home else 1]
            if (len(own), len(opp)) == (5, 4) and gid in recent[tid]:
                taker = e["p1"] if e["p1"] in own else e["p2"] if e["p2"] in own else None
                if taker:
                    draws[tid][taker] += 1

    # Role tags read each player's own power-play shots and assists over this season and last, but only with the team
    # he plays for now: a player who has changed teams is judged on his new role, not his old one.
    current = pl.read_parquet(d / "players.parquet").join(games.select("game_id", "date"), on="game_id").sort("date", "game_id").unique("player_id", keep="last")
    now_team = dict(zip(current["player_id"].to_list(), current["team_id"].to_list()))
    ind = defaultdict(lambda: defaultdict(float))
    shots = defaultdict(list)
    chances = defaultdict(list)  # (team, kind) -> [(xg, skaters)] in the window
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
            mine = now_team.get(e["p1"], tid) == tid
            p = ind[e["p1"]] if mine else defaultdict(float)  # with another team: not counted toward his tags
            p["att"] += 1
            p["ixg"] += xg
            if e["x_norm"] is not None:
                p["close"] += ((89 - e["x_norm"]) ** 2 + e["y_norm"] ** 2) ** 0.5 <= 15
                p["located"] += 1
            p["is_d"] = ppos.get((gid, e["p1"])) == "D"
            if e["type"] == "goal" and e["p2"] and now_team.get(e["p2"], tid) == tid:
                ind[e["p2"]]["a1"] += 1
            if e["type"] in UNBLOCKED and e["x_norm"] is not None:
                shots[tid].append({"x": int(e["x_norm"]), "y": int(e["y_norm"]), "xg": round(xg, 3), "game_id": gid, "p": e["p1"]})
            if yr == season and e["type"] in UNBLOCKED:
                if gid in recent.get(tid, ()):
                    chances[(tid, "pp")].append((xg, frozenset(own)))
                if gid in recent.get(opp_tid, ()):
                    chances[(opp_tid, "pk")].append((xg, frozenset(opp)))

    def owner(members: list[frozenset], on: frozenset) -> int | None:
        counts = [len(m & on) for m in members]
        return None if not members or max(counts) == 0 else counts.index(max(counts))

    out, pool = {}, {"pp": [0.0, 0.0], "pk": [0.0, 0.0]}
    for tid in recent:
        entry = {"games": len(recent[tid]), "pp": [], "pk": [], "shots": sorted(shots.get(tid, []), key=lambda s: s["game_id"])[-140:]}
        for kind, size in (("pp", 5), ("pk", 4)):
            toi = defaultdict(float)
            for sec, on in spans[(tid, kind)]:
                for pid in on:
                    toi[pid] += sec
            ranked = sorted(toi, key=lambda p: -toi[p])
            anchors, stand_for = [], {}
            if kind == "pk":
                # A kill is two forwards and two defensemen, so rank each position separately.
                fwd = [p for p in ranked if not is_d.get(p, False)]
                dmen = [p for p in ranked if is_d.get(p, False)]
                members = [frozenset(fwd[i * 2:(i + 1) * 2] + dmen[i * 2:(i + 1) * 2]) for i in range(2) if len(fwd) >= (i + 1) * 2 and len(dmen) >= (i + 1) * 2]
            else:
                specialist = _faceoff_specialist(draws[tid], stays[tid], swaps[tid], toi)
                members, anchors, stand_for = _quarterback_units(spans[(tid, kind)], toi, is_d, total_sec=sum(sec for sec, _ in spans[(tid, kind)]), specialist=specialist, swaps=swaps[tid])
                entry["faceoff"] = specialist
            total = sum(sec for sec, _ in spans[(tid, kind)])
            secs, xgs = [0.0] * len(members), [0.0] * len(members)

            def credit(on):
                if kind == "pp":
                    there = [a in on for a in anchors]
                    if sum(there) == 1:
                        return there.index(True)
                return owner(members, on)

            for sec, on in spans[(tid, kind)]:
                if (k := credit(on)) is not None:
                    secs[k] += sec
            for xg, on in chances[(tid, kind)]:
                if (k := credit(on)) is not None:
                    xgs[k] += xg
            pool[kind][0] += sum(xgs)
            pool[kind][1] += sum(secs)
            for k, m in enumerate(members):
                players = sorted(m, key=lambda p: (is_d.get(p, False) if kind == "pk" else p != anchors[k], -toi[p]))
                roles = {}
                if kind == "pp":
                    if is_d.get(anchors[k]):
                        roles[anchors[k]] = "Quarterback"
                    shooters = sorted((p for p in players if p not in roles), key=lambda p: -(ind.get(p, {}).get("ixg", 0.0)))
                    if ind.get(shooters[0], {}).get("att", 0) >= 8:
                        roles[shooters[0]] = "Trigger"
                    passers = sorted((p for p in players if p not in roles), key=lambda p: -(ind.get(p, {}).get("a1", 0.0)))
                    if passers and ind.get(passers[0], {}).get("a1", 0) >= 3:
                        roles[passers[0]] = "Distributor"
                    for pid in players:
                        q = ind.get(pid)
                        if pid in roles or not q or q["att"] < 8:
                            continue
                        if q.get("is_d") and pid != anchors[k]:
                            roles[pid] = "Point"
                        elif q["located"] and q["close"] / q["located"] >= 0.4:
                            roles[pid] = "Net-front"
                both = [p for p in players if kind == "pp" and sum(p in mm for mm in members) > 1]
                entry[kind].append({"label": f"{kind.upper()}{k + 1}", "players": players, "roles": {str(p): roles.get(p) for p in players}, "both": both,
                                    "after_draw": {str(p): stand_for[(k, p)] for p in players if kind == "pp" and (k, p) in stand_for}, "minutes": round(secs[k] / 60, 1), "seconds": round(secs[k]),
                                    "share": round(100 * secs[k] / total) if total else 0, "_xg": xgs[k], "_sec": secs[k]})
        out[tid] = entry
    for kind, higher in (("pp", True), ("pk", False)):
        mean = pool[kind][0] / pool[kind][1] * 3600
        for e in out.values():
            for u in e[kind]:
                m = u.pop("_sec") / 60
                u["rate"] = round((u.pop("_xg") + mean / 60 * 20) / (m + 20) * 60, 2)  # 20 minutes of league-average weight
        for i in range(2):
            vals = [e[kind][i]["rate"] for e in out.values() if len(e[kind]) > i]
            for e in out.values():
                if len(e[kind]) > i:
                    v = e[kind][i]["rate"]
                    below = sum(x < v for x in vals) if higher else sum(x > v for x in vals)
                    e[kind][i]["pct"] = round(100 * (below + 0.5) / len(vals))
    return out


def matchups(season: int, usual_units: dict[int, dict]) -> dict[int, dict]:
    """Who each line and pair plays against at 5v5: share of its ice time spent facing each opposing forward line.

    Both teams' groups are their usual numbered lines; a group on the ice counts as a given line when at least two of
    its three forwards (or both defensemen, else one) belong to it. Split by all games, home and road, because the
    home coach changes last and so chooses the matchups.
    """
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    pos = {(r["game_id"], r["player_id"]): r["pos"] for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    sets = {tid: {u["label"]: {int(x) for x in u["unit"].split("-")} for u in e["units"]} for tid, e in usual_units.items()}

    def label(tid: int, players: set, kind: str) -> str | None:
        best, score = None, 0
        for lab, members in sets.get(tid, {}).items():
            if lab[0] != kind:
                continue
            n = len(members & players)
            if n > score:
                best, score = lab, n
        need = 2 if kind == "L" else 1
        return best if score >= need else None

    acc = defaultdict(lambda: defaultdict(float))  # (team, venue) -> (own label, opp label) -> seconds
    played = defaultdict(set)
    for s in pl.read_parquet(d / "stints.parquet").iter_rows(named=True):
        gid = s["game_id"]
        if gid not in ids or not (s["n_home"] == 5 and s["n_away"] == 5 and s["home_goalie"] and s["away_goalie"]):
            continue
        sides = {}
        for home, skaters in ((True, s["home_skaters"]), (False, s["away_skaters"])):
            tid = ids[gid][0 if home else 1]
            fwd = {p for p in skaters if pos.get((gid, p)) in ("C", "L", "R")}
            dmen = {p for p in skaters if pos.get((gid, p)) == "D"}
            sides[home] = (tid, label(tid, fwd, "L") if len(fwd) == 3 else None, label(tid, dmen, "P") if len(dmen) == 2 else None)
        for home in (True, False):
            tid, own_f, own_d = sides[home]
            _, opp_f, _ = sides[not home]
            if opp_f is None:
                continue
            for venue in ("all", "home" if home else "road"):
                played[(tid, venue)].add(gid)
                for own in (own_f, own_d):
                    if own:
                        acc[(tid, venue)][(own, opp_f)] += s["duration"]
    out = {}
    cols = ["L1", "L2", "L3", "L4"]
    for tid in usual_units:
        entry = {}
        for venue in ("all", "home", "road"):
            cells = acc.get((tid, venue), {})
            block = {"games": len(played.get((tid, venue), ())), "rows": []}
            for kind, rows in (("L", cols), ("P", ["P1", "P2", "P3"])):
                total = sum(v for (own, _), v in cells.items() if own[0] == kind)
                expected = [sum(v for (own, opp), v in cells.items() if own[0] == kind and opp == c) / total * 100 if total else 0.0 for c in cols]
                for r in rows:
                    row_sec = sum(cells.get((r, c), 0.0) for c in cols)
                    block["rows"].append({"label": r, "minutes": round(row_sec / 60, 1), "seconds": round(row_sec), "share": [round(100 * cells.get((r, c), 0.0) / row_sec) if row_sec else None for c in cols],
                                          "expected": [round(x) for x in expected]})
            entry[venue] = block
        out[tid] = entry
    return out


KILL_MIN_SEC = 30  # kills shorter than this (a quick goal, an offsetting call) say little about who starts them


def pk_roles(season: int) -> dict[int, dict]:
    """Penalty-kill roles over each team's recent window, instead of fixed units (PK groupings are loose: the most common
    exact foursome covers only about 11% of a team's shorthanded time). A kill is one unbroken shorthanded stretch.

    Starter: in at least 40% of the team's kills and on the ice when most of them begin (usually the defensive-zone draw).
    Second wave: in at least 40% of kills but usually comes on after the first change.
    Spot duty: in 10-40% of kills, at 20+ seconds a kill. "Takes the draw": wins or loses the opening faceoff in at least half the kills he starts.
    """
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ids = {g["game_id"]: (g["home_id"], g["away_id"]) for g in games.iter_rows(named=True)}
    stints = pl.read_parquet(d / "stints.parquet").filter(pl.col("game_id").is_in(list(ids))).sort("game_id", "start")
    have = set(stints["game_id"].unique().to_list())
    recent = {}
    for tid in set(games["home_id"]) | set(games["away_id"]):
        mine = games.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid)).sort("date", "game_id")["game_id"].to_list()
        recent[tid] = set([g for g in mine if g in have][-WINDOW_GAMES:])
    pos = {(r["game_id"], r["player_id"]): r["pos"] for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    draws = {}
    for e in pl.read_parquet(d / "events.parquet").filter(pl.col("type") == "faceoff").iter_rows(named=True):
        draws.setdefault((e["game_id"], e["sec"]), (e["p1"], e["p2"]))

    kills = []  # (team, game, start, end, [(seconds, skaters)])
    for (gid,), g in stints.group_by("game_id", maintain_order=True):
        for home in (True, False):
            tid = ids[gid][0 if home else 1]
            if gid not in recent[tid]:
                continue
            cur = None
            for s in g.iter_rows(named=True):
                own, opp = (s["n_home"], s["n_away"]) if home else (s["n_away"], s["n_home"])
                short = s["home_goalie"] is not None and s["away_goalie"] is not None and 3 <= own < opp
                on = s["home_skaters"] if home else s["away_skaters"]
                if short and cur is not None and s["start"] == cur[3]:
                    cur[4].append((s["duration"], on))
                    cur[3] = s["end"]
                elif short:
                    if cur:
                        kills.append(cur)
                    cur = [tid, gid, s["start"], s["end"], [(s["duration"], on)]]
                else:
                    if cur:
                        kills.append(cur)
                    cur = None
            if cur:
                kills.append(cur)

    out: dict[int, dict] = {}
    for tid, gid, start, end, parts in kills:
        if end - start < KILL_MIN_SEC:
            continue
        team = out.setdefault(tid, {"kills": 0, "players": defaultdict(lambda: {"kills": 0, "starts": 0, "sec": 0.0, "draws": 0, "pos": None})})
        team["kills"] += 1
        first = set(parts[0][1])
        opener = draws.get((gid, start))
        seen = set()
        for sec, on in parts:
            for p in on:
                rec = team["players"][p]
                rec["sec"] += sec
                rec["pos"] = rec["pos"] or pos.get((gid, p))
                if p not in seen:
                    seen.add(p)
                    rec["kills"] += 1
                    if p in first:
                        rec["starts"] += 1
                        if opener and p in opener:
                            rec["draws"] += 1
    result = {}
    for tid, team in out.items():
        n = team["kills"]
        rows = []
        for pid, r in team["players"].items():
            share = r["kills"] / n
            if share < 0.10 or r["sec"] / r["kills"] < 20:
                continue  # also skips players who only hop on as the penalty runs out
            start_rate = r["starts"] / r["kills"]
            role = ("starter" if start_rate >= 0.5 else "second") if share >= 0.4 else "spot"
            rows.append({"id": pid, "d": r["pos"] == "D", "kills_in": r["kills"], "starts": r["starts"], "sec": round(r["sec"]), "per_kill": round(r["sec"] / r["kills"]),
                         "role": role, "draw": r["starts"] >= 3 and r["draws"] >= 0.5 * r["starts"]})
        order = {"starter": 0, "second": 1, "spot": 2}
        rows.sort(key=lambda x: (order[x["role"]], x["d"], -x["sec"]))
        result[tid] = {"kills": n, "players": rows}
    return result
