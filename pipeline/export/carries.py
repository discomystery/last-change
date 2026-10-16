"""What a skater carries for his team, for the "What he carries" section of player pages.

Shares of the team's goals, goals he is in on, power-play goals he is in on and shots on goal, each counted over
the games he played (this season and last for "blend", this season only for "season"), with his rank among the
league's regulars and the same share for each teammate on his current club. Who is next in line for each thing he
does (his line spot, the power play, ice time, the penalty kill), from this season's ice time; for a player who has not
dressed this season (`idle`), his own load comes from last season and the teammates are who carry it now. Goals he is in on per
game against typical forwards or defensemen by ice-time rank on their team. And the team's results in games he
missed while on the club.

Plain counts only: the hidden relevancy score (metrics/relevancy.py) is never used or written here.
"""
import polars as pl

from pipeline.config import REGULAR, TABLES
from pipeline.export import wording
from pipeline.metrics.players import POOL_GAMES

PP = ["5v4", "5v3", "4v3"]
PK = ["4v5", "3v5", "3v4"]
PP_MIN_SEC = 60  # a game, to count as a power-play regular for the power-play share list
PP_TEAM_GOALS = {"blend": 10, "season": 2}  # team power-play goals in his games before a share means anything
RELIED_PP, RELIED_PK = 90, 60  # seconds a game: the report cards' "relied on" bars
# Ice-time ranks on a team that make "typical" forwards and defensemen, for the fill-in comparison.
TIERS = {"F": [(1, 3, "First line"), (4, 6, "Second line"), (10, 13, "Typical fill-in")],
         "D": [(1, 2, "Top pair"), (3, 4, "Second pair"), (6, 7, "Typical fill-in")]}
POS_WORD = {"L": "left wing", "R": "right wing", "C": "center", "D": "defenseman"}


def _frames(season: int):
    seasons = [s for s in (season - 1, season) if (TABLES / str(s) / "player_game.parquet").exists()]
    games = pl.concat([pl.read_parquet(TABLES / str(s) / "games.parquet") for s in seasons], how="diagonal_relaxed").filter(pl.col("game_type") == REGULAR)
    pg = pl.concat([pl.read_parquet(TABLES / str(s) / "player_game.parquet").with_columns(season=pl.lit(s)) for s in seasons], how="diagonal_relaxed")
    pg = pg.join(games.select("game_id", "date"), on="game_id").filter(pl.col("pos") != "G")
    tg = pl.concat([pl.read_parquet(TABLES / str(s) / "team_game.parquet") for s in seasons], how="diagonal_relaxed").join(games.select("game_id"), on="game_id")
    team = tg.group_by("game_id", "team_id").agg(
        pl.col("gf").sum().alias("tgf"), pl.col("ga").sum().alias("tga"), pl.col("sf").sum().alias("tsf"),
        pl.col("gf").filter(pl.col("strength").is_in(PP)).sum().alias("tppg"), pl.col("sec").filter(pl.col("strength").is_in(PP)).sum().alias("tpp_sec"),
        pl.col("sec").filter(pl.col("strength").is_in(PK)).sum().alias("tpk_sec"),
        (pl.col("xgf").filter(pl.col("strength") != "EN").sum() - pl.col("xga").filter(pl.col("strength") != "EA").sum()).alias("txgd"))
    res = pl.concat([games.select("game_id", "date", "season", pl.col("home_id").alias("team_id"), (pl.col("home_score") > pl.col("away_score")).alias("won")),
                     games.select("game_id", "date", "season", pl.col("away_id").alias("team_id"), (pl.col("away_score") > pl.col("home_score")).alias("won"))])
    team = team.join(res, on=["game_id", "team_id"])
    return pg.join(team.drop("date", "season"), on=["game_id", "team_id"]), team


def _shares(pg: pl.DataFrame) -> pl.DataFrame:
    return pg.group_by("player_id").agg(
        pl.len().alias("gp"), (pl.col("g") + pl.col("a1") + pl.col("a2")).sum().alias("p"), pl.col("g").sum(), pl.col("sog").sum(),
        (pl.col("g_pp") + pl.col("a1_pp")).sum().alias("ppi"), pl.col("sec_pp").sum().alias("pp_sec"),
        pl.col("tgf").sum(), pl.col("tsf").sum(), pl.col("tppg").sum(), pl.col("pos").last())


STATS = [("inOn", "p", "tgf"), ("goals", "g", "tgf"), ("pp", "ppi", "tppg"), ("shots", "sog", "tsf")]


def run(season: int, latest: pl.DataFrame, lines: dict, abbr: dict[int, str], idle: pl.DataFrame | None = None) -> dict[int, dict]:
    """Per skater id: the "carries" block for his player page. `idle`: players a club holds who have not dressed this
    season (players.py `_idle`), shaped like `latest`; they get the block from last season's games."""
    pg, team = _frames(season)
    if idle is not None and idle.height:
        keep = ["player_id", "team_id", "first", "last"]
        latest = pl.concat([latest.select(keep), idle.filter(pl.col("pos") != "G").select(keep)], how="vertical_relaxed")
    name = {r["player_id"]: f'{r["first"][0]}. {r["last"]}' for r in latest.iter_rows(named=True)}
    name |= wording.club_short_names(season)  # teammates by last name, "M. Tkachuk" for namesakes
    club = dict(zip(latest["player_id"], latest["team_id"]))
    by_mode = {"blend": _shares(pg), "season": _shares(pg.filter(pl.col("season") == season))}
    pools = {}
    for mode, d in by_mode.items():
        reg = d.filter(pl.col("gp") >= POOL_GAMES[mode])
        pools[mode] = {k: sorted(reg.filter(pl.col(den) > 0).select((100 * pl.col(num) / pl.col(den)).alias("v"))["v"].to_list())
                       for k, num, den in STATS if k != "pp"}
        ppreg = reg.filter((pl.col("pp_sec") / pl.col("gp") >= PP_MIN_SEC) & (pl.col("tppg") >= PP_TEAM_GOALS[mode]))
        pools[mode]["pp"] = sorted((100 * ppreg["ppi"] / ppreg["tppg"]).to_list())
    rows = {mode: {r["player_id"]: r for r in d.iter_rows(named=True)} for mode, d in by_mode.items()}
    typical = _typical(season)
    now = pg.filter(pl.col("season") == season)
    per = now.group_by("player_id", "team_id").agg(pl.len().alias("gp"), (pl.col("sec").sum() / pl.len()).alias("toi"),
                                                   (pl.col("sec_pp").sum() / pl.len()).alias("pp"), (pl.col("sec_pk").sum() / pl.len()).alias("pk"),
                                                   pl.col("pos").mode().first())
    team_pp = now.select("game_id", "team_id", "tpp_sec").unique().group_by("team_id").agg(pl.col("tpp_sec").sum())
    team_pp = dict(zip(team_pp["team_id"], team_pp["tpp_sec"]))
    units = {}  # game_units for both seasons, read only if an idle player needs them
    f = TABLES / str(season) / "game_units.parquet"
    units_now = pl.read_parquet(f) if f.exists() else None
    recent_by = {}

    out = {}
    for pid, tid in club.items():
        if pid not in rows["blend"]:
            continue
        mates = [p for p, t in club.items() if t == tid]
        tiles = {}
        for mode in ("blend", "season"):
            me = rows[mode].get(pid)
            if not me:
                continue
            tiles[mode] = []
            for k, num, den in STATS:
                if not me[den]:
                    tiles[mode].append({"key": k, "num": int(me[num]), "den": 0})
                    continue
                v = 100 * me[num] / me[den]
                pool = pools[mode][k]
                team_vals = []
                for m in mates:
                    r = rows[mode].get(m)
                    if not r or not r[den] or (r["gp"] < POOL_GAMES[mode] and m != pid):
                        continue
                    if k == "pp" and m != pid and r["pp_sec"] / r["gp"] < PP_MIN_SEC:
                        continue
                    team_vals.append([name.get(m, "?"), round(100 * r[num] / r[den], 1), int(m == pid)])
                tiles[mode].append({"key": k, "num": int(me[num]), "den": int(me[den]), "v": round(v, 1),
                                    "rank": sum(1 for x in pool if x > v + 1e-9) + 1, "of": len(pool),
                                    "mates": sorted(team_vals, key=lambda x: -x[1])})
                if k == "pp" and me["pp_sec"] / me["gp"] < PP_MIN_SEC:
                    tiles[mode][-1]["rank"] = None  # not a power-play regular: no rank against those who are
        grp = "D" if (rows["blend"][pid]["pos"] == "D") else "F"
        b = rows["blend"][pid]
        fill = {"v": round(b["p"] / b["gp"], 2), "ticks": typical[grp],
                "mates": sorted([[name.get(m, "?"), round(rows["blend"][m]["p"] / rows["blend"][m]["gp"], 2)] for m in mates
                                 if m != pid and m in rows["blend"] and rows["blend"][m]["gp"] >= POOL_GAMES["blend"]
                                 and (rows["blend"][m]["pos"] == "D") == (grp == "D")], key=lambda x: -x[1])}
        if per.filter((pl.col("player_id") == pid) & (pl.col("team_id") == tid)).is_empty():
            if not units:
                units = {s: pl.read_parquet(TABLES / str(s) / "game_units.parquet") for s in (season - 1, season) if (TABLES / str(s) / "game_units.parquet").exists()}
            nxt = _next_idle(pid, tid, pg, season, units, name)
        else:
            if tid not in recent_by:
                ids = now.filter(pl.col("team_id") == tid).select("game_id", "date").unique().sort("date")["game_id"].tail(RECENT).to_list()
                recent_by[tid] = (now.filter((pl.col("team_id") == tid) & pl.col("game_id").is_in(ids)),
                                  units_now.filter((pl.col("team_id") == tid) & pl.col("game_id").is_in(ids)) if units_now is not None else None)
            nxt = _next(pid, tid, per, team_pp.get(tid, 0), lines.get(abbr.get(tid)) or {}, name, *recent_by[tid])
        out[pid] = {"tiles": tiles, "next": nxt,
                    "fill": fill, "out": _missed(pid, tid, pg, team, season)}
    return out


def _typical(season: int) -> dict:
    """Goals in on per game by ice-time rank on the team, last full season (this one if it is the only one)."""
    s = season - 1 if (TABLES / str(season - 1) / "player_game.parquet").exists() else season
    g = pl.read_parquet(TABLES / str(s) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
    pg = pl.read_parquet(TABLES / str(s) / "player_game.parquet").join(g, on="game_id").filter(pl.col("pos") != "G")
    d = pg.group_by("player_id", "team_id").agg(pl.len().alias("gp"), pl.col("sec").mean(), (pl.col("g") + pl.col("a1") + pl.col("a2")).sum().alias("p"),
                                                (pl.col("pos").last() == "D").alias("d")).filter(pl.col("gp") >= 20)
    d = d.with_columns(r=pl.col("sec").rank("ordinal", descending=True).over("team_id", "d"))
    out = {}
    for grp, tiers in TIERS.items():
        x = d.filter(pl.col("d") == (grp == "D"))
        out[grp] = [[round(float(t["p"].sum() / t["gp"].sum()), 2), label] for lo, hi, label in tiers
                    for t in [x.filter((pl.col("r") >= lo) & (pl.col("r") <= hi))]]
    return out


# Tested on 2,445 stretches a top-nine forward or top-four defenseman missed in 2023-26 (who actually took the job
# while he was out): the usual stand-in on his line named the right player 34% of the time (the next most-used
# player at his position 10%); the part-timer at his position who has dressed most lately was in the lineup 64% of the
# time; nothing named the main gainer of his ice time better than chance (his minutes get shared out); for the power
# play and penalty kill the next most-used player was right 10-17%, so those names are "most likely", not sure things.
RECENT = 10  # team games for the stand-in and the part-timers
STAND_IN_SEC = 120  # seconds with his linemates over those games before a stand-in counts (under it: 7% right, below the 10% fallback)


def _next(pid: int, tid: int, per: pl.DataFrame, team_pp_sec: float, ln: dict, name: dict, recent: pl.DataFrame | None = None,
          units: pl.DataFrame | None = None) -> dict:
    """Who would most likely take each thing he does if he were out. `recent`: the team's player-games over its last
    RECENT games; `units`: those games' 5-on-5 groups (game_units)."""
    t = per.filter((pl.col("team_id") == tid) & (pl.col("gp") >= 2))
    me = per.filter((pl.col("team_id") == tid) & (pl.col("player_id") == pid))
    if me.is_empty():
        return {}
    me = me.row(0, named=True)
    is_d = me["pos"] == "D"
    others = t.filter((pl.col("player_id") != pid) & ((pl.col("pos") == "D") == is_d))
    out = {}

    def top(df: pl.DataFrame, col: str):
        df = df.sort(col, descending=True)
        return {"id": df["player_id"][0], "name": name.get(df["player_id"][0], "?"), "sec": round(df[col][0])} if df.height else None

    unit = next((u for u in ln.get("units", []) if pid in u["ids"]), None)
    if unit:
        # The usual stand-in: whoever has played with his linemates without him over the team's last games.
        partners = {m for m in unit["ids"] if m != pid}
        sec, games = {}, {}
        if units is not None and partners:
            for r in units.filter(pl.col("kind") == ("D" if is_d else "F")).iter_rows(named=True):
                members = {int(x) for x in r["unit"].split("-")}
                if partners <= members and pid not in members:
                    for o in members - partners:
                        sec[o] = sec.get(o, 0) + r["sec"]
                        games.setdefault(o, set()).add(r["game_id"])
        sub = max(sec, key=sec.get) if sec else None
        if sub and sec[sub] >= STAND_IN_SEC:
            out["line"] = {"pos": POS_WORD.get(me["pos"], "player"), "stand_in": True,
                           "next": {"id": sub, "name": name.get(sub, "?"), "sec": round(sec[sub] / len(games[sub])), "games": len(games[sub]),
                                    "of": recent["game_id"].n_unique() if recent is not None else RECENT}}
        else:  # no real stand-in yet: the next most-used player at his position
            same = others.filter(pl.col("pos") == me["pos"]) if not is_d else others
            out["line"] = {"pos": POS_WORD.get(me["pos"], "player"), "next": top(same.filter(~pl.col("player_id").is_in(unit["ids"])), "toi")}
    if recent is not None and recent.height:
        # His place in the lineup: the part-timer at his position who has dressed most over the team's last games.
        n = recent["game_id"].n_unique()
        part = recent.filter((pl.col("pos") == "D") == is_d).group_by("player_id").agg(pl.len().alias("gp"), pl.col("date").max()).filter(
            (pl.col("player_id") != pid) & (pl.col("gp") <= n / 2)).sort("gp", "date", descending=True)
        if part.height:
            r = part.row(0, named=True)
            out["lineup"] = {"id": r["player_id"], "name": name.get(r["player_id"], "?"), "gp": r["gp"], "of": n}
    if me["pp"] >= RELIED_PP:
        mine = {p["id"] for u in (ln.get("special") or {}).get("pp", []) if any(x["id"] == pid for x in u["players"]) for p in u["players"]}
        out["pp"] = {"sec": round(me["pp"]), "share": round(100 * me["pp"] * me["gp"] / team_pp_sec) if team_pp_sec else None,
                     "next": top(t.filter((pl.col("player_id") != pid) & ~pl.col("player_id").is_in(list(mine))), "pp")}
    out["toi"] = {"sec": round(me["toi"]), "rank": int((others["toi"] > me["toi"]).sum()) + 1, "next": None, "spread": True}
    if me["pk"] >= RELIED_PK:
        out["pk"] = {"sec": round(me["pk"]), "next": top(others.filter(pl.col("pk") <= me["pk"]), "pk")}
    else:
        out["pk"] = {"sec": round(me["pk"]), "next": None}
    return out


# Who has picked up an absent player's jobs: the clearest gain on last season, never just whoever plays most like him.
GAIN_TOI, GAIN_SPECIAL, GAIN_PP_SHARE = 45, 20, 5  # seconds a game of ice time / of penalty-kill time, points of power-play share
NEW_TO = 20  # seconds a game last season under which a job is new to him
PART_TIME, DRESSING = 0.75, 0.3  # last season's share of club games under which a player was a part-timer; this season's share that counts as dressing


def _next_idle(pid: int, tid: int, pg: pl.DataFrame, season: int, units: dict, name: dict) -> dict:
    """For a player who has not dressed this season: what he did per game last season (for this club if he played for
    it, else wherever he played most) and, for each job, the teammate whose load grew most over last season with this
    club: who now plays beside his usual linemates, who came into the lineup, who gained power-play share, penalty-kill
    time and ice time. A job nobody clearly took over says so (`next` None)."""
    from pipeline.metrics.patterns import _mates, _with

    last = pg.filter(pl.col("season") == season - 1)
    mine = last.filter(pl.col("player_id") == pid)
    if mine.is_empty():
        return {}
    here = mine.filter(pl.col("team_id") == tid)
    my_team = tid if here.height else mine.group_by("team_id").len().sort("len", descending=True)["team_id"][0]
    me_rows = mine.filter(pl.col("team_id") == my_team)
    is_d = me_rows["pos"].mode()[0] == "D"
    grp = pl.col("pos") == "D" if is_d else pl.col("pos") != "D"

    def load(d: pl.DataFrame) -> pl.DataFrame:  # per player and club
        team_games = d.select("game_id", "team_id", "tpp_sec", "tpk_sec").unique().group_by("team_id").agg(
            pl.col("game_id").n_unique().alias("n"), pl.col("tpp_sec").sum().alias("pp_team"))
        return d.group_by("player_id", "team_id").agg(
            pl.len().alias("gp"), (pl.col("sec").sum() / pl.len()).alias("toi"), (pl.col("sec_pp").sum() / pl.len()).alias("pp"),
            pl.col("sec_pp").sum().alias("pp_tot"), (pl.col("sec_pk").sum() / pl.len()).alias("pk"), pl.col("pos").mode().first()).join(team_games, on="team_id").with_columns(
            share=pl.col("gp") / pl.col("n"), pp_share=pl.when(pl.col("pp_team") > 0).then(100 * pl.col("pp_tot") / pl.col("pp_team")).otherwise(0.0))

    then_all = load(last)
    me = then_all.filter((pl.col("player_id") == pid) & (pl.col("team_id") == my_team)).row(0, named=True)
    now_t = pg.filter((pl.col("season") == season) & (pl.col("team_id") == tid))
    then_t = last.filter(pl.col("team_id") == tid)
    if now_t.is_empty():
        return {}
    # Last season's loads: each player's own (at the club he played most for), so a newcomer is measured against what
    # he did elsewhere, never against zero; and how often he dressed for this club.
    own = then_all.sort("gp", descending=True).unique("player_id", keep="first").select("player_id", *[pl.col(c).alias(f"{c}_was") for c in ("toi", "pp", "pp_share", "pk")])
    # Share of the club's games he dressed for from his first game with it (a deadline arrival is not a scratch).
    club_days = then_t.select("game_id", "date").unique()["date"]
    held = then_t.group_by("player_id").agg(pl.col("game_id").n_unique().alias("gp"), pl.col("date").min().alias("first"))
    held = held.with_columns(share_was=pl.col("gp") / pl.col("first").map_elements(lambda d: int((club_days >= d).sum()), return_dtype=pl.Int64)).select("player_id", "share_was")
    cmp = load(now_t).filter((pl.col("player_id") != pid) & (pl.col("gp") >= 2)).join(own, on="player_id", how="left").join(held, on="player_id", how="left")
    cmp = cmp.with_columns(pl.col("^.*_was$").fill_null(0.0), with_club=pl.col("player_id").is_in(held["player_id"].to_list()))
    mates = cmp.filter(grp)

    def who(r: dict, col: str, **extra) -> dict:
        return {"id": r["player_id"], "name": name.get(r["player_id"], "?"), "sec": round(r[col]), "was": round(r[f"{col}_was"]), **extra}

    def gainer(df: pl.DataFrame, col: str, bar: float) -> dict | None:
        df = df.with_columns(gain=pl.col(col) - pl.col(f"{col}_was")).filter(pl.col("gain") >= bar).sort("gain", descending=True)
        return df.row(0, named=True) if df.height else None

    out = {"last_season": True, "club": bool(here.height)}
    # His spot on the line: who plays beside the linemates he had most last season, and where that player used to be.
    if here.height and season - 1 in units and season in units:
        kind = "D" if is_d else "F"
        usual = _mates(units[season - 1], tid, then_t["game_id"].unique().to_list(), pid, kind)
        if usual:
            partners, sec_together, _ = usual
            cover = [c for c in _with(units[season], tid, now_t["game_id"].unique().to_list(), partners, kind) if c[0] != pid]
            nxt = None
            if cover and cover[0][2] >= 2:
                sub, sec, n = cover[0]
                n_then = then_t["game_id"].n_unique()  # ice-time order among last season's regulars at his position
                was = then_t.filter(grp).group_by("player_id").agg(pl.len().alias("gp"), pl.col("sec").mean()).filter(pl.col("gp") >= 0.4 * n_then).sort("sec", descending=True)["player_id"].to_list()
                nxt = {"id": sub, "name": name.get(sub, "?"), "sec": round(sec), "games": n,
                       "was_rank": was.index(sub) + 1 if sub in was else None}
            out["line"] = {"partners": [name.get(m, "?") for m in partners], "sec": round(sec_together), "next": nxt}
    # Into the lineup: the club's part-timers from last season (dressed for under three in four of its games once they
    # joined) who are dressing now. A newcomer's games say nothing about who took his place, so they are left out.
    came = mates.filter(pl.col("with_club") & (pl.col("share_was") < PART_TIME) & (pl.col("share") >= DRESSING)).sort("gp", "share_was", descending=[True, False])
    out["lineup"] = [{"id": r["player_id"], "name": name.get(r["player_id"], "?"), "gp": r["gp"], "of": now_t["game_id"].n_unique(),
                      "was": round(100 * r["share_was"])} for r in came.head(2).iter_rows(named=True)]
    if me["pp"] >= RELIED_PP:
        g = gainer(cmp, "pp_share", GAIN_PP_SHARE)
        out["pp"] = {"sec": round(me["pp"]), "share": round(me["pp_share"]),
                     "next": g and {**who(g, "pp_share"), "pp": round(g["pp"]), "new": g["pp_was"] < NEW_TO}}
    g = gainer(mates, "toi", GAIN_TOI)
    out["toi"] = {"sec": round(me["toi"]), "rank": int((mates["toi"] > me["toi"]).sum()) + 1, "next": g and who(g, "toi")}
    if me["pk"] >= RELIED_PK:
        g = gainer(mates, "pk", GAIN_SPECIAL)
        out["pk"] = {"sec": round(me["pk"]), "next": g and {**who(g, "pk"), "new": g["pk_was"] < NEW_TO}}
    else:
        out["pk"] = {"sec": round(me["pk"]), "next": None}
    return out


def _missed(pid: int, tid: int, pg: pl.DataFrame, team: pl.DataFrame, season: int) -> dict | None:
    """Team results in games he missed while with the club (this season and last, from his first game for them)."""
    mine = pg.filter((pl.col("player_id") == pid) & (pl.col("team_id") == tid))
    if mine.is_empty():
        return None
    t = team.filter((pl.col("team_id") == tid) & (pl.col("date") >= mine["date"].min()))
    played = set(mine["game_id"].to_list())
    w, wo = t.filter(pl.col("game_id").is_in(list(played))), t.filter(~pl.col("game_id").is_in(list(played)))
    if wo.is_empty():
        return {"missed": 0, "with": {"gp": w.height}}

    def line(d: pl.DataFrame) -> dict:
        return {"gp": d.height, "won": int(d["won"].sum()), "gf": round(float(d["tgf"].mean()), 1), "xgd": round(float(d["txgd"].mean()), 2)}
    dates = sorted(wo["date"].to_list())
    return {"missed": wo.height, "first": dates[0], "last": dates[-1], "this_season": int((wo["season"] == season).sum()),
            "with": line(w), "without": line(wo)}
