"""Fact packets: everything a writer may use about one game, and nothing of the generator's wording.

A recap packet is built from the same tables and post-game JSON the story generator reads: the goals in order (from
`stories._goals`), period lines, team numbers, goalies, the replay meter, records and standings before and after, each
player's line, and the structured finds of the detectors that return data (firsts, rare scorers). Detectors that only
return finished sentences (streaks, club history, standings swaps, injury follow-ups...) are passed on as `notes`:
true facts the writer may use, worded however the writer likes. The generator's own story is never in the packet.

A preview packet holds both clubs' form, earlier meetings, rest, likely goalies, who is out, and each call's measured
facts (the chips and the check's numbers), with the outlook and Who to watch sentences as `notes`. The hidden win
chance is never in a preview packet (it is never saved before puck drop)."""
import gzip
import json
from pathlib import Path

import polars as pl

from pipeline.config import RAW, SITE_DATA, TABLES
from pipeline.export import stories as S
from pipeline.export import penalties, textcheck

STRENGTH = {"ev": "even strength", "pp": "power play", "sh": "shorthanded", "en": "empty net", "ps": "penalty shot",
            "dp": "extra attacker during a delayed penalty"}


def clock(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def _teams() -> dict[str, dict]:
    return {t["abbr"]: t for t in json.loads((SITE_DATA / "teams.json").read_text())}


def _team(t: str, T: dict) -> dict:
    x = T.get(t, {})
    return {"abbr": t, "name": x.get("name"), "place": x.get("place"), "nickname": x.get("nick"),
            "division": x.get("division"), "conference": x.get("conference"), "coach": x.get("coach")}


def _form(res: list[dict], T: dict) -> dict:
    """Record, current run and last five, from `stories.results` rows (oldest first)."""
    kind, n = S.streak(res)
    last = [{"date": r["date"], "opp": r["opp"], "opp_name": T.get(r["opp"], {}).get("name"), "home": r["home"], "result": ("W" if r["win"] else "OTL" if r["end"] != "REG" else "L"),
             "score": f"{r['us']}-{r['them']}", "ended": r["end"]} for r in res[-5:]]
    return {"record": S.record(res) if res else "0-0-0", "games": len(res), "run": f"{kind} {n}" if n else None,
            "last_ten": S.record(res[-10:]) if res else None, "last_five": last}


def _standings(day: str, season: int) -> dict[str, dict]:
    f = RAW / "standings" / str(season) / f"{day}.json.gz"
    if not f.exists():
        return {}
    rows = json.loads(gzip.open(f).read())
    rows = sorted(rows, key=lambda s: (-s["points"], s["gamesPlayed"]))
    return {s["teamAbbrev"]["default"] if isinstance(s["teamAbbrev"], dict) else s["teamAbbrev"]:
            {"points": s["points"], "gp": s["gamesPlayed"], "division": s["divisionName"], "division_place": s["divisionSequence"],
             "conference_place": s["conferenceSequence"], "wild_card_place": s["wildcardSequence"] or None,
             "league_place_by_points": i + 1}
            for i, s in enumerate(rows)}


def _sentences(texts: list[str]) -> list[str]:
    return [s for t in texts for s in textcheck.sentences(t)]


def recap(gid: int, season: int = S.CURRENT_SEASON) -> dict:
    r = json.loads((SITE_DATA / "recaps" / f"{gid}.json").read_text())
    T = _teams()
    G, P, N = S.book()
    row = G.filter(pl.col("game_id") == gid).row(0, named=True)
    away, home, day = r["away"], r["home"], r["date"]
    goals = S._goals(gid, season, row["home_id"])
    name = lambda pid: N.get(pid) if pid else None
    tally: dict[int, int] = {}
    season_goals = {x["player_id"]: x["g"] for x in P.filter((pl.col("season") == season) & (pl.col("date") <= day))
                    .group_by("player_id").agg(pl.col("g").sum()).iter_rows(named=True)}
    gl = []
    for i, g in enumerate(goals):
        tally[g["p1"]] = tally.get(g["p1"], 0) + 1
        gl.append({"n": i + 1, "period": g["period"] if g["period"] <= 3 else "OT", "time_in_period": clock(g["sec"]),
                   "time_left_in_period": clock(max((1200 if g["period"] <= 3 else 300) - g["sec"], 0)),
                   "team": home if g["home"] else away, "scorer": name(g["p1"]), "scorer_goal_tonight": tally[g["p1"]],
                   "assists": [x for x in (name(g["p2"]), name(g["p3"])) if x], "strength": STRENGTH.get(g["strength"], g["strength"]),
                   "against_pulled_goalie": g["extra"], "own_goalie_pulled": g["own_extra"],
                   "score_after": {away: g["as"], home: g["hs"]},
                   "chance_of_scoring": round(100 * g["xg"]) if g.get("xg") is not None else None, "shot": g["shot"]})
    shootout = []
    if r.get("end") == "SO":
        ids = {row["home_id"]: home, row["away_id"]: away}
        shootout = [{"team": ids.get(x["tid"]), "shooter": name(x["pid"]), "scored": x["scored"]} for x in S._shootout(gid, season)]
    night = P.filter(pl.col("game_id") == gid)
    players = []
    for x in night.sort(["pts", "g", "sec"], descending=True).iter_rows(named=True):
        if x["pts"] or x["sec"] >= 18 * 60:
            players.append({"name": name(x["player_id"]), "team": x["team"], "pos": x["pos"], "goals": x["g"], "assists": x["a1"] + x["a2"],
                            "points": x["pts"], "toi": clock(int(x["sec"])), "season_goals_after": season_goals.get(x["player_id"])})
    before = G.filter(pl.col("date") < day)
    after = G.filter(pl.col("date") <= day)
    meetings = [m for m in S.results(before, home, season) if m["opp"] == away]
    reveal = r.get("reveal") or {}
    sides = reveal.get("sides") or {}
    found = []
    for x in S._found_firsts(gid, season, G, P):
        found.append({k: v for k, v in x.items() if k not in ("weight", "pid")} | {"player": name(x.get("pid"))})
    rare = [{"player": name(x["pid"]), "team": x["team"], "goals": x["goals"], "usual_games_per_goal": round(1 / x["lam"]) if x.get("lam") else None}
            for x in S._rare_scorers(P, night, day, N)]
    story = r.get("story") or {}
    paras = story.get("paras") or []
    notes = _sentences(paras[1:]) + [f"{i.get('tag')}: {i.get('text')}" for i in story.get("items") or []]
    return {
        "kind": "recap", "game_id": gid, "date": day, "venue": r.get("venue"),
        "away": _team(away, T), "home": _team(home, T),
        "final": {away: r["score"][away], home: r["score"][home], "ended": {"REG": "regulation", "OT": "overtime", "SO": "shootout"}.get(r.get("end"), r.get("end"))},
        "goals": gl, "shootout": shootout,
        "penalty_shots_stopped": [{"shooter": name(x["p1"]), "goalie": name(x["goalie"]), "period": x["period"], "time_in_period": clock(x["sec"])}
                                  for x in S._ps_stopped(gid, season)],
        "periods": r.get("periods"), "team_numbers": r.get("numbers"),
        "team_numbers_key": "shots = shots on goal, xg = expected goals (empty nets out), cf5 = 5-on-5 shot attempts, pp = power-play goals of chances",
        "goalies": [{k: x.get(k) for k in ("full", "team", "started", "toi", "shots", "saves", "ga", "gsax", "sv")} for x in r.get("goalies") or []],
        "players": players,
        "run_it_back": {"wins_out_of_100": {t: round(100 * v) for t, v in ((r.get("replays") or {}).get("share") or {}).items()}},
        "pregame_chance": {t: v.get("chance") for t, v in sides.items()},
        "records_before": {t: _form(S.results(before, t, season), T) for t in (away, home)},
        "records_after": {t: _form(S.results(after, t, season), T) for t in (away, home)},
        "earlier_meetings_this_season": [{"date": m["date"], "winner": home if m["win"] else away, "score": S.score_text(m)} for m in meetings],
        "standings_after": {t: v for t, v in _standings(day, season).items() if t in (away, home)},
        "firsts": found, "rare_scorers": rare,
        # what the chronology tells besides goals (the pilot's packets lacked these, so drafts missed the fights and
        # Boston's 20 minutes without a shot that the generator told)
        "fights": [{"period": min(sec // 1200 + 1, 4), "time_in_period": clock(sec % 1200), "fighters": [name(x) for x in who]}
                   for sec, who, _ in penalties.fight_events(gid, season, name)],
        "shot_droughts": [{"team": x.get("team"), "from": clock(x["start"]) + " of play", "to": clock(x["end"]) + " of play",
                           "length": clock(x["gap"]), "other_team_shots_meanwhile": x.get("other"), "rare": x.get("rare")}
                          for x in S.shot_droughts(gid, season, home, away)],
        "goals_taken_off": [{"team": home if x.get("home") else away, "why": x.get("why"), "period": x.get("period"),
                             "time_in_period": clock(int(x.get("sec") or 0)), "league_review": x.get("league")}
                            for x in S._overturned(gid, season)],
        "returns_to_old_club": [{"player": name(x["pid"]), "old_club": x.get("old"), "at_old_rink": x.get("at_old"), "goals": x.get("g")}
                                for x in S.old_club_returns(P, gid, day, home)],
        "surprises": [{"kind": x.get("kind"), "fact": x.get("body")} for x in r.get("surprises") or []],
        "notes": notes,
        "notes_rule": "True facts found by the detectors. Use any, in any order and in your own words, or leave them out.",
    }


def preview(gid: int, season: int = S.CURRENT_SEASON) -> dict:
    p = json.loads((SITE_DATA / "previews" / f"{gid}.json").read_text())
    T = _teams()
    G, P, N = S.book()
    away, home, day = p["away"], p["home"], (p.get("start") or "")[:10]
    before = G.filter(pl.col("date") < day)
    form = {t: _form(S.results(before, t, season), T) for t in (away, home)}
    meetings = [m for m in S.results(before, home, season) if m["opp"] == away]
    claims = []
    for c in p.get("claims") or []:
        chk = c.get("check") or {}
        claims.append({"kind": c.get("kind"), "about": c.get("team"),
                       "facts": [x.get("tip") or x.get("text") for x in c.get("chips") or []],
                       "what_we_check": chk.get("measure"), "usual": chk.get("baseline"), "league": chk.get("league"),
                       "direction": chk.get("direction")})
    story = p.get("story") or {}
    outlook = p.get("outlook") or {}
    notes = _sentences(list((outlook.get(home) or {}).get("body", "").split("\n"))) + \
        [f"{i.get('tag')}: {i.get('text')}" for i in story.get("items") or []]
    goalies = {t: N.get((v or {}).get("id")) for t, v in (p.get("projected") or {}).items()}
    return {
        "kind": "preview", "game_id": gid, "start_utc": p.get("start"), "venue": p.get("venue"),
        "away": _team(away, T), "home": _team(home, T), "form": form,
        "earlier_meetings_this_season": [{"date": m["date"], "winner": home if m["win"] else away, "score": S.score_text(m),
                                          "where": home if m["home"] else away} for m in meetings],
        "projected_goalies": goalies,
        "calls": claims,
        "calls_rule": "Each call is a measured matchup the page will grade after the game. Mention any; the numbers are in `facts`.",
        "notes": notes,
        "notes_rule": "True facts found by the detectors (who leans which way and why, who is out, storylines). Never state a win chance.",
    }


def generator_text(gid: int, kind: str) -> dict:
    """What the site says now, kept beside the draft (never shown to the writer)."""
    d = json.loads((SITE_DATA / ("recaps" if kind == "recap" else "previews") / f"{gid}.json").read_text())
    st = d.get("story") or {}
    out = {"head": st.get("head"), "paras": st.get("paras") or [], "items": st.get("items") or []}
    if kind == "preview":
        out["outlook"] = {t: v for t, v in (d.get("outlook") or {}).items()}
        out["calls"] = [{"head": c.get("head"), "body": c.get("body")} for c in d.get("claims") or []]
    return out


def write(folder: Path, gid: int, kind: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    pk = recap(gid) if kind == "recap" else preview(gid)
    (folder / f"{gid}.packet.json").write_text(json.dumps(pk, ensure_ascii=False, indent=1))
    (folder / f"{gid}.generator.json").write_text(json.dumps(generator_text(gid, kind), ensure_ascii=False, indent=1))
