"""Series context for playoff previews: the series score, who has last change, what happened earlier in the series,
what changed in the lineups, past playoff meetings, and how teams in the same spot have done (told as history,
never as a chance for this series: Sora, 2026-10-13, "history works").
"""
import json
from functools import lru_cache
from pathlib import Path

import polars as pl

from pipeline.config import RAW, TABLES
from pipeline.playoffs import series as S

HISTORY = json.loads((Path(__file__).parent / "series_history.json").read_text())
ROUND_WORDS = {1: "first round", 2: "second round", 3: "conference final", 4: "Stanley Cup Final"}
NUM = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def series_state(g: dict, sched: list[dict]) -> dict:
    """The series as it stands before game `g` (a schedule row with type 3)."""
    away, home = g["away"], g["home"]
    sb = g["sb"]  # [home wins, away wins]... see below
    # schedule rows carry the score before the game as [this game's home, this game's away]
    hw, aw = sb
    games = [x for x in sched if x.get("series") == g["series"] and x["game"] < g["game"]]
    lead = home if hw > aw else away if aw > hw else None
    hi, lo = max(hw, aw), min(hw, aw)
    state = {"id": g["series"], "round": g["round"], "round_name": S.ROUND_NAMES[g["round"]], "game": g["game"],
             "wins": {home: hw, away: aw}, "last_change": home, "leader": lead}
    if hi == 3:
        state["clinch"] = lead
        if lo < 3:
            state["elimination"] = home if lead == away else away
        else:
            state["game7"] = True
    state["label"] = f"Game {g['game']}" + (f" · {S.score_words(home, away, [hw, aw])}" if games else "")
    state["history"] = history_line(hi, lo, lead, g)
    state["so_far"] = [{"game": x["game"], "home": x["home"], "away": x["away"], "hs": x["hs"], "as": x["as"], "end": x["end"],
                        "ot": x.get("ot", 0), "id": x["id"]} for x in sorted(games, key=lambda x: x["game"])]
    return state


def history_line(hi: int, lo: int, lead: str | None, g: dict) -> str | None:
    """How teams in this spot have done since 1998, as a count of past teams (never this team's chance)."""
    if hi == lo == 3:
        h = HISTORY["game7_home"]
        return f"Home teams have won {h['won']} of {h['games']} Game 7s since {HISTORY['since']}."
    if lead is None:
        return None
    s = HISTORY["states"].get(f"{hi}-{lo}")
    if not s:
        return None
    if hi == 3 and lo == 0:
        lost = s["series"] - s["leader_won"]
        return f"Of {s['series']} teams up 3-0 since {HISTORY['since']}, {s['leader_won']} won the series; {NUM.get(lost, lost)} didn't."
    return f"Of {s['series']} teams up {hi}-{lo} since {HISTORY['since']}, {s['leader_won']} went on to win the series."


@lru_cache(maxsize=2)
def _dressed(season: int) -> pl.DataFrame:
    return pl.read_parquet(TABLES / str(season) / "players.parquet")


def lineup_changes(season: int, team_id: int | None, team: str, prev_ids: list[int]) -> dict | None:
    """Who came in and who went out between the team's last two games (its last game against the one before)."""
    if len(prev_ids) < 2 or team_id is None:
        return None
    p = _dressed(season)
    a, b = prev_ids[-2], prev_ids[-1]
    def names(gid):
        rows = p.filter((pl.col("game_id") == gid) & (pl.col("team_id") == team_id))
        return {r["player_id"]: (f'{r["first"]} {r["last"]}', r["pos"]) for r in rows.iter_rows(named=True)}
    before, now = names(a), names(b)
    if not before or not now:
        return None
    ins = [{"id": k, "name": v[0], "pos": v[1]} for k, v in now.items() if k not in before]
    outs = [{"id": k, "name": v[0], "pos": v[1]} for k, v in before.items() if k not in now]
    if not ins and not outs:
        return {"team": team, "same": True}
    return {"team": team, "in": ins, "out": outs}


def chances_by_game(season: int, ids: list[int]) -> dict[int, dict]:
    """Each earlier series game's 5-on-5 chances (expected goals), all-situation chances and shots, by team id."""
    if not ids:
        return {}
    tg = pl.read_parquet(TABLES / str(season) / "team_game.parquet").filter(pl.col("game_id").is_in(ids))
    out = {}
    for (gid, tid), rows in tg.group_by(["game_id", "team_id"]):
        e = rows.filter(pl.col("strength") == "5v5")
        allx = rows.filter(~pl.col("strength").is_in(["EN", "PS"]))
        out.setdefault(gid, {})[tid] = {"xgf5": round(float(e["xgf"].sum()), 2), "xga5": round(float(e["xga"].sum()), 2),
                                        "xgf": round(float(allx["xgf"].sum()), 2), "sf": int(rows.filter(pl.col("strength") != "PS")["sf"].sum())}
    return out


def past_meetings(a_id: int, b_id: int, before_season: int) -> list[dict]:
    """Earlier playoff series between the two franchises since 1997-98 (brackets saved by ingest/history.py), newest
    first. Franchises, not names: the Jets' meetings include the Thrashers'."""
    fr = _franchise_of()
    fa, fb = fr.get(a_id), fr.get(b_id)
    out = []
    for f in sorted((RAW / "history" / "bracket").glob("*.json"), reverse=True):
        year = int(f.stem)  # the spring
        if year > before_season:
            continue
        for s in json.loads(f.read_text()).get("series", []):
            w, l = fr.get(s.get("winningTeamId")), fr.get(s.get("losingTeamId"))
            if {w, l} == {fa, fb} and fa != fb:
                out.append({"year": year, "round": s["playoffRound"], "winner_is_a": w == fa})
    return out


@lru_cache(maxsize=1)
def _franchise_of() -> dict[int, int]:
    return {r["id"]: r["franchiseId"] for r in json.loads((RAW / "history" / "teams.json").read_text())}


def add_to_preview(snap: dict, g: dict, sched: list[dict], season: int) -> dict:
    """Series facts for a playoff preview (saved in the snapshot, like everything else the page reads)."""
    st = series_state(g, sched)
    ids = [x["id"] for x in st["so_far"]]
    games = pl.read_parquet(TABLES / str(season) / "games.parquet")
    tid = {r["home"]: r["home_id"] for r in games.iter_rows(named=True)} | {r["away"]: r["away_id"] for r in games.iter_rows(named=True)}
    per = chances_by_game(season, ids)
    for x in st["so_far"]:
        c = per.get(x["id"], {})
        x["chances"] = {t: c.get(tid[t]) for t in (x["home"], x["away"]) if c.get(tid[t])}
    changes = []
    for t in (g["away"], g["home"]):
        mine = [x["id"] for x in sched if x["final"] and x["start"] < g["start"] and t in (x["home"], x["away"])]
        ch = lineup_changes(season, tid.get(t), t, mine)
        if ch and not ch.get("same") and len(st["so_far"]) >= 1:
            changes.append(ch)
    st["changes"] = changes
    met = past_meetings(tid[g["away"]], tid[g["home"]], season)  # springs up to and including `season`'s start year
    st["met"] = [{"year": m["year"], "round": m["round"], "winner": g["away"] if m["winner_is_a"] else g["home"]} for m in met][:3]
    snap["series"] = st
    # The header's records stay the regular season's; the series score is shown beside them.
    snap["pregame"]["records"] = regular_records(sched, (g["away"], g["home"]))
    if snap.get("story"):
        snap["story"] = series_story(snap["story"], st, g, sched, season, snap.get("claims"))
    return snap


REGULAR_ONLY = {"Slow start", "Leads the way"}  # "still looking for his first goal of the season" reads wrong in May
RESTED = ("latest game", "last two games", "last three games", "last four games")
NEXT = {1: "the second round", 2: "the conference final", 3: "the Stanley Cup Final", 4: "a Stanley Cup"}
GENERIC_HEADS = ("Round two", "come in hot", "need a win")


def series_story(story: dict, st: dict, g: dict, sched: list[dict], season: int, claims: list[dict] | None = None) -> dict:
    """The regular-season story, retold for a series: the first paragraph (meetings and form this season) becomes
    the series so far; storylines told in an earlier game of the series are not told again."""
    from pipeline.config import SITE_DATA
    from pipeline.export import stories, wording
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    W = stories.Words(wording.places(places))
    away, home, n = g["away"], g["home"], g["game"]
    sents = []
    if n == 1:
        reg = [x for x in sched if x.get("type") != 3 and x["final"] and {x["home"], x["away"]} == {away, home}]
        if reg:
            wins = {t: sum(1 for x in reg if (x["hs"] > x["as"]) == (x["home"] == t)) for t in (away, home)}
            if 0 in wins.values():
                w = home if wins[home] else away
                sents.append(f"{stories._cap(W.the(w))} won all {NUM.get(len(reg), len(reg))} meetings in the regular season.")
            elif wins[away] == wins[home]:
                sents.append(f"They split {NUM.get(len(reg), len(reg))} meetings in the regular season.")
            else:
                w = max(wins, key=wins.get)
                sents.append(f"{stories._cap(W.the(w))} won {NUM.get(wins[w], wins[w])} of {NUM.get(len(reg), len(reg))} meetings in the regular season.")
        rec = regular_records(sched, (away, home))
        sents.append(f"{stories._cap(W.the(home))} finished {rec[home]} and have home ice; {W.the(away)} finished {rec[away]}.")
        if st["met"]:
            m = st["met"][0]
            sents.append(f"Their last playoff meeting was in the {ROUND_WORDS[m['round']]} in {m['year']}, and {W.the(m['winner'])} won it.")
        else:
            sents.append("They haven’t met in the playoffs since at least 1998.")
    else:
        last = st["so_far"][-1]
        lw = last["home"] if last["hs"] > last["as"] else last["away"]
        hi, lo = max(last["hs"], last["as"]), min(last["hs"], last["as"])
        ot = {0: "", 1: " in overtime", 2: " in double overtime", 3: " in triple overtime"}.get(last.get("ot", 0), " in overtime")
        wins = st["wins"]
        if st["leader"]:
            L = st["leader"]
            score = f"{max(wins.values())}-{min(wins.values())}"
            sents.append(f"{stories._cap(W.the(L))} lead the series {score}" +
                         (f" after winning Game {last['game']} {hi}-{lo}{ot}." if lw == L else f", though {W.the(lw)} took Game {last['game']} {hi}-{lo}{ot}."))
        else:
            sents.append(f"The series is tied {wins[home]}-{wins[away]} after {W.the(lw)} won Game {last['game']} {hi}-{lo}{ot}.")
        if last["home"] != home:
            sents.append(f"It moves to {W.place(home)}, where {W.the(home)} have last change.")
        # the series in chances: 5-on-5 expected goals over the games so far
        tot = {t: sum((x.get("chances") or {}).get(t, {}).get("xgf5", 0) for x in st["so_far"]) for t in (away, home)}
        if sum(tot.values()) > 0:
            share = tot[home] / sum(tot.values())
            top = home if share >= 0.5 else away
            pct = round(100 * max(share, 1 - share))
            if pct >= 55:
                sents.append(f"Over {NUM.get(len(st['so_far']), len(st['so_far']))} games {W.the(top)} have had {pct}% of the 5-on-5 chances.")
            else:
                sents.append("At 5-on-5 the chances have been close to even so far.")
        if st.get("game7"):
            sents.append(f"Game 7: the winner goes on to {NEXT[g['round']]}.")
        elif st.get("clinch"):
            sents.append(f"A win sends {W.the(st['clinch'])} to {NEXT[g['round']]}; {W.the(st['elimination'])} need it to stay alive.")
        if st.get("history"):
            sents.append(st["history"])
    paras = [" ".join(sents)] + story.get("paras", [])[1:]
    items = list(story.get("items", []))
    if g["round"] == 1 and n == 1:  # clubs rest their regulars in the last games of the season: that isn't news
        items = [i for i in items if not (i["tag"] == "Missing" and any(w in i["text"] for w in RESTED))]
    lead = series_leader(season, [x["id"] for x in st["so_far"]], W)
    if lead:
        items.insert(0, lead)
    options = head_options(story.get("head", ""), st, g, W, claims or [])
    head = options[0]
    kicker = f"{st['round_name']}, Game {n}"
    # every storyline is kept here; `dedupe` (run once all previews exist) drops those told earlier in the series
    return {**story, "kicker": kicker, "head": head, "head_options": options, "paras": paras, "items": items}


def head_options(head: str, st: dict, g: dict, W, claims: list[dict]) -> list[str]:
    """Headlines for a playoff preview, best first; `dedupe` takes the first one not used earlier in the series
    (Sora's test build: "Suzuki's favorite opponent" ran before four straight games). A Game 7 or a clinching game
    always leads with that; regular-season form ("come in hot") is replaced by the series itself."""
    from pipeline.export import stories
    cap = lambda t: stories._cap(W.the(t))
    away, home, n = g["away"], g["home"], g["game"]
    at = W.at(home)
    if st.get("game7"):
        return [f"Game 7 {at}", "One game for the series"]
    if st.get("clinch"):
        c, e = st["clinch"], st["elimination"]
        if c == home:
            return [f"{cap(c)} can close it out {at}", f"{cap(c)} get another chance to close it out", f"{cap(e)} face elimination again"]
        return [f"{cap(e)} face elimination", f"{cap(e)} face elimination again", f"{cap(e)} have to win {at} again"]
    generic = any(h in head for h in GENERIC_HEADS)
    if n == 1:
        opener = f"{cap(away)} and {W.the(home)} open the {ROUND_WORDS[g['round']]}"
        return [opener] if generic or not head else [head, opener]
    out = []
    wins, lead = st["wins"], st["leader"]
    hi, lo = max(wins.values()), min(wins.values())
    trail = (away if lead == home else home) if lead else None
    tot = {t: sum((x.get("chances") or {}).get(t, {}).get("xgf5", 0) for x in st["so_far"]) for t in (away, home)}
    if lead and sum(tot.values()) > 0 and tot[trail] / sum(tot.values()) >= 0.55:
        out.append(f"{cap(trail)} have the chances, {W.the(lead)} have the wins")
    if head and not generic:
        out.append(head)
    last = st["so_far"][-1]
    if last.get("ot"):
        out.append(f"Game {n} after overtime in Game {last['game']}")
    if lead is None:
        out += [f"All square going into Game {n}", f"Tied {hi}-{lo}, and it’s back {at}" if last["home"] == home else f"Tied {hi}-{lo} as the series moves {at}"]
    elif hi - lo >= 2:
        out += [f"{cap(trail)} need an answer", f"{cap(lead)} up {hi}-{lo}"]
    else:
        out += [f"{cap(lead)} up {hi}-{lo}", f"{cap(trail)} try to draw level {at}"]
    out += [c["head"] for c in claims if c.get("head")]
    return out


def _family(head: str) -> str:
    """Headlines of one kind count as the same headline within a series."""
    for k in ("favorite opponent", "come in hot", "need a win"):
        if k in head:
            return k
    return head


def dedupe(sched: list[dict], folder: Path) -> int:
    """Storylines told before an earlier game of the same series are not told again (only the first few words are
    compared, since the counts move). Runs over the saved previews in game order; each keeps at most five items."""
    told: dict[str, set] = {}
    heads_used: dict[str, set] = {}
    n = 0
    for g in sorted((x for x in sched if x.get("type") == 3), key=lambda x: (x["series"], x["game"])):
        f = folder / f"{g['id']}.json"
        if not f.exists():
            continue
        snap = json.loads(f.read_text())
        story = snap.get("story") or {}
        items = story.get("all_items", story.get("items", []))
        seen = told.setdefault(g["series"], set())
        heads = heads_used.setdefault(g["series"], set())
        options = story.get("head_options") or [story.get("head", "")]
        story["head"] = next((h for h in options if _family(h) not in heads), options[0])
        heads.add(_family(story["head"]))
        keep = [i for i in items if i["tag"] not in REGULAR_ONLY and (i["tag"] == "Series leader" or (i["tag"], i["text"][:28]) not in seen)]
        seen |= {(i["tag"], i["text"][:28]) for i in items}
        story["all_items"] = items
        story["items"] = keep[:5]
        snap["story"] = story
        f.write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
        n += 1
    return n


def series_leader(season: int, ids: list[int], W) -> dict | None:
    """The series' top scorer so far, when he has three or more points."""
    if not ids:
        return None
    from pipeline.export import stories
    _, P, N = stories.book()
    r = P.filter(pl.col("game_id").is_in(ids)).group_by("player_id").agg(pl.col("pts").sum(), pl.col("g").sum()).sort(
        ["pts", "g", "player_id"], descending=[True, True, False])
    if r.is_empty() or r["pts"][0] < 3:
        return None
    pid, pts, g = r.row(0)
    pts, g = int(pts), int(g)
    return {"tag": "Series leader", "text": f"{N.get(pid, '?')} has {pts} points ({g} goal{'' if g == 1 else 's'}) in the series."}


def regular_records(sched: list[dict], teams) -> dict[str, str]:
    rec = {}
    for g in sched:
        if g.get("type") == 3 or not g["final"]:
            continue
        win, lose = (g["home"], g["away"]) if g["hs"] > g["as"] else (g["away"], g["home"])
        rec.setdefault(win, [0, 0, 0])[0] += 1
        rec.setdefault(lose, [0, 0, 0])[1 if g["end"] == "REG" else 2] += 1
    return {t: "-".join(map(str, rec.get(t, [0, 0, 0]))) for t in teams}


SERIES_MIN_MINUTES = 5  # a group's 5-on-5 time against the opponent in this series' home games before its habit counts


def series_matching(season: int, home: str, home_id: int, hosted: list[int], h_units: list[dict], a_units: list[dict]) -> dict:
    """Who tonight's home lines and pairs faced in the home team's earlier home games of this series, as the same
    block `units.matchups` gives for a whole season ({games, rows: [{label, minutes, share, expected}]}). Groups are
    tonight's lines on both sides (two of three forwards, or one of two defensemen, make a group that line)."""
    from collections import defaultdict
    d = TABLES / str(season)
    pos = {(r["game_id"], r["player_id"]): r["pos"] for r in
           pl.read_parquet(d / "players.parquet").filter(pl.col("game_id").is_in(hosted)).iter_rows(named=True)}
    own = {u["label"]: set(u["ids"]) for u in h_units if u["label"][0] in "LP"}
    opp = {u["label"]: set(u["ids"]) for u in a_units if u["label"][0] == "L"}

    def label(sets: dict, players: set, kind: str) -> str | None:
        best = max(((len(m & players), lab) for lab, m in sets.items() if lab[0] == kind), default=(0, None))
        return best[1] if best[0] >= (2 if kind == "L" else 1) else None

    cells = defaultdict(float)
    st = pl.read_parquet(d / "stints.parquet").filter(pl.col("game_id").is_in(hosted))
    for s in st.iter_rows(named=True):
        if not (s["n_home"] == 5 and s["n_away"] == 5 and s["home_goalie"] and s["away_goalie"]):
            continue
        gid = s["game_id"]
        fwd = lambda sk: {p for p in sk if pos.get((gid, p)) in ("C", "L", "R")}
        hf, hd, af = fwd(s["home_skaters"]), {p for p in s["home_skaters"] if pos.get((gid, p)) == "D"}, fwd(s["away_skaters"])
        o = label(opp, af, "L") if len(af) == 3 else None
        if o is None:
            continue
        for mine in (label(own, hf, "L") if len(hf) == 3 else None, label(own, hd, "P") if len(hd) == 2 else None):
            if mine:
                cells[(mine, o)] += s["duration"]
    cols = ["L1", "L2", "L3", "L4"]
    rows = []
    for kind, labels in (("L", cols), ("P", ["P1", "P2", "P3"])):
        total = sum(v for (m, _), v in cells.items() if m[0] == kind)
        expected = [sum(v for (m, c2), v in cells.items() if m[0] == kind and c2 == c) / total * 100 if total else 0.0 for c in cols]
        for r in labels:
            sec = sum(cells.get((r, c), 0.0) for c in cols)
            rows.append({"label": r, "minutes": round(sec / 60, 1), "seconds": round(sec),
                         "share": [round(100 * cells.get((r, c), 0.0) / sec) if sec else None for c in cols], "expected": [round(x) for x in expected]})
    return {"games": len(hosted), "rows": rows}


def series_matchup_claim(regular, g: dict, sched: list[dict], season: int, tids: dict[str, int]):
    """A stand-in for `previews.matchup_claim` in playoff previews. Before the home team has hosted this opponent in
    the series, the call is the regular-season home habit, as before. Once it has, the call reads only what the coach
    did in this series' home games (Sora, 2026-10-13: regular-season habits held 31 of 66); when no line was matched
    there, there is no matchup call."""
    from pipeline.export import previews

    def claim(gid, away, home, lines, places, pts=None, coach=None):
        hosted = [x["id"] for x in sched if x.get("series") == g["series"] and x["final"] and x["home"] == home and x["game"] < g["game"]]
        if not hosted or not lines.get(home) or not lines.get(away):
            return regular(gid, away, home, lines, places, pts, coach)
        block = series_matching(season, home, tids[home], hosted, lines[home]["units"], lines[away]["units"])
        mine = {**lines, home: {**lines[home], "matchups": {**lines[home].get("matchups", {}), "home": block}}}
        keep = previews.MIN_HOME_GAMES, previews.MIN_MINUTES
        previews.MIN_HOME_GAMES, previews.MIN_MINUTES = 1, SERIES_MIN_MINUTES
        try:
            c = regular(gid, away, home, mine, places, pts, coach)
        finally:
            previews.MIN_HOME_GAMES, previews.MIN_MINUTES = keep
        if not c:
            return None
        k = len(hosted)
        where = previews.team_words(home, places, "H")["Hc"]
        games = "the one game" if k == 1 else f"the {NUM.get(k, k)} games"
        first, _, rest = c["body"].partition(". ")
        share, exp = c["check"]["baseline"], c["check"]["threshold"]
        ordn = previews.ORD[int(c["check"]["opp_line"][1:]) - 1]
        opp_w = previews.team_words(away, places, "O")
        group = c["call"].split(" spends ")[0]
        group = group[0].lower() + group[1:] if group.startswith("The ") else group
        c["body"] = (f"In {games} in {where} this series, {group} spent {share}% of its 5-on-5 time against "
                     f"{opp_w['Op']} {ordn} line, where about {exp}% would be normal with no line matching. {rest}")
        c["body"] = c["body"][0].upper() + c["body"][1:]
        c["nums"] = f"This series in {where}: {share}% of this group’s 5-on-5 time against the {ordn} line, about {exp}% with no line matching"
        c["gist"] = f"In {where} this series, {coach or 'their coach'} has been picking matchups: watch who goes out against {opp_w['Op']} {ordn} line."
        c["cite"] = f"Who plays against whom · {where} home games in this series · {k} game{'' if k == 1 else 's'}, {c['cite'].split(', ')[-1]}"
        c["check"]["series"] = True
        c["check"]["rule"] = "held if the share is at or above what the coach did earlier in the series; partly if above the no-matching level; otherwise didn't happen"
        return c
    return claim
