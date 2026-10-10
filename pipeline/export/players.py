"""Player pages: one JSON file per player who has dressed this season, plus an index.

Skaters get their role (line, units, ice time), ratings against their position, NHL EDGE tracking numbers,
every unblocked shot for the map, and how their misses miss. Goalies get their role and their goalie ratings.
"""
import json
import math
from datetime import date, datetime, timezone

import numpy as np
import polars as pl

from pipeline.config import REGULAR, SITE_DATA, TABLES, season_id
from pipeline.ingest import edge, rosters
from pipeline.metrics import players as rating
from pipeline.metrics.players import POOL_GAMES

OUT = SITE_DATA / "players"
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
MISS_GROUPS = {"wide-left": "wide", "wide-right": "wide", "above-crossbar": "high", "high-and-wide-left": "high", "high-and-wide-right": "high",
               "hit-left-post": "post", "hit-right-post": "post", "hit-crossbar": "post", "short": "short"}


# Shot areas are rings and wedges around the middle of the net (x 89, y 0), so their edges follow the
# distance and angle that decide how dangerous a shot is. The site's map draws the same shapes from these
# numbers (site/src/components/AreaMap.astro); keep the two in step.
RINGS = (6, 22, 46, 60)  # crease, low slot and net sides, high slot and circles, the band below the points
WEDGES = (28, 15, 72)  # degrees off straight out: low slot, high slot and middle point, start of the corners


def area(x: float, y: float) -> str:
    """Rink area of a shot, seen from the shooter (positive y is his left)."""
    if x < 25:
        return "outside"
    side = "L" if y > 0 else "R"
    d = math.hypot(89 - x, y)
    a = math.degrees(math.atan2(abs(y), 89 - x))  # 0 straight out from the net, 90 along the goal line, 180 behind
    crease, inner, middle, outer = RINGS
    low, slot, corner = WEDGES
    if d <= inner:
        if a > 90:
            return "behind"
        if d <= crease:
            return "crease"
        return "lowSlot" if a <= low else f"netSide{side}"
    if a > corner:
        return f"corner{side}"
    if a <= slot:
        return "highSlot" if d <= middle else "point"
    if d <= middle:
        return f"circle{side}"
    return f"outer{side}" if d <= outer else f"point{side}"


AREAS = ["crease", "lowSlot", "netSideL", "netSideR", "highSlot", "circleL", "circleR", "behind", "cornerL", "cornerR", "outerL", "outerR", "point", "pointL", "pointR", "outside"]


def _seasons_frame(season: int, name: str) -> pl.DataFrame:
    return pl.concat([pl.read_parquet(TABLES / str(s) / f"{name}.parquet").with_columns(pl.lit(s).alias("season")) for s in (season - 1, season)], how="diagonal_relaxed")


def _abbr(season: int) -> dict[int, str]:
    out = {}
    for s in (season - 1, season):
        g = pl.read_parquet(TABLES / str(s) / "games.parquet")
        out |= {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
    return out


def _age(birth: str | None) -> int | None:
    if not birth:
        return None
    b, t = date.fromisoformat(birth), date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def _edge_values(season: int, pid: int, sec: float | None) -> dict | None:
    """The EDGE numbers we show, for one season. Rates use our own ice time."""
    e = edge.read(season, pid)
    if not e or "skatingSpeed" not in e:
        return None
    g = lambda *path: _dig(e, path)
    hours = (sec or 0) / 3600
    return {"topSpeed": g("skatingSpeed", "speedMax", "imperial"), "bursts": g("skatingSpeed", "burstsOver20", "value"),
            "shotSpeed": g("topShotSpeed", "imperial"), "distance": g("totalDistanceSkated", "imperial"),
            "oz": g("zoneTimeDetails", "offensiveZoneEvPctg"), "games": (e.get("player") or {}).get("gamesPlayed") or 0, "hours": hours}


def _dig(d, path):
    for k in path:
        if not isinstance(d, dict) or d.get(k) is None:
            return None
        d = d[k]
    return d


def _combine_edge(now: dict | None, last: dict | None, mode: str) -> dict | None:
    """One set of EDGE numbers for a mode: this season, or this season and last together."""
    parts = [p for p in ([now] if mode == "season" else [now, last]) if p]
    if not parts:
        return None
    hours = sum(p["hours"] for p in parts)
    games = sum(p["games"] for p in parts)
    top = lambda k: max((p[k] for p in parts if p[k] is not None), default=None)
    total = lambda k: sum(p[k] or 0 for p in parts)
    oz = [(p["oz"], p["games"]) for p in parts if p["oz"] is not None and p["games"]]
    return {"topSpeed": top("topSpeed"), "shotSpeed": top("shotSpeed"), "games": games,
            "bursts": total("bursts") / hours if hours else None,  # per 60 minutes of ice time
            "distance": total("distance") / hours if hours else None,
            "oz": 100 * sum(v * n for v, n in oz) / sum(n for _, n in oz) if oz else None}


# The NHL's portrait photos. A player no club lists today (sent down) gets the address his photo would have for this
# season's team; the site shows initials when no photo loads.
MUGS = "https://assets.nhle.com/mugs/nhl"

EDGE_HIGHER = {"topSpeed": True, "bursts": True, "shotSpeed": True, "distance": True, "oz": True}
EDGE_SPLIT = {"shotSpeed", "distance"}  # centres and wingers differ here (standardized gap 0.3+), so each is compared with his own


def run(season: int) -> int:
    r = rating.run(season)
    abbr = _abbr(season)
    pg = _seasons_frame(season, "player_game")
    games = _seasons_frame(season, "games").filter(pl.col("game_type") == REGULAR)
    pg = pg.join(games.select("game_id"), on="game_id")
    roster = pl.read_parquet(TABLES / str(season) / "players.parquet").join(games.select("game_id", "date"), on="game_id").sort("date", "game_id")
    latest = roster.unique("player_id", keep="last")
    lines = json.loads((SITE_DATA / "lines.json").read_text())["teams"]
    goalies = json.loads((SITE_DATA / "goalies.json").read_text())

    # Team games played this season, and each skater's ice time rank among teammates at his position.
    team_games = pg.filter(pl.col("season") == season).group_by("team_id").agg(pl.col("game_id").n_unique().alias("n"))
    team_games = dict(zip(team_games["team_id"], team_games["n"]))
    cur = pg.filter(pl.col("season") == season)
    per = cur.group_by("player_id", "team_id").agg(pl.len().alias("gp"), *[(pl.col(c).sum() / pl.len()).alias(k) for c, k in (("sec", "toi"), ("sec5", "toi5"), ("sec_pp", "toi_pp"), ("sec_pk", "toi_pk"))],
                                                pl.col("pos").mode().first())

    # Every unblocked shot (not at an empty net) for the map, and how his misses missed.
    ev = _seasons_frame(season, "events").join(games.select("game_id"), on="game_id")
    xg = pl.concat([pl.read_parquet(TABLES / str(s) / "shots_xg_own.parquet") for s in (season - 1, season)])
    shots = ev.filter(pl.col("type").is_in(UNBLOCKED) & pl.col("p1").is_not_null() & pl.col("x_norm").is_not_null()).join(xg, on=["game_id", "event_id"], how="left")
    opp_goalie = pl.when(pl.col("is_home")).then(pl.col("away_goalie")).otherwise(pl.col("home_goalie"))
    shots = shots.filter(opp_goalie.is_not_null()).with_columns(pl.col("xg").fill_null(0.0))
    shots = shots.with_columns(area=pl.struct("x_norm", "y_norm").map_elements(lambda s: area(s["x_norm"], s["y_norm"]), return_dtype=pl.String))
    group_of = {pid: rec["group"] for pid, rec in r["players"].items()}

    # Position averages: share of unblocked shots from each area, among regulars, per mode.
    area_avg = {}
    for mode in ("blend", "season"):
        sub = shots if mode == "blend" else shots.filter(pl.col("season") == season)
        counts = sub.group_by("p1", "area").len()
        tot = counts.group_by("p1").agg(pl.col("len").sum().alias("tot"))
        area_avg[mode] = {}
        for g in ("F", "D"):
            members = [p for p, gg in group_of.items() if gg == g]
            c = counts.filter(pl.col("p1").is_in(members)).group_by("area").agg(pl.col("len").sum())
            n = c["len"].sum()
            area_avg[mode][g] = {row["area"]: round(100 * row["len"] / n, 1) for row in c.iter_rows(named=True)}

    misses = ev.filter((pl.col("type") == "missed-shot") & pl.col("p1").is_not_null()).with_columns(
        kind=pl.col("reason").replace_strict(MISS_GROUPS, default="other"), side=pl.col("reason").str.extract(r"(left|right)"))
    miss_by = {}
    for (pid, s), part in misses.group_by("p1", "season"):
        miss_by.setdefault(pid, {})[s] = {
            "n": part.height, **{k: int((part["kind"] == k).sum()) for k in ("wide", "high", "post", "short", "other")},
            "left": int(((part["kind"] == "wide") & (part["side"] == "left")).sum()), "right": int(((part["kind"] == "wide") & (part["side"] == "right")).sum()),
            "crossbar": int((part["reason"] == "hit-crossbar").sum())}

    # Counting stats per season.
    counting = pg.group_by("player_id", "season").agg(pl.len().alias("gp"), pl.col("g").sum(), (pl.col("a1") + pl.col("a2")).sum().alias("a"),
                                                       pl.col("sog").sum(), pl.col("sec").sum(), pl.col("pen_taken").sum())
    count_by = {(r_["player_id"], r_["season"]): r_ for r_ in counting.iter_rows(named=True)}

    # EDGE: values per player and mode, then percentiles among regulars at the same position.
    edge_vals = {}
    for pid, rec in r["players"].items():
        sec_now = rec["totals"]["season"]["sec"]
        sec_last = rec["totals"]["last"]["sec"] if rec["totals"]["last"] else None
        now, last = _edge_values(season, pid, sec_now), _edge_values(season - 1, pid, sec_last)
        edge_vals[pid] = {mode: _combine_edge(now, last, mode) for mode in ("blend", "season")}
    edge_pct = {}
    for mode in ("blend", "season"):
        for k, higher in EDGE_HIGHER.items():
            peers: dict[str, list[int]] = {}
            for pid, rec in r["players"].items():
                g = rec["sub"] if rec["group"] == "F" and k in EDGE_SPLIT else rec["group"]
                peers.setdefault(g, []).append(pid)
            for g, members in peers.items():
                regular = {pid for pid in members if r["players"][pid]["games"] + (r["players"][pid]["games_last"] if mode == "blend" else 0) >= POOL_GAMES[mode]}
                pool = np.array([edge_vals[p][mode][k] for p in regular if edge_vals[p][mode] and edge_vals[p][mode][k] is not None])
                for pid in members:
                    v = edge_vals[pid][mode][k] if edge_vals[pid][mode] else None
                    if v is None or not len(pool):
                        continue
                    edge_pct.setdefault(pid, {}).setdefault(mode, {})[k] = {"v": round(v, 2), "pct": round(rating._pct(v, pool, higher)), "ok": pid in regular,
                                                                         "of": int(len(pool)), "avg": round(float(pool.mean()), 2), "vs": g,
                                                                         "rank": int((pool > v).sum() + 1 if higher else (pool < v).sum() + 1)}

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    index = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    photos = rosters.headshots(season)
    goalie_rec = {g["id"]: (abbr_, g) for abbr_, gs in goalies["teams"].items() for g in gs}
    for row in latest.iter_rows(named=True):
        pid, tid = row["player_id"], row["team_id"]
        team = abbr.get(tid)
        e_now = edge.read(season, pid) or edge.read(season - 1, pid) or {}
        bio = e_now.get("player") or {}
        base = {"id": pid, "first": row["first"], "last": row["last"], "name": f'{row["first"]} {row["last"]}', "number": row["number"], "pos": row["pos"],
                "team": team, "shoots": bio.get("shootsCatches"), "age": _age(bio.get("birthDate")), "generated_at": now_iso, "season": season,
                "photo": photos.get(pid) or (f"{MUGS}/{season_id(season)}/{team}/{pid}.png" if team else None)}
        stats = {}
        for s, key in ((season, "season"), (season - 1, "last")):
            c = count_by.get((pid, s))
            if c:
                stats[key] = {"gp": c["gp"], "g": int(c["g"]), "a": int(c["a"]), "p": int(c["g"] + c["a"]), "sog": int(c["sog"]), "toi": round(c["sec"] / c["gp"]), "pim_pens": int(c["pen_taken"])}
        base["stats"] = stats
        if row["pos"] == "G":
            team_g, g = goalie_rec.get(pid, (team, None))
            if g is None:
                continue
            gp_team = team_games.get(tid, 0)
            base |= {"group": "G", "goalie": g, "team_games": gp_team, "goalie_min": goalies["min_starts"]}
            base["shots_against"] = _goalie_areas(ev, xg, pid, season)
        else:
            rec = r["players"].get(pid)
            if rec is None:
                continue
            base |= {"group": rec["group"], "sub": rec["sub"], "traits": rec["traits"], "competition": rec.get("competition"), "moved_from": abbr.get(rec["moved_from"]) if rec.get("moved_from") else None, "games": rec["games"], "games_last": rec["games_last"],
                     "role": _role(pid, row, lines.get(team) or {}, per, team_games.get(tid, 0)),
                     "edge": edge_pct.get(pid), "edge_raw": {m: edge_vals[pid][m] for m in ("blend", "season")}}
            mine = shots.filter(pl.col("p1") == pid)
            base["shots"] = [[int(s_["x_norm"]), int(s_["y_norm"]), round(1000 * s_["xg"]), int(s_["type"] == "goal"), int(s_["season"] == season)]
                             for s_ in mine.select("x_norm", "y_norm", "xg", "type", "season").iter_rows(named=True)]
            areas = {}
            for mode in ("blend", "season"):
                sub = mine if mode == "blend" else mine.filter(pl.col("season") == season)
                n = sub.height
                c = sub.group_by("area").agg(pl.len().alias("shots"), (pl.col("type") == "goal").sum().alias("goals"))
                have = {x["area"]: x for x in c.iter_rows(named=True)}
                areas[mode] = {"n": n, "areas": {a: {"shots": int(have[a]["shots"]) if a in have else 0, "goals": int(have[a]["goals"]) if a in have else 0,
                                                     "share": round(100 * have[a]["shots"] / n, 1) if a in have and n else 0.0,
                                                     "avg": area_avg[mode][rec["group"]].get(a, 0.0)} for a in AREAS}}
            base["areas"] = areas
            mb = miss_by.get(pid, {})
            base["misses"] = {"season": mb.get(season), "blend": _add(mb.get(season), mb.get(season - 1))}
        (OUT / f"{pid}.json").write_text(json.dumps(base, separators=(",", ":"), default=_num))
        index.append({"id": pid, "name": base["name"], "last": row["last"], "team": team, "pos": row["pos"], "number": row["number"], "photo": base["photo"]})
    (OUT / "index.json").write_text(json.dumps(sorted(index, key=lambda x: (x["team"] or "", x["last"])), separators=(",", ":")))
    return len(index)


def _num(v):
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    raise TypeError(type(v))


def _add(a: dict | None, b: dict | None) -> dict | None:
    if not a or not b:
        return a or b
    return {k: a[k] + b[k] for k in a}


def _role(pid: int, row: dict, ln: dict, per: pl.DataFrame, team_games: int) -> dict:
    """Where he fits on his team right now: line or pair, special-teams units, ice time and its rank."""
    out = {"team_games": team_games}
    unit = next((u for u in ln.get("units", []) if pid in u["ids"]), None)
    if unit:
        mates = [{"id": i, "name": n} for i, n in zip(unit["ids"], unit["players"]) if i != pid]
        out["line"] = {"label": unit["label"], "mates": mates, "toi_sec": unit["toi_sec"], "share": unit["share"], "pct": unit["pct"].get("share")}
    sp = ln.get("special") or {}
    kr = sp.get("pk_roles") or {"kills": 0, "players": []}
    me = next((r for r in kr["players"] if r["id"] == pid), None)
    out["pk"] = {"kills": kr["kills"], **({k: me[k] for k in ("role", "kills_in", "starts", "per_kill", "draw")} if me else {"role": None})} if kr["kills"] else None
    for kind in ("pp",):
        for u in sp.get(kind, []):
            me = next((p for p in u["players"] if p["id"] == pid), None)
            if me:
                out[kind] = {"label": u["label"], "role": me.get("role"), "both": me.get("both"), "share": u.get("share"),
                             "mates": [{"id": p["id"], "name": p["name"]} for p in u["players"] if p["id"] != pid]}
                break
    fo = sp.get("faceoff")
    short = f'{row["first"][0]}. {row["last"]}'
    if fo and fo["player"] == short:
        out["faceoff"] = fo["text"]
    out["notes"] = [n["text"] for n in ln.get("notes", []) if n.get("player") == short]
    team = per.filter(pl.col("team_id") == row["team_id"])
    me = team.filter(pl.col("player_id") == pid)
    if me.height:
        is_d = row["pos"] == "D"
        mates = team.filter((pl.col("pos") == "D") == is_d).filter(pl.col("gp") >= max(1, team_games // 2))
        out["toi_rank"] = int((mates["toi"] > me["toi"][0]).sum()) + 1
        out["toi_of"] = mates.height
        out["gp"] = int(me["gp"][0])
        out["toi"] = {k: round(me[k][0]) for k in ("toi", "toi5", "toi_pp", "toi_pk")}
    return out


def _goalie_areas(ev: pl.DataFrame, xg: pl.DataFrame, pid: int, season: int) -> dict:
    """Shots he faced (unblocked, on goal or wide), by area, with saves, for both modes."""
    faced = ev.filter(pl.col("type").is_in(["shot-on-goal", "goal"]) & (pl.col("goalie_id") == pid) & pl.col("x_norm").is_not_null()).join(xg, on=["game_id", "event_id"], how="left")
    out = {}
    for mode in ("blend", "season"):
        sub = faced if mode == "blend" else faced.filter(pl.col("season") == season)
        acc = {}
        for s in sub.iter_rows(named=True):
            a = area(s["x_norm"], s["y_norm"])
            c = acc.setdefault(a, {"shots": 0, "goals": 0, "xg": 0.0})
            c["shots"] += 1
            c["goals"] += s["type"] == "goal"
            c["xg"] += s["xg"] or 0.0
        out[mode] = {a: {**v, "xg": round(v["xg"], 2)} for a, v in acc.items()}
    return out


if __name__ == "__main__":
    from pipeline.config import CURRENT_SEASON

    print(run(CURRENT_SEASON))
