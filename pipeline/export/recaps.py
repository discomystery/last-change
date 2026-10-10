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
from pipeline.metrics import adjust, deserve, goalies, team_style

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
            txt = f"{team} took {v:.1f} shot attempts per 60 at 5-on-5 (adjusted for score and venue), against its usual {base:.1f}."
            fmt = lambda x: f"{x:.1f} per 60"
        elif m == "quality":
            v = r["xgf_adj"] / r["ff_adj"] if r["ff_adj"] else 0.0
            txt = f"{team} needed {1 / v:.1f} unblocked shots per expected goal, against its usual {1 / base:.1f}." if v else f"{team} barely got a shot away at 5-on-5."
            fmt = lambda x: f"1 per {1 / x:.1f} shots" if x else "–"
        elif m == "turnover":
            v = 100 * r["to_xgf"] / r["xgf5"] if r["xgf5"] else 0.0
            txt = f"{v:.1f}% of {team}’s 5-on-5 chances came right after winning the puck, against its usual {base:.1f}%."
            fmt = lambda x: f"{x:.1f}%"
        else:
            if r["pp_sec"] < MIN_PP_SEC:
                return {"verdict": "na", "happened": f"{team} had only {clock(r['pp_sec'])} of power-play time, too little to judge.",
                        "usual": f"{base:.1f} per 60", "tonight": "–"}
            v = r["pp_xgf"] / r["pp_sec"] * 3600
            g = S.pp_goals(gid, tid)
            txt = (f"{team}’s power play created {v:.1f} expected goals per 60 in {clock(r['pp_sec'])} of 5-on-4 time, against its usual {base:.1f}, "
                   f"and scored {g} {'goal' if g == 1 else 'goals'}.")
            fmt = lambda x: f"{x:.1f} per 60"
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
        return {"verdict": grade(v, base, league, chk["direction"]),
                "happened": f"The game ran at {v:.1f} unblocked shots per 60 at 5-on-5, both teams combined, against the two teams’ usual {base:.1f} and a league average of {league:.1f}.",
                "usual": f"{base:.1f} per 60", "tonight": f"{v:.1f} per 60", "league": f"{league:.1f} per 60"}
    if m == "physical":
        if min(r["close_sec"] for r in rows) < MIN_CLOSE_SEC:
            return {"verdict": "na", "happened": "The game was rarely close, and hit rates are only counted in close games, so this one can’t be judged.", "usual": f"{base:.0f}", "tonight": "–"}
        est = fp[g["away"]]["dims"]["physical"]["blend"]
        league_rate = est["v"] * 100 / est["index"]
        v = sum(r["hits_adj"] / r["close_sec"] * 3600 for r in rows) / 2 / league_rate * 100
        return {"verdict": grade(v, base, 100, "above"),
                "happened": f"The two teams’ combined hit score was {v:.0f}, against their usual {base:.0f} (100 is league average, after adjusting for the arena’s scorer).",
                "usual": f"{base:.0f}", "tonight": f"{v:.0f}", "league": "100"}
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


def surprises(S: Season, gid: int, nums: dict, places: dict, fp: dict, usual_lines: dict, glines: list) -> list[dict]:
    g = S.games[gid]
    a, h = g["away"], g["home"]
    win, lose = (h, a) if g["home_score"] > g["away_score"] else (a, h)
    out = []
    xw, xl = nums[win]["xg"], nums[lose]["xg"]
    if xw < xl - 0.5:
        best = max((x for x in glines if x["team"] == win), key=lambda x: x["gsax"], default=None)
        extra = f" {best['name']} stopped {best['gsax']:.1f} more goals than expected." if best and best["gsax"] >= 1 else ""
        out.append({"head": f"{places[win]} won without the better chances",
                    "body": f"Expected goals favoured {places[lose]}, {xl:.1f} to {xw:.1f}, with empty nets left out.{extra}"})
    elif xw - xl >= 1.5 and xw >= 1.8 * max(xl, 0.1):
        out.append({"head": f"{places[win]} controlled the chances", "body": f"Expected goals ran {xw:.1f} to {xl:.1f} for {places[win]}, with empty nets left out."})
    for t in (a, h):
        r = S.rows.get((gid, S.team_id[(gid, t)]))
        usual = fp[t]["dims"]["volume"]["blend"]["v"]
        if r and r["sec5"] > 1200:
            v = r["cf_adj"] / r["sec5"] * 3600
            if v >= usual * 1.25 or v <= usual * 0.75:
                more = v > usual
                out.append({"head": f"{places[t]} shot {'far more' if more else 'far less'} than usual",
                            "body": f"{v:.1f} shot attempts per 60 at 5-on-5, adjusted for score and venue, against its usual {usual:.1f}."})
    for t in (a, h):
        tid = S.team_id[(gid, t)]
        usual = [set(ids) for ids in usual_lines.get(t, [])]
        trios = S.units.filter((pl.col("game_id") == gid) & (pl.col("team_id") == tid) & (pl.col("kind") == "F") & (pl.col("sec") >= 300)).sort("sec", descending=True)
        for r in trios.iter_rows(named=True):
            ids = {int(x) for x in r["unit"].split("-")}
            if ids not in usual:
                names = ", ".join(S.name.get(i, "?") for i in sorted(ids, key=lambda i: S.name.get(i, "")))
                out.append({"head": f"{places[t]} tried a different line", "body": f"{names} played {clock(r['sec'])} together at 5-on-5, a trio that isn’t one of {places[t]}’s usual lines."})
                break
    for t in (a, h):
        if nums[t]["pp_goals"] >= 2:
            out.append({"head": f"{places[t]}’s power play did damage", "body": f"{nums[t]['pp_goals']} power-play goals on {nums[t]['pp_opps']} chances."})
    return out[:4]


def deserve_block(row: dict, away: str, home: str) -> dict:
    """Deserve-to-win shares for the post-game meter: replays of regulation chances, level replays split evenly."""
    return {"share": {away: round(row["away_deserve"], 3), home: round(row["home_deserve"], 3)},
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
    meter = {r["game_id"]: r for r in deserve.season_table(season).iter_rows(named=True)}
    out_dir = SITE_DATA / "recaps"
    out_dir.mkdir(parents=True, exist_ok=True)
    snaps = {}
    for p in (SITE_DATA / "previews").glob("*.json"):
        if p.name != "index.json":
            s = json.loads(p.read_text())
            snaps[s["game_id"]] = s
    index, tally = [], defaultdict(int)
    by_kind = defaultdict(lambda: defaultdict(int))
    for gid, g in sorted(S.games.items()):
        if g["state"] not in ("OFF", "FINAL"):
            continue
        abbr_of = {g["home_id"]: g["home"], g["away_id"]: g["away"]}
        nums = {t: team_numbers(S, gid, t) for t in (g["away"], g["home"])}
        glines = goalie_lines(S, gid, abbr_of)
        snap = snaps.get(gid)
        calls = []
        for c in (snap or {}).get("claims", []):
            res = measure(S, gid, c, places, fp)
            calls.append({"id": c["id"], "kind": c["kind"], "head": c["head"], "body": c["body"], "cite": c["cite"], **res})
            if res.get("verdict") in ("held", "partly", "missed"):
                tally[res["verdict"]] += 1
                by_kind[c["kind"]][res["verdict"]] += 1
        rec = {"game_id": gid, "date": g["date"], "start": g["start_utc"], "away": g["away"], "home": g["home"], "venue": g["venue"],
               "score": {g["away"]: g["away_score"], g["home"]: g["home_score"]}, "end": g["last_period"],
               "preview": snap is not None, "snapshot_at": snap["snapshot_at"] if snap else None, "calls": calls,
               "numbers": nums, "goalies": glines, "surprises": surprises(S, gid, nums, places, fp, usual_lines, glines),
               "deserve": deserve_block(meter[gid], g["away"], g["home"]) if gid in meter else None}
        (out_dir / f"{gid}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        graded = [c["verdict"] for c in calls if c.get("verdict") in ("held", "partly", "missed")]
        index.append({"id": gid, "date": g["date"], "away": g["away"], "home": g["home"], "score": rec["score"], "end": g["last_period"],
                      "calls": len(graded), "held": graded.count("held"), "partly": graded.count("partly"), "missed": graded.count("missed")})
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season,
               "tally": {k: tally[k] for k in ("held", "partly", "missed")}, "by_kind": {k: dict(v) for k, v in by_kind.items()}, "games": index}
    (out_dir / "index.json").write_text(json.dumps(summary, separators=(",", ":")))
    return {"recaps": len(index), "graded": sum(tally.values())}
