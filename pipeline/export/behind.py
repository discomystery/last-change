"""What's behind each team's record (Sora, 2026-10-27, thread on early-season storylines; prototype doc "What's behind
each team's record").

Every goal a team scores or allows this season is split into five parts that add up to its goal difference exactly:
the chances at 5-on-5, the chances on special teams (both as expected goals for minus against), shooting (goals minus
expected goals, empty nets out), goaltending (expected goals against minus goals against, empty nets out) and
empty-net goals. Rule-based checks then read those parts and write behind.json for team pages, the Standings page and
one preview item:

1. Record against play. What a team's chances are worth in points: points percentage fitted on the chance difference a
   game over FULL_SEASONS (about .555 + .156 per expected goal a game). A gap of .080 or more reads as "well ahead" or
   "playing better than their record", and the biggest one or two parts on that side are the reason. Tested on
   2023-24 to 2025-26: after 15 games the chances predicted the rest of the season better than the record did
   (r 0.59 against 0.42), and teams .100 ahead of their chances went on to finish near what the chances said.
2. Shooting holding them back (4.5+ goals under the chances) or running hot (6+ over), naming the shooters furthest off.
3. Goalies: one at +3 and another at -3 or worse goals saved above expected (3+ starts each), a goalie covering for a
   team that has been outplayed, or goaltending costing them 6+ goals.
4. Lines: one forward line with 58%+ of the 5-on-5 chances while the rest of the lineup has 47% or less, or the depth
   (neither of the two busiest trios on the ice) winning or being outplayed.

Team finishing and goaltending barely carry over from one stretch of a season to the next (r 0.04 and 0.08), so the
words say what has happened, never what will. No "luck" and no prediction on the page.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, SITE_DATA, TABLES
from pipeline.export.grammar import and_list as _and, cap as _cap, num
from pipeline.export.intros import _poss

MIN_GAMES = 8  # games before a team gets the checks
GAP = 0.08  # points percentage between the record and what the chances are worth: "well ahead" / "playing better"
SMALL_GAP = 0.05  # "a little ahead" / "a little behind"
REASON = 2.5  # goals a part needs to count as a reason for the gap
CLOSE_REASON = 1.5  # points from one-goal and overtime games that count as a reason
COLD, HOT = -4.5, 6.0  # shooting: goals under / over the chances
GOALIE_SPLIT, GOALIE_STARTS = 3.0, 3  # goals saved above expected, either way, and starts each
GOALIE_TEAM = 6.0  # goaltending covering for them or costing them
SHOOTER = 1.5  # a shooter this far off his chances gets named
LINE_TOP, LINE_REST, LINE_GAP = 0.58, 0.47, 0.12  # one line carrying them
DEPTH_UP, DEPTH_GOALS, DEPTH_DOWN, DEPTH_TOP = 0.54, 5, 0.42, 0.50

PP = ("5v4", "5v3", "4v3")
PK = ("4v5", "3v5", "3v4")
EN = ("EN", "EA")

TAGS = {
    "ahead": "Record ahead of their play", "behind": "Playing better than their record",
    "cold": "Shooting holding them back", "hot": "Shooting running hot",
    "split": "Goalies pulling apart", "covering": "Goalie covering for them", "costing": "Goaltending costing them",
    "carrying": "One line carrying them", "depth_up": "Depth driving it", "depth_down": "Depth being outplayed",
}


def _games(season: int) -> pl.DataFrame:
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    return g.filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))


@lru_cache(maxsize=8)
def team_games(season: int) -> pl.DataFrame:
    """One row per team-game: the five parts of the goal difference, points and whether it was a one-goal game."""
    g = _games(season)
    if g.is_empty():
        return pl.DataFrame()
    s = pl.col("strength")
    part = (pl.when(s == "5v5").then(pl.lit("ev")).when(s.is_in(EN)).then(pl.lit("en")).otherwise(pl.lit("st")))
    tg = pl.read_parquet(TABLES / str(season) / "team_game.parquet").join(g.select("game_id"), on="game_id").with_columns(c=part)
    diff = pl.col("xgf") - pl.col("xga")
    w = tg.group_by("game_id", "team_id").agg(
        ev=diff.filter(pl.col("c") == "ev").sum(),
        st=diff.filter(pl.col("c") == "st").sum(),
        fin=(pl.col("gf") - pl.col("xgf")).filter(pl.col("c") != "en").sum(),
        gk=(pl.col("xga") - pl.col("ga")).filter(pl.col("c") != "en").sum(),
        en=(pl.col("gf") - pl.col("ga")).filter(pl.col("c") == "en").sum(),
        gd=(pl.col("gf") - pl.col("ga")).sum(),  # shootout winners left out, unlike the final score
    )
    cols = ["game_id", "date", "last_period"]
    side = pl.concat([
        g.select(*cols, team_id="home_id", ab="home", gf="home_score", ga="away_score"),
        g.select(*cols, team_id="away_id", ab="away", gf="away_score", ga="home_score"),
    ])
    side = side.with_columns(
        pts=pl.when(pl.col("gf") > pl.col("ga")).then(2).when(pl.col("last_period") != "REG").then(1).otherwise(0),
    )
    return side.join(w, on=["game_id", "team_id"], how="left").fill_null(0)


def _totals(d: pl.DataFrame) -> pl.DataFrame:
    return d.group_by("team_id", "ab").agg(
        gp=pl.len(), pts=pl.col("pts").sum(), gd=pl.col("gd").sum(),
        ev=pl.col("ev").sum(), st=pl.col("st").sum(), fin=pl.col("fin").sum(), gk=pl.col("gk").sum(), en=pl.col("en").sum(),
    )


@lru_cache(maxsize=1)
def fit() -> dict:
    """Points percentage on chance difference a game, and on goal difference a game, over the full seasons."""
    t = pl.concat([_totals(team_games(s)) for s in FULL_SEASONS])
    y = (t["pts"] / (2 * t["gp"])).to_numpy()
    out = {}
    for key, x in (("play", ((t["ev"] + t["st"]) / t["gp"]).to_numpy()), ("goals", (t["gd"] / t["gp"]).to_numpy())):
        slope, icept = np.polyfit(x, y, 1)
        out[key] = (float(icept), float(slope))
    return out


def summary(season: int = CURRENT_SEASON, before: str | None = None) -> dict[str, dict]:
    """Each team's record, what its chances are worth and the parts of its goal difference, from games before a date."""
    d = team_games(season)
    if d.is_empty():
        return {}
    if before:
        d = d.filter(pl.col("date") < before[:10])
    k = fit()
    out = {}
    for r in _totals(d).iter_rows(named=True):
        gp = r["gp"]
        pp = r["pts"] / (2 * gp)
        play = k["play"][0] + k["play"][1] * (r["ev"] + r["st"]) / gp
        by_goals = k["goals"][0] + k["goals"][1] * r["gd"] / gp
        out[r["ab"]] = {**r, "pp": pp, "play": play, "gap": pp - play, "close": (pp - by_goals) * 2 * gp}
    return out


def reasons(r: dict, sign: int) -> list[tuple[float, str]]:
    """The parts on the side of the gap, biggest first: (size, kind), kind one of gk / fin / en / close."""
    out = [(r[k] * sign, k) for k in ("gk", "fin", "en") if r[k] * sign >= REASON]
    if r["close"] * sign >= CLOSE_REASON:
        out.append((r["close"] * sign * 1.6, "close"))  # a point is worth about 1.6 goals of difference over a season
    return sorted(out, reverse=True)


# ---------------------------------------------------------------- the people behind the parts

def _names(season: int) -> dict[int, str]:
    p = pl.read_parquet(TABLES / str(season) / "players.parquet").sort("game_id").unique("player_id", keep="last")
    return {r["player_id"]: f"{r['first']} {r['last']}".strip() for r in p.iter_rows(named=True)}


def _shooters(season: int) -> pl.DataFrame:
    """Goals against expected goals for every skater and club, empty nets out, with points for ordering lines."""
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet").join(_games(season).select("game_id"), on="game_id")
    return pg.group_by("player_id", "team_id").agg(
        g=pl.col("g_net").sum(), xg=pl.col("ixg_net").sum(), p=(pl.col("g") + pl.col("a1") + pl.col("a2")).sum(),
    ).with_columns(gax=pl.col("g") - pl.col("xg"))


def reliance(season: int) -> dict[int, dict]:
    """Each club's two busiest forward trios at 5-on-5 and the chances and goals with the first, the second and neither
    on the ice. A group counts as on when two of its three forwards are."""
    g = _games(season)
    keep = pl.concat([g.select("game_id", team_id="home_id"), g.select("game_id", team_id="away_id")])
    gu = pl.read_parquet(TABLES / str(season) / "game_units.parquet").filter(pl.col("kind") == "F").join(keep, on=["game_id", "team_id"])
    tops = {}
    busy = gu.group_by("team_id", "unit").agg(pl.col("sec").sum()).sort(["team_id", "sec", "unit"], descending=[False, True, False])
    for (tid,), grp in busy.group_by("team_id", maintain_order=True):
        chosen = []
        for u in grp["unit"]:
            ids = {int(x) for x in u.split("-")}
            if all(not (ids & c) for c in chosen):
                chosen.append(ids)
            if len(chosen) == 2:
                break
        if len(chosen) == 2:
            tops[tid] = chosen
    xg = pl.read_parquet(TABLES / str(season) / "shots_xg_own.parquet")
    ev = (pl.read_parquet(TABLES / str(season) / "events.parquet")
          .filter(pl.col("type").is_in(["goal", "shot-on-goal", "missed-shot"]) & (pl.col("situation_code") == "1551"))
          .join(xg, on=["game_id", "event_id"], how="left").with_columns(pl.col("xg").fill_null(0.0)))
    st = pl.read_parquet(TABLES / str(season) / "stints.parquet").filter(
        (pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
    gm = g.select("game_id", "home_id", "away_id")
    E, S = [], []
    for side in ("home", "away"):
        tcol = f"{side}_id"
        E.append(ev.join(gm, on="game_id").select(team=pl.col(tcol), on=pl.col(f"{side}_on"), mine=pl.col("team_id") == pl.col(tcol),
                                                  xg="xg", goal=pl.col("type") == "goal"))
        S.append(st.join(gm, on="game_id").select(team=pl.col(tcol), on=pl.col(f"{side}_skaters"), sec="duration"))
    E, S = pl.concat(E), pl.concat(S)
    out = {}
    for tid, (l1, l2) in tops.items():
        def lab(on):
            s = set(on or [])
            return "l1" if len(s & l1) >= 2 else "l2" if len(s & l2) >= 2 else "rest"
        acc = {k: {"sec": 0, "xf": 0.0, "xa": 0.0, "gf": 0, "ga": 0} for k in ("l1", "l2", "rest")}
        for r in S.filter(pl.col("team") == tid).iter_rows(named=True):
            acc[lab(r["on"])]["sec"] += r["sec"]
        for r in E.filter(pl.col("team") == tid).iter_rows(named=True):
            a = acc[lab(r["on"])]
            if r["mine"]:
                a["xf"] += r["xg"]
                a["gf"] += int(r["goal"])
            else:
                a["xa"] += r["xg"]
                a["ga"] += int(r["goal"])
        out[tid] = {"ids1": sorted(l1), "ids2": sorted(l2), **acc}
    return out


# ---------------------------------------------------------------- words

def p3(x: float) -> str:
    """A points percentage the way the standings print it: ".584"."""
    s = f"{x:.3f}"
    return s[1:] if s.startswith("0") else s


def g1(x: float) -> str:
    return f"{abs(x):.1f}"


def _goals(n: int) -> str:
    return f"{n} goal" if n == 1 else f"{n} goals"


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


class _Writer:
    """Collects the names a team's sentences mention, so the page can link them."""

    def __init__(self, names: dict[int, str]):
        self.names, self.said = names, {}

    def __call__(self, pid: int) -> str:
        n = self.names.get(pid, "")
        if n:
            self.said[pid] = n
        return n


def _shooter_list(rows: list[dict], who: _Writer) -> str:
    return _and([f"{who(r['player_id'])} ({_goals(int(r['g']))} on {r['xg']:.1f} expected)" for r in rows])


def team_checks(t: dict, r: dict, shooters: pl.DataFrame, goalies: list[dict], lines: dict | None, names: dict[int, str]) -> dict:
    """The checks for one team. `t` is its teams.json row, `r` its summary row."""
    who = _Writer(names)
    nick = t.get("nick") or t["place"]
    The = f"The {nick}"
    mine = shooters.filter(pl.col("team_id") == r["team_id"])
    cold = [x for x in mine.sort("gax", "player_id").head(3).to_dicts() if x["gax"] <= -SHOOTER]
    hot = [x for x in mine.sort(["gax", "player_id"], descending=[True, False]).head(3).to_dicts() if x["gax"] >= SHOOTER]
    gs = sorted([(g["id"], g["season"]["starts"], g["season"]["gsax"]) for g in goalies if (g.get("season") or {}).get("starts")],
                key=lambda g: -g[1])
    checks, tags = [], []
    sign = 1 if r["gap"] > 0 else -1

    def detail(kind: str) -> str:
        if kind == "gk":
            on_side = sorted([g for g in gs if g[2] * sign >= REASON], key=lambda g: -g[2] * sign)[:2]
            if not on_side:
                return ""
            verb = "saving" if sign > 0 else "giving up"
            amounts = _and([g1(g[2]) for g in on_side])
            return f", with {_and([who(g[0]) for g in on_side])} {verb} {amounts} more goals than expected"
        if kind == "fin":
            rows = (hot if sign > 0 else cold)[:2]
            return f", {'led by' if sign > 0 else 'most of all'} {_shooter_list(rows, who)}" if rows else ""
        return ""

    word = {"gk": "goaltending", "fin": "shooting",
            "en": "empty-net goals" if sign > 0 else "empty-net goals against",
            "close": f"{'winning' if sign > 0 else 'losing'} more than their share of one-goal and overtime games"}
    said, why = set(), []
    gap = r["gap"]
    if abs(gap) >= GAP:
        why = reasons(r, sign)[:2]
        said = {k for _, k in why}
        if sign > 0:
            verdict = "ahead"
            text = f"{The} have a {p3(r['pp'])} points percentage, but their chances at 5-on-5 and on special teams are worth about {p3(r['play'])}."
            if why:
                text += f" Most of the gap is {word[why[0][1]]}{detail(why[0][1])}."
                if len(why) > 1:
                    text += f" {_cap(word[why[1][1]])} added to it{detail(why[1][1])}."
            else:
                text += " No one thing explains it; it’s a little of everything."
        else:
            verdict = "behind"
            text = f"{The} are playing better than their {p3(r['pp'])} points percentage: their chances at 5-on-5 and on special teams are worth about {p3(r['play'])}."
            if why:
                text += f" What has held them back most is {word[why[0][1]]}{detail(why[0][1])}."
                if len(why) > 1:
                    text += f" {_cap(word[why[1][1]])} {'haven’t' if why[1][1] == 'en' else 'hasn’t'} helped either{detail(why[1][1])}."
            else:
                text += " No one thing explains it; it’s a little of everything."
        tags.append(verdict)
        checks.append({"kind": verdict, "text": text})
    else:
        verdict = "level" if abs(gap) < SMALL_GAP else "little_ahead" if gap > 0 else "little_behind"
        how = {"level": "is about", "little_ahead": "is a little ahead of", "little_behind": "is a little behind"}[verdict]
        checks.append({"kind": verdict, "text": f"{_cap(_poss(t))} record ({p3(r['pp'])}) {how} what their chances at 5-on-5 and on special teams are worth ({p3(r['play'])})."})
    big = verdict in ("ahead", "behind")

    # shooting
    if "fin" not in said:
        even_so = (big and sign < 0 and r["fin"] >= HOT) or (big and sign > 0 and r["fin"] <= COLD)
        if r["fin"] <= COLD:
            text = f"shooting has cost them about {g1(r['fin'])} goals against the chances they’ve had" + (f", most of all {_shooter_list(cold[:2], who)}" if cold else "") + "."
            checks.append({"kind": "cold", "text": ("Even so, " if even_so else "") + (text if even_so else _cap(text))})
            tags.append("cold")
        elif r["fin"] >= HOT:
            text = f"they’ve scored about {g1(r['fin'])} more goals than their chances usually bring" + (f", led by {_shooter_list(hot[:2], who)}" if hot else "") + "."
            checks.append({"kind": "hot", "text": ("Even so, " if even_so else "") + (text if even_so else _cap(text))})
            tags.append("hot")

    # goalies
    regular = [g for g in gs if g[1] >= GOALIE_STARTS]
    up = [g for g in regular if g[2] >= GOALIE_SPLIT]
    down = [g for g in regular if g[2] <= -GOALIE_SPLIT]
    if up and down:
        a, b = max(up, key=lambda g: g[2]), min(down, key=lambda g: g[2])
        checks.append({"kind": "split", "text": f"In net it’s two different seasons: {who(a[0])} has saved {g1(a[2])} more goals than expected in {a[1]} starts, and {who(b[0])} has given back {g1(b[2])} in {b[1]}."})
        tags.append("split")
    elif "gk" not in said and r["gk"] >= GOALIE_TEAM and r["ev"] + r["st"] <= 0 and gs:
        a = max(gs, key=lambda g: g[2])
        checks.append({"kind": "covering", "text": f"{who(a[0])} has kept them afloat: the chances against them would usually have produced about {g1(r['gk'])} more goals than they’ve allowed."})
        tags.append("covering")
    elif "gk" not in said and r["gk"] <= -GOALIE_TEAM:
        bad = [g for g in sorted(gs, key=lambda g: g[2]) if g[2] <= -2][:2]
        tail = f", most of it with {_and([who(g[0]) for g in bad])} in net" if bad else ""
        checks.append({"kind": "costing", "text": f"Goaltending has cost them about {g1(r['gk'])} goals against the chances they’ve allowed{tail}."})
        tags.append("costing")

    # lines
    if lines:
        def share(*ks):
            f, a = sum(lines[k]["xf"] for k in ks), sum(lines[k]["xa"] for k in ks)
            return f / (f + a) if f + a > 0 else 0.5
        l1, l2, rest, off = share("l1"), share("l2"), share("rest"), share("l2", "rest")
        pts = dict(zip(mine["player_id"].to_list(), mine["p"].to_list()))
        trio = [p for p in sorted(lines["ids1"], key=lambda p: (-pts.get(p, 0), p))]
        gf1, ga1 = lines["l1"]["gf"], lines["l1"]["ga"]
        ogf, oga = lines["l2"]["gf"] + lines["rest"]["gf"], lines["l2"]["ga"] + lines["rest"]["ga"]
        if l1 >= LINE_TOP and off <= LINE_REST and l1 - off >= LINE_GAP:
            trio = [who(p) for p in trio]
            if gf1 - ga1 >= 3 and ogf <= oga:
                text = (f"One line is carrying their 5-on-5 play: with {_and(trio)} on, they get {_pct(l1)} of the chances and have outscored teams {gf1}-{ga1}; "
                        f"with that line off, {_pct(off)} and {ogf}-{oga}.")
            else:
                text = f"One line is carrying their 5-on-5 play: with {_and(trio)} on, they get {_pct(l1)} of the chances; with that line off, {_pct(off)}."
            checks.append({"kind": "carrying", "text": text})
            tags.append("carrying")
        elif rest >= DEPTH_UP and lines["rest"]["gf"] - lines["rest"]["ga"] >= DEPTH_GOALS:
            checks.append({"kind": "depth_up", "text": f"Their depth is winning: with neither of their top two lines on at 5-on-5, they have {_pct(rest)} of the chances and a {lines['rest']['gf']}-{lines['rest']['ga']} edge in goals."})
            tags.append("depth_up")
        elif rest <= DEPTH_DOWN and max(l1, l2) >= DEPTH_TOP:
            rg, ra = lines["rest"]["gf"], lines["rest"]["ga"]
            goals = f"have been outscored {ra}-{rg}" if ra > rg else f"have still outscored teams {rg}-{ra}" if rg > ra else f"are level in goals at {rg}-{ra}"
            checks.append({"kind": "depth_down", "text": f"Beyond their top two lines they’re being outplayed: with neither on at 5-on-5, they get {_pct(rest)} of the chances and {goals}."})
            tags.append("depth_down")

    head = {"ahead": "Their record is ahead of their play", "behind": "They’re playing better than their record",
            "little_ahead": "Their record is a little ahead of their play", "little_behind": "Their record is a little behind their play",
            "level": "Their record fits how they’ve played"}[verdict]
    return {"verdict": verdict, "head": head, "why": [k for _, k in why], "tags": [TAGS[k] for k in tags], "checks": checks,
            "names": [{"id": pid, "name": n} for pid, n in who.said.items()]}


def run(season: int = CURRENT_SEASON) -> int:
    """Write behind.json: every team's record against its play, the parts of its goal difference and the checks."""
    teams = {t["abbr"]: t for t in json.loads((SITE_DATA / "teams.json").read_text())}
    goalies = json.loads((SITE_DATA / "goalies.json").read_text())["teams"] if (SITE_DATA / "goalies.json").exists() else {}
    s = summary(season)
    k = fit()
    out = {"season": season, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "min_games": MIN_GAMES,
           "gap": GAP, "fit": {"base": round(k["play"][0], 4), "per_goal": round(k["play"][1], 4)}, "teams": {}}
    ready = [ab for ab, r in s.items() if r["gp"] >= MIN_GAMES and ab in teams]
    lines = reliance(season) if ready else {}
    shooters = _shooters(season) if ready else None
    names = _names(season) if ready else {}
    for ab, r in sorted(s.items()):
        if ab not in teams:
            continue
        row = {"gp": r["gp"], "pts": r["pts"], "pp": round(r["pp"], 3), "play": round(r["play"], 3), "gap": round(r["gap"], 3),
               "parts": {"ev": round(r["ev"], 1), "st": round(r["st"], 1), "fin": round(r["fin"], 1), "gk": round(r["gk"], 1),
                         "en": int(r["en"]), "gd": int(r["gd"])}}
        if ab in ready:
            row.update(team_checks(teams[ab], r, shooters, goalies.get(ab, []), lines.get(r["team_id"]), names))
        out["teams"][ab] = row
    (SITE_DATA / "behind.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return len(ready)


# ---------------------------------------------------------------- previews

def preview_item(team: str, season: int, before: str, W) -> dict | None:
    """One Who-to-watch item when a team's record is well away from its play, from games before the preview's day."""
    r = summary(season, before).get(team)
    if not r or r["gp"] < MIN_GAMES or abs(r["gap"]) < GAP:
        return None
    sign = 1 if r["gap"] > 0 else -1
    why = reasons(r, sign)
    if sign > 0:
        word = {"gk": "their goaltending", "fin": "their shooting", "en": "empty-net goals", "close": "a good run in one-goal and overtime games"}
        tail = f", largely thanks to {word[why[0][1]]}" if why else ""
        text = f"{_cap(W.poss(team))} record ({p3(r['pp'])}) is well ahead of what their chances are worth ({p3(r['play'])}){tail}."
    else:
        word = {"gk": "their goaltending has", "fin": "their shooting has", "en": "empty-net goals against have", "close": "one-goal and overtime losses have"}
        text = f"{_cap(W.the(team))} have played better than their record ({p3(r['pp'])}): their chances are worth about {p3(r['play'])}"
        text += f", and {word[why[0][1]]} held them back." if why else "."
    return {"tag": "Behind the record", "text": text, "score": 58 + min(abs(r["gap"]), 0.2) * 50}


# ---------------------------------------------------------------- shooters whose luck may turn (previews)

LUCK_HOT, LUCK_COLD = 3.0, 2.5  # goals over / under what his chances and his own usual finishing would give
LUCK_MIN_XG = 3.0               # chances worth at least this many goals before a cold run is worth telling
FINISH_K = 20.0                 # expected goals of weight pulling his finishing from the last two seasons toward average


@lru_cache(maxsize=4)
def _finishing(season: int) -> dict[int, float]:
    """Each skater's usual finishing over the two seasons before: goals per expected goal, pulled toward 1."""
    rows = []
    for s in (season - 1, season - 2):
        p = TABLES / str(s) / "player_game.parquet"
        if p.exists():
            rows.append(pl.read_parquet(p, columns=["game_id", "player_id", "g_net", "ixg_net"]).join(_games(s).select("game_id"), on="game_id"))
    if not rows:
        return {}
    f = pl.concat(rows).group_by("player_id").agg(g=pl.col("g_net").sum(), xg=pl.col("ixg_net").sum())
    return {r["player_id"]: (r["g"] + FINISH_K) / (r["xg"] + FINISH_K) for r in f.iter_rows(named=True)}


def luck_items(away: str, home: str, season: int, before: str, W, names: dict[int, str]) -> list[dict]:
    """Who to watch: a shooter on either club scoring far above or below his chances, counted against his own usual
    finishing so a sniper is not called lucky for being a sniper (Sora, 2026-10-30: "whose luck may turn"). Only
    players who dressed in their club's last game, from games before the preview's day."""
    g = _games(season).filter(pl.col("date") < before[:10])
    if g.is_empty():
        return []
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet",
                         columns=["game_id", "player_id", "team_id", "g_net", "ixg_net"]).join(g.select("game_id", "date"), on="game_id")
    fin = _finishing(season)
    out = []
    for t in (away, home):
        ids = set(g.filter(pl.col("home") == t)["home_id"].to_list()) | set(g.filter(pl.col("away") == t)["away_id"].to_list())
        mine = pg.filter(pl.col("team_id").is_in(list(ids)))
        if mine.is_empty():
            continue
        last = mine.filter(pl.col("date") == mine["date"].max())["player_id"].to_list()
        s = (mine.filter(pl.col("player_id").is_in(last)).group_by("player_id")
             .agg(g=pl.col("g_net").sum(), xg=pl.col("ixg_net").sum()))
        best = None
        for r in s.iter_rows(named=True):
            exp = r["xg"] * fin.get(r["player_id"], 1.0)
            d = r["g"] - exp
            ok = (d >= LUCK_HOT and r["g"] >= 1.6 * exp) or (d <= -LUCK_COLD and r["xg"] >= LUCK_MIN_XG)
            if ok and r["player_id"] in names and (best is None or abs(d) > abs(best[0])):
                best = (d, r)
        if best:
            d, r = best
            who, n = names[r["player_id"]], int(r["g"])
            goals = f"{num(n)} goal{'' if n == 1 else 's'}" if n else "no goals"
            if d > 0:
                text = (f"{who} has {goals} on chances worth about {r['xg']:.1f} goals, well above what his shooting usually turns "
                        f"chances into. Runs like that tend to cool off.")
            else:
                text = (f"{who} has {goals} on chances worth about {r['xg']:.1f} goals, well below what his shooting usually "
                        f"turns chances into. If the chances keep coming, so should the goals.")
            out.append({"tag": "Luck may turn", "text": text, "pid": r["player_id"], "d": abs(d)})
    out.sort(key=lambda i: -i.pop("d"))
    for i, x in enumerate(out):  # one a preview usually makes the list; the other club's only when there is room
        x["score"] = 72 if i == 0 else 61
    return out


TEXT_GAP = 0.05  # a record this far from the chances is worth a clause in the preview's opening paragraph


def preview_sentence(away: str, home: str, season: int, before: str, W) -> str | None:
    """Each club's record against what its chances are worth, for the preview's opening paragraph (Sora, 2026-10-30:
    "a team by team over/underperformance note in the text"). A club near its chances gets a short clause only when
    the other one is told."""
    s = summary(season, before)
    rows = {t: s.get(t) for t in (away, home)}
    if any(r is None or r["gp"] < MIN_GAMES for r in rows.values()):
        return None
    if all(abs(r["gap"]) < TEXT_GAP for r in rows.values()):
        return None

    up = {"gk": "their goaltending", "fin": "their shooting", "en": "empty-net goals", "close": "winning the close ones"}
    down = {"gk": "their goaltending has", "fin": "their shooting has", "en": "empty-net goals against have", "close": "losses in close games have"}

    def why(r):
        sign = 1 if r["gap"] > 0 else -1
        w = reasons(r, sign)
        if not w:
            return ""
        return f", largely thanks to {up[w[0][1]]}" if sign > 0 else f", and {down[w[0][1]]} held them back"

    def clause(t, r):
        if abs(r["gap"]) < TEXT_GAP:
            return f"{W.poss(t)} record ({p3(r['pp'])}) is about what their chances are worth"
        if r["gap"] > 0:
            return f"{W.poss(t)} record ({p3(r['pp'])}) is running ahead of what their chances are worth ({p3(r['play'])}){why(r)}"
        return f"{W.the(t)} have played better than their record ({p3(r['pp'])}) says: their chances are worth about {p3(r['play'])}{why(r)}"
    ra, rh = rows[away], rows[home]
    side = lambda r: 0 if abs(r["gap"]) < TEXT_GAP else (1 if r["gap"] > 0 else -1)
    a = clause(away, ra)
    if side(ra) == side(rh) != 0:  # the same story twice reads as a list: "So is the Islanders’ (.615 on chances worth .521)"
        h = (f"So is {W.poss(home)} ({p3(rh['pp'])} on chances worth {p3(rh['play'])}){why(rh)}" if side(rh) > 0 else
             f"So have {W.the(home)} ({p3(rh['pp'])} on chances worth {p3(rh['play'])}){why(rh).replace(', and ', ', where ', 1)}")
        return f"{cap_(a)}. {h}."
    h = clause(home, rh)
    if "," in a or "," in h:
        return f"{cap_(a)}. {cap_(h)}."
    return f"{cap_(a)}{', and ' if side(ra) == side(rh) else ', while '}{h}."


def cap_(s: str) -> str:
    return s[:1].upper() + s[1:]
