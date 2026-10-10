"""Post-game pages: grade each preview call frozen at puck drop, by the rule stored with it, and add the goalies, the
team numbers and a few surprises.

The preview is never rewritten: grading reads the snapshot exactly as it was saved. Calls that cannot be judged (a
power play that barely happened, a line that was hardly on the ice) are marked "na" and left out of the tally.
Reads the site JSON written earlier in `export` (snapshots, fingerprints, lines), so it runs after `previews`.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

import polars as pl

from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import previews
from pipeline.export import surprises as surprise_rules
from pipeline.metrics import adjust, goalies, replays, team_style

PP_STRENGTHS = {"5v4", "5v3", "4v3"}
MIN_PP_SEC = 60  # less power-play time than this and a power-play call can't be judged
MIN_CLOSE_SEC = 600  # hit rates need some close-game time
MIN_MATCHUP_SEC = 180  # a matchup call needs the group on the ice against the opposing lines for three minutes


def clock(sec: float) -> str:
    s = round(sec)
    return f"{s // 60}:{s % 60:02d}"


def grade(v: float, base: float, floor: float | None, direction: str) -> str:
    """held: at or beyond the team's usual level; partly: beyond the floor (league average or no-matching level)."""
    beyond = (lambda a, b: a >= b) if direction == "above" else (lambda a, b: a <= b)
    if beyond(v, base):
        return "held"
    if floor is not None and beyond(v, floor):
        return "partly"
    return "missed"


class Season:
    """Everything about the finished games of one season that grading needs, loaded once."""

    def __init__(self, season: int):
        d = TABLES / str(season)
        self.games = {g["game_id"]: g for g in pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).iter_rows(named=True)}
        self.team_id = {}
        for g in self.games.values():
            self.team_id[(g["game_id"], g["home"])] = g["home_id"]
            self.team_id[(g["game_id"], g["away"])] = g["away_id"]
        self.rows = {(r["game_id"], r["team_id"]): r for r in team_style.per_game(season, adjust.weights()).iter_rows(named=True)}
        tg = pl.read_parquet(d / "team_game.parquet")
        self.totals = {(r["game_id"], r["team_id"]): r for r in tg.group_by("game_id", "team_id").agg(
            pl.col("sf").sum(), pl.col("hits").sum(),
            pl.col("xgf").filter(pl.col("strength") != "EN").sum().alias("xg"),
            pl.col("cf").filter(pl.col("strength") == "5v5").sum().alias("cf5"),
        ).iter_rows(named=True)}
        disc = pl.read_parquet(d / "discipline.parquet")
        self.short = {(r["game_id"], r["team_id"]): r["times_short"] for r in disc.iter_rows(named=True)}
        self.goals = pl.read_parquet(d / "goals.parquet")
        players = pl.read_parquet(d / "players.parquet")
        self.pos = {(r["game_id"], r["player_id"]): r["pos"] for r in players.select("game_id", "player_id", "pos").iter_rows(named=True)}
        self.name = {r["player_id"]: f"{r['first'][0]}. {r['last']}" for r in players.sort("game_id").iter_rows(named=True)}
        self.stints = pl.read_parquet(d / "stints.parquet")
        self.units = pl.read_parquet(d / "game_units.parquet")
        self.goalies = goalies.per_game(season)

    def pp_goals(self, gid: int, tid: int) -> int:
        return self.goals.filter((pl.col("game_id") == gid) & (pl.col("team_id") == tid) & pl.col("strength").is_in(list(PP_STRENGTHS))).height


def measure(S: Season, gid: int, claim: dict, places: dict, fp: dict) -> dict:
    """Tonight's figure for one call, its verdict, and a sentence saying what happened."""
    chk, m = claim["check"], claim["metric"]
    if claim["kind"] in ("edge", "contrast"):
        return measure_edge(S, gid, claim, places, fp)
    if claim["kind"] == "changed":
        return measure_changed(S, gid, claim, places)
    if chk is None:
        return {"verdict": None}
    tid = S.team_id.get((gid, chk["team"])) if chk.get("team") else None
    base, league = chk["baseline"], chk.get("league")
    team = places.get(chk.get("team"), "")
    if m in ("volume", "quality", "turnover", "pp"):
        r = S.rows.get((gid, tid))
        if r is None:
            return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
        if m == "volume":
            v = r["cf_adj"] / r["sec5"] * 3600
            txt = f"{team} took {v:.1f} shot attempts per 60 at 5-on-5 (adjusted for score and venue), against their usual {base:.1f}."
            fmt = lambda x: f"{x:.1f} per 60"
        elif m == "quality":
            v = r["xgf_adj"] / r["ff_adj"] if r["ff_adj"] else 0.0
            txt = f"{team}’s average shot had {previews.a_pct(v)} chance of going in, against their usual {100 * base:.1f}%." if v else f"{team} barely got a shot away at 5-on-5."
            fmt = lambda x: f"{100 * x:.1f}% a shot" if x else "–"
        elif m == "turnover":
            v = 100 * r["to_xgf"] / r["xgf5"] if r["xgf5"] else 0.0
            txt = f"{v:.1f}% of {team}’s 5-on-5 chances came right after winning the puck, against their usual {base:.1f}%."
            fmt = lambda x: f"{x:.1f}%"
        else:
            if r["pp_sec"] < MIN_PP_SEC:
                return {"verdict": "na", "happened": f"{team} had only {clock(r['pp_sec'])} of power-play time, too little to judge.",
                        "usual": f"{base:.1f} per 60", "tonight": "–"}
            v = r["pp_xgf"] / r["pp_sec"] * 3600
            g = S.pp_goals(gid, tid)
            txt = (f"{team}’s power play created {v:.1f} expected goals per 60 in {clock(r['pp_sec'])} of 5-on-4 time, against their usual {base:.1f}, "
                   f"and scored {g} {'goal' if g == 1 else 'goals'}.")
            fmt = lambda x: f"{x:.1f} per 60"
        if claim["kind"] == "duel":  # graded against the league average alone: which side of it the attack landed on
            usual = chk.get("usual", base)
            what = {"volume": "5-on-5 shot attempts", "quality": "chance quality", "pp": "the power play", "turnover": "chances off turnovers"}[m]
            txt = f"On {what}, {team} had {fmt(v)}. The league average is {fmt(base)}, and they came in at {fmt(usual)}."
            return {"verdict": grade(v, base, None, chk["direction"]), "happened": txt, "usual": fmt(usual), "tonight": fmt(v), "league": fmt(base)}
        out = {"verdict": grade(v, base, league, chk["direction"]), "happened": txt, "usual": fmt(base), "tonight": fmt(v)}
        if league is not None:
            out["league"] = fmt(league)
        return out
    g = S.games[gid]
    rows = [S.rows.get((gid, g["away_id"])), S.rows.get((gid, g["home_id"]))]
    if any(r is None for r in rows):
        return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
    if m == "pace":
        r = rows[0]
        v = (r["ff_adj"] + r["fa_adj"]) / r["sec5"] * 3600
        usual = (f"the {'faster' if chk['direction'] == 'above' else 'slower'} team’s usual {base:.1f}" if "average" in chk
                 else f"the two teams’ usual {base:.1f}")  # rules 3 set the bar at the faster (or slower) team's usual
        return {"verdict": grade(v, base, league, chk["direction"]),
                "happened": f"The game ran at {v:.1f} shots per 60 at 5-on-5, both teams combined, against {usual} and a league average of {league:.1f}.",
                "usual": f"{base:.1f} per 60", "tonight": f"{v:.1f} per 60", "league": f"{league:.1f} per 60"}
    if m == "physical":
        if min(r["close_sec"] for r in rows) < MIN_CLOSE_SEC:
            return {"verdict": "na", "happened": "The game was rarely close, and hit rates are only counted in close games, so this one can’t be judged.", "usual": f"hit score {base:.0f}", "tonight": "–"}
        est = fp[g["away"]]["dims"]["physical"]["blend"]
        league_rate = est["v"] * 100 / est["index"]
        v = sum(r["hits_adj"] / r["close_sec"] * 3600 for r in rows) / 2 / league_rate * 100
        return {"verdict": grade(v, base, 100, "above"),
                "happened": f"The two teams’ combined hit score was {v:.0f}, against their usual {base:.0f} (100 is league average, after adjusting for the arena’s scorer).",
                "usual": f"hit score {base:.0f}", "tonight": f"hit score {v:.0f}", "league": "hit score 100"}
    if m == "matchup_share":
        if "opp_lines" not in chk:
            return {"verdict": "na", "happened": "This call was saved before the site recorded the visitors’ lines, so it can’t be checked."}
        sec = matchup_seconds(S, gid, chk)
        total = sum(sec.values())
        names = ", ".join(S.name.get(i, "?").split(" ", 1)[-1] for i in chk["unit_ids"])
        opp_place = places.get(claim["opp"], claim["opp"])
        if total < MIN_MATCHUP_SEC:
            return {"verdict": "na", "happened": f"{team}’s {chk['unit']} ({names}) spent only {clock(total)} against {opp_place}’s usual lines, too little to judge.",
                    "usual": f"{base}%", "tonight": "–"}
        v = round(100 * sec.get(chk["opp_line"], 0) / total)
        return {"verdict": grade(v, base, chk["threshold"], "above"),
                "happened": f"{team}’s {chk['unit']} ({names}) spent {v}% of its 5-on-5 time against {opp_place}’s {chk['opp_line']} "
                            f"({clock(sec.get(chk['opp_line'], 0))} of {clock(total)}), against a home habit of {base}% and about {chk['threshold']}% with no line matching.",
                "usual": f"{base}%", "tonight": f"{v}%", "league": f"{chk['threshold']}%"}
    return {"verdict": "na"}


def measure_edge(S: Season, gid: int, claim: dict, places: dict, fp: dict) -> dict:
    """An edge call: did the team at the better end of the scale come out ahead tonight? Notes saved under the first
    rulebook ("contrast", with no check) say the same thing in words, so they are checked by the same rule."""
    chk, k = claim["check"], claim["metric"]
    if chk is None:
        if k not in previews.EDGE:
            return {"verdict": "na", "happened": "This note compared the two teams on pace, which both teams share in a single game, so it can’t be checked."}
        chk = previews.edge_check(k, claim["team"], claim["opp"], fp)
    top, bot = chk["team"], chk["opp"]
    rt, rb = S.rows.get((gid, S.team_id.get((gid, top)))), S.rows.get((gid, S.team_id.get((gid, bot))))
    T, B = places.get(top, top), places.get(bot, bot)
    if rt is None or rb is None:
        return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
    need = {"pp": ("pp_sec", MIN_PP_SEC, "power-play time"), "pk": ("pk_sec", MIN_PP_SEC, "penalty-kill time"),
            "physical": ("close_sec", MIN_CLOSE_SEC, "close-game time")}.get(k, ("sec5", 600, "5-on-5 time"))
    short = min(rt[need[0]], rb[need[0]])
    if short < need[1]:
        return {"verdict": "na", "happened": f"One of the teams had only {clock(short)} of {need[2]}, too little to judge."}
    val = lambda r: team_style._value(r, k)
    tv, bv = val(rt), val(rb)
    if k in previews.INDEX_TRAITS:
        if not chk.get("league"):
            return {"verdict": "na", "happened": "There was no league average to score this against."}
        tv, bv = 100 * tv / chk["league"], 100 * bv / chk["league"]
    if tv != tv or bv != bv:  # NaN: nothing this trait counts happened
        return {"verdict": "na", "happened": "Too little of what this call counts happened to judge it."}
    d = (tv - bv) if chk["higher"] else (bv - tv)
    gap = chk["gap"]
    verdict = "held" if d >= gap / 2 else "partly" if d > 0 else "missed"
    fmt = previews.EDGE[k][4]
    ut, ub = chk["baseline"][top], chk["baseline"][bot]
    return {"verdict": verdict,
            "happened": f"On {chk['measure']}, {T} had {fmt(tv)} and {B} {fmt(bv)}. Coming in, {T} was at {fmt(ut)} and {B} at {fmt(ub)}.",
            "usual": f"{top} {fmt(ut)} · {bot} {fmt(ub)}", "tonight": f"{top} {fmt(tv)} · {bot} {fmt(bv)}"}


def measure_changed(S: Season, gid: int, claim: dict, places: dict) -> dict:
    """A team playing unlike their two-season selves: did tonight land on this season's side of the blended level?"""
    chk, k = claim["check"], claim["metric"]
    r = S.rows.get((gid, S.team_id.get((gid, chk["team"]))))
    T = places.get(chk["team"], chk["team"])
    if r is None:
        return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
    need = {"pp": ("pp_sec", MIN_PP_SEC, "power-play time"), "pk": ("pk_sec", MIN_PP_SEC, "penalty-kill time"),
            "physical": ("close_sec", MIN_CLOSE_SEC, "close-game time")}.get(k, ("sec5", 600, "5-on-5 time"))
    if r[need[0]] < need[1]:
        return {"verdict": "na", "happened": f"{T} had only {clock(r[need[0]])} of {need[2]}, too little to judge."}
    v = team_style._value(r, k)
    if v != v:
        return {"verdict": "na", "happened": "Too little of what this call counts happened to judge it."}
    base, season = chk["baseline"], chk["season"]
    fmt = previews.EDGE[k][4]
    show = (lambda x: fmt(100 * x / chk["league"])) if k in previews.INDEX_TRAITS and chk.get("league") else fmt
    beyond = (lambda a, b: a > b) if chk["direction"] == "above" else (lambda a, b: a < b)
    verdict = "held" if beyond(v, base) else "missed"
    return {"verdict": verdict,
            "happened": f"On {previews.EDGE[k][3]}, {T} had {show(v)}, against {show(base)} over this season and last and {show(season)} this season alone.",
            "usual": show(base), "tonight": show(v)}


def measure_history(claim: dict, glines: list) -> dict:
    """A goalie's record against one team, called a coincidence: was tonight closer to his usual than to that record?"""
    chk = claim["check"]
    g = next((x for x in glines if x["id"] == chk["goalie_id"]), None)
    fmt = lambda x: f"{x:.3f}".lstrip("0")
    if g is None or not g["started"] or not g["shots"]:
        return {"verdict": "na", "happened": "He didn’t start, so this call can’t be judged.", "usual": fmt(chk["baseline"]), "tonight": "–"}
    sv = g["saves"] / g["shots"]
    verdict = "held" if abs(sv - chk["baseline"]) < abs(sv - chk["record"]) else "missed"
    return {"verdict": verdict,
            "happened": f"{g['name']} stopped {g['saves']} of {g['shots']} shots ({fmt(sv)}), against his usual {fmt(chk['baseline'])} and his {fmt(chk['record'])} against this team before tonight.",
            "usual": fmt(chk["baseline"]), "tonight": fmt(sv), "league": None}


def matchup_seconds(S: Season, gid: int, chk: dict) -> dict[str, float]:
    """5v5 seconds the home group spent against each of the visitors' usual forward lines in this game."""
    g = S.games[gid]
    unit = set(chk["unit_ids"])
    need = 2 if len(unit) == 3 else 1
    lines = {k: set(v) for k, v in chk["opp_lines"].items()}
    out = defaultdict(float)
    st = S.stints.filter((pl.col("game_id") == gid) & (pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
    home_is_team = chk["team"] == g["home"]
    for s in st.iter_rows(named=True):
        mine, theirs = (s["home_skaters"], s["away_skaters"]) if home_is_team else (s["away_skaters"], s["home_skaters"])
        if len(unit & set(mine)) < need:
            continue
        fwd = {p for p in theirs if S.pos.get((gid, p)) in ("C", "L", "R")}
        best = max(lines, key=lambda k: len(lines[k] & fwd)) if lines else None
        if best and len(lines[best] & fwd) >= 2:
            out[best] += s["duration"]
    return out


def team_numbers(S: Season, gid: int, abbr: str) -> dict:
    tid = S.team_id[(gid, abbr)]
    t = S.totals.get((gid, tid), {})
    opp = next(a for (g, a) in S.team_id if g == gid and a != abbr)
    return {"shots": int(t.get("sf", 0)), "xg": round(t.get("xg", 0.0), 2), "cf5": int(t.get("cf5", 0)), "hits": int(t.get("hits", 0)),
            "pp_goals": S.pp_goals(gid, tid), "pp_opps": int(S.short.get((gid, S.team_id[(gid, opp)]), 0))}


def goalie_lines(S: Season, gid: int, abbr_of: dict) -> list[dict]:
    out = []
    for r in S.goalies.filter(pl.col("game_id") == gid).sort("team_id", "sec", descending=[False, True]).iter_rows(named=True):
        sv = (r["sa"] - r["ga"]) / r["sa"] if r["sa"] else None
        out.append({"id": r["goalie_id"], "name": S.name.get(r["goalie_id"], "?"), "team": abbr_of[r["team_id"]], "started": r["started"],
                    "toi": clock(r["sec"]), "shots": r["sa"], "saves": r["sa"] - r["ga"], "ga": r["ga"], "xga": round(r["xga"], 2),
                    "gsax": round(r["xga"] - r["ga"], 2), "sv": round(sv, 3) if sv is not None else None,
                    "high": {"shots": r["sa_high"], "goals": r["ga_high"]}})
    return out


def surprises(S: Season, gid: int, nums: dict, places: dict, fp: dict, usual_lines: dict, glines: list, replay: dict | None,
              calls: list[dict] = ()) -> tuple[list[dict], dict[str, dict]]:
    """See `surprises.py`. `fp` and `usual_lines` are the teams' numbers as the preview saw them; `calls` are the
    snapshot's claims with their verdicts. Returns the surprises and an "and then some" note per call id."""
    g = S.games[gid]
    a, h = g["away"], g["home"]
    rows = {t: S.rows.get((gid, S.team_id[(gid, t)])) for t in (a, h)}
    if any(r is None for r in rows.values()):
        return [], {}
    score = {a: g["away_score"], h: g["home_score"], "pp_goals": {t: nums[t]["pp_goals"] for t in (a, h)},
             "pp_opps": {t: nums[t]["pp_opps"] for t in (a, h)}}
    trios = []
    for t in (a, h):
        usual = [set(ids) for ids in usual_lines.get(t, [])]
        if not usual:
            continue
        units = S.units.filter((pl.col("game_id") == gid) & (pl.col("team_id") == S.team_id[(gid, t)]) & (pl.col("kind") == "F") & (pl.col("sec") >= 300)).sort("sec", descending=True)
        for r in units.iter_rows(named=True):
            ids = {int(x) for x in r["unit"].split("-")}
            if ids not in usual:
                trios.append((t, ", ".join(S.name.get(i, "?") for i in sorted(ids, key=lambda i: S.name.get(i, ""))), int(r["sec"])))
                break
    cands, beyond = surprise_rules.against_calls(surprise_rules.candidates(rows, a, h, score, fp, places, glines, trios, replay), list(calls))
    return surprise_rules.pick(cands), beyond


def replay_block(row: dict, away: str, home: str) -> dict:
    """Run-it-back shares for the post-game page: replays of regulation chances, level replays split evenly."""
    return {"share": {away: round(row["away_share"], 3), home: round(row["home_share"], 3)},
            "regulation": {away: round(row["away_reg_win"], 3), "tie": round(row["reg_tie"], 3), home: round(row["home_reg_win"], 3)},
            "xg": {away: round(row["away_xg"], 2), home: round(row["home_xg"], 2)},
            "chances": {away: row["away_chances"], home: row["home_chances"]}}


def run(season: int = CURRENT_SEASON) -> dict:
    teams = json.loads((SITE_DATA / "teams.json").read_text())
    places = {t["abbr"]: t["place"] for t in teams}
    fp = json.loads((SITE_DATA / "fingerprints.json").read_text())["teams"]
    lines = json.loads((SITE_DATA / "lines.json").read_text())["teams"]
    usual_lines = {t: [u["ids"] for u in v["units"] if u["label"][0] == "L"] for t, v in lines.items()}
    S = Season(season)
    meter = {r["game_id"]: r for r in replays.season_table(season).iter_rows(named=True)}
    out_dir = SITE_DATA / "recaps"
    out_dir.mkdir(parents=True, exist_ok=True)
    snaps = {}
    for p in (SITE_DATA / "previews").glob("*.json"):
        if p.name != "index.json":
            s = json.loads(p.read_text())
            snaps[s["game_id"]] = s
    index, tally = [], defaultdict(int)
    from pipeline.export import outlook
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    try:
        reveals = outlook.reveal(season, [g for g in sched if g["final"]], places)
    except Exception as e:  # the post-game page still works without the reveal
        print(f"win chance reveal unavailable: {e!r}")
        reveals = {}
    by_kind = defaultdict(lambda: defaultdict(int))
    for gid, g in sorted(S.games.items()):
        if g["state"] not in ("OFF", "FINAL"):
            continue
        abbr_of = {g["home_id"]: g["home"], g["away_id"]: g["away"]}
        nums = {t: team_numbers(S, gid, t) for t in (g["away"], g["home"])}
        glines = goalie_lines(S, gid, abbr_of)
        snap = snaps.get(gid)
        rebuilt = bool((snap or {}).get("rebuilt"))
        pre = (snap or {}).get("pregame") or {}
        fp_pre = {**fp, **pre.get("fingerprints", {}).get("teams", {})}
        lines_pre = {**usual_lines, **{t: [u["ids"] for u in v["units"] if u["label"][0] == "L"] for t, v in pre.get("lines", {}).get("teams", {}).items()}}
        rb = replay_block(meter[gid], g["away"], g["home"]) if gid in meter else None
        calls, graded_claims = [], []
        for c in (snap or {}).get("claims", []):
            res = measure_history(c, glines) if c["kind"] == "history" else measure(S, gid, c, places, fp_pre)
            graded_claims.append({**c, "verdict": res.get("verdict")})
            calls.append({"id": c["id"], "kind": c["kind"], "head": c["head"], "body": c["body"], "call": c.get("call"), "cite": c["cite"], **res})
            if res.get("verdict") in ("held", "partly", "missed"):
                tally[res["verdict"]] += 1  # rebuilt previews count like live ones (user decision)
                by_kind[c["kind"]][res["verdict"]] += 1
        surp, beyond = surprises(S, gid, nums, places, fp_pre, lines_pre, glines, rb, graded_claims)
        for c in calls:
            if c["id"] in beyond:
                c["beyond"] = beyond[c["id"]]["body"]
        rec = {"game_id": gid, "date": g["date"], "start": g["start_utc"], "away": g["away"], "home": g["home"], "venue": g["venue"],
               "score": {g["away"]: g["away_score"], g["home"]: g["home_score"]}, "end": g["last_period"],
               "preview": snap is not None, "rebuilt": rebuilt, "preview_page": bool((snap or {}).get("pregame")), "snapshot_at": snap["snapshot_at"] if snap else None, "calls": calls,
               "reveal": reveals.get(gid), "numbers": nums, "goalies": glines, "surprises": surp,
               "replays": rb}
        (out_dir / f"{gid}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        graded = [c["verdict"] for c in calls if c.get("verdict") in ("held", "partly", "missed")]
        index.append({"id": gid, "date": g["date"], "away": g["away"], "home": g["home"], "score": rec["score"], "end": g["last_period"],
                      "rebuilt": rebuilt, "calls": len(graded), "held": graded.count("held"), "partly": graded.count("partly"), "missed": graded.count("missed")})
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season,
               "tally": {k: tally[k] for k in ("held", "partly", "missed")},
               "by_kind": {k: dict(v) for k, v in by_kind.items()}, "games": index}
    (out_dir / "index.json").write_text(json.dumps(summary, separators=(",", ":")))
    return {"recaps": len(index), "graded": sum(tally.values())}
