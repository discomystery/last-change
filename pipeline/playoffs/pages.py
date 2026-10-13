"""Series files for the playoff pages: the bracket (`series/index.json`) and one file per series (`series/{id}.json`),
built from the schedule, the frozen previews and the post-game files. Also retells each playoff post-game story's
"What it means" list as the series it belongs to (the regular season's "season series" and "record" lines don't
apply).

Never a chance for a series or a game: series history is told as counts of past teams ("Of 203 teams up 2-0 since
1998, 174 won the series"), which Sora approved on 2026-10-13.
"""
import json
from collections import defaultdict

import polars as pl

from pipeline.config import SITE_DATA, TABLES
from pipeline.playoffs import context as C
from pipeline.playoffs import series as S

VERDICTS = ("held", "partly", "missed")


def _load(kind: str, gid: int) -> dict | None:
    f = SITE_DATA / kind / f"{gid}.json"
    return json.loads(f.read_text()) if f.exists() else None


def _five(season: int, ids: list[int], tid: dict) -> dict[int, dict]:
    """5-on-5 expected goals for and against, each team, each game."""
    per = C.chances_by_game(season, ids)
    out = {}
    for gid, by in per.items():
        out[gid] = {abbr: by.get(t) for abbr, t in tid.items() if by.get(t)}
    return out


def _scorers(season: int, ids: list[int], names: dict, abbr_of: dict) -> list[dict]:
    if not ids:
        return []
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet", columns=["game_id", "player_id", "team_id", "pos", "g", "a1", "a2"])
    r = (pg.filter(pl.col("game_id").is_in(ids) & (pl.col("pos") != "G"))
         .group_by("player_id", "team_id").agg(pl.col("g").sum(), (pl.col("a1") + pl.col("a2")).sum().alias("a"), pl.len().alias("gp"))
         .with_columns(p=pl.col("g") + pl.col("a")).sort(["p", "g", "player_id"], descending=[True, True, False]).head(6))
    return [{"id": x["player_id"], "name": names.get(x["player_id"], "?"), "team": abbr_of.get(x["team_id"]), "gp": int(x["gp"]),
             "g": int(x["g"]), "a": int(x["a"]), "p": int(x["p"])} for x in r.iter_rows(named=True) if x["p"] > 0]


def _goalies(recaps: list[dict]) -> list[dict]:
    tot = defaultdict(lambda: {"gp": 0, "starts": 0, "shots": 0, "saves": 0, "xga": 0.0, "ga": 0, "w": 0})
    for r in recaps:
        winner = max(r["score"], key=r["score"].get)
        for g in r.get("goalies", []):
            t = tot[(g["id"], g["name"], g["team"])]
            t["gp"] += 1
            t["starts"] += int(bool(g["started"]))
            t["shots"] += g["shots"]
            t["saves"] += g["saves"]
            t["ga"] += g["ga"]
            t["xga"] += g["xga"]
            t["w"] += int(bool(g["started"]) and g["team"] == winner)
    out = []
    for (pid, name, team), t in tot.items():
        out.append({"id": pid, "name": name, "team": team, **t, "xga": round(t["xga"], 2), "gsax": round(t["xga"] - t["ga"], 2),
                    "sv": round(t["saves"] / t["shots"], 3) if t["shots"] else None})
    return sorted(out, key=lambda x: (x["team"], -x["shots"]))


def _swing_star(gid: int) -> dict | None:
    sc = _load("scores", gid)
    if not sc or not sc.get("players"):
        return None
    p = max(sc["players"], key=lambda p: p["score"])
    return {"id": p["id"], "name": p["name"], "team": p["team"], "score": p["score"]}


def _calls_across(series_games: list[dict]) -> list[dict]:
    """Every graded call made in the series, grouped by what it was about (kind, metric, the team it backed), with
    how often it held. The same edge called before Games 1 to 6 is one row with six results."""
    rows = {}
    for x in series_games:
        snap, rec = _load("previews", x["id"]), _load("recaps", x["id"])
        if not snap or not rec:
            continue
        verdict = {c["id"]: c for c in rec.get("calls", [])}
        for c in snap.get("claims", []):
            v = verdict.get(c["id"], {})
            if v.get("verdict") not in VERDICTS:
                continue
            key = (c["kind"], c.get("metric"), c.get("team"))
            row = rows.setdefault(key, {"kind": c["kind"], "metric": c.get("metric"), "team": c.get("team"), "head": c["head"],
                                         "games": []})
            row["games"].append({"game": x["game"], "id": x["id"], "verdict": v["verdict"], "title": v.get("title"), "happened": v.get("happened")})
    out = sorted(rows.values(), key=lambda r: (-len(r["games"]), r["games"][0]["game"]))
    for r in out:
        r["held"] = sum(g["verdict"] == "held" for g in r["games"])
        r["partly"] = sum(g["verdict"] == "partly" for g in r["games"])
    return out


def _story(s: dict, places: dict, games: list[dict], five: dict) -> dict:
    """How the series went (or is going), in a few sentences: the result, the comebacks, overtime, and the chances."""
    from pipeline.export import stories, wording
    W = stories.Words(wording.places(places))
    top, bot = s["top"], s["bottom"]
    done = [g for g in games if g["final"]]
    sents = []
    if not done:
        return {"head": f"{W.place(bot)} at {W.place(top)}", "paras": []}
    wins = dict(zip((top, bot), s["wins"]))
    if s["winner"]:
        w, l = s["winner"], s["loser"]
        n = len(done)
        head = f"{stories._cap(W.the(w))} {'sweep' if n == 4 else 'win in ' + C.NUM[n]}"
        trail = 0  # the most games the winner trailed by
        for g in done:
            a, b = (g["before"] if w == top else g["before"][::-1])
            trail = max(trail, b - a)
        sents.append(f"{stories._cap(W.the(w))} won the series {wins[w]}-{wins[l]}" + (f", coming back from {trail} games down." if trail >= 2 else
                                                                               f", after trailing in it." if trail == 1 else "."))
    else:
        lead = top if wins[top] > wins[bot] else bot if wins[bot] > wins[top] else None
        head = (f"{stories._cap(W.the(lead))} lead {max(wins.values())}-{min(wins.values())}" if lead else
                f"Tied {wins[top]}-{wins[bot]}")
        sents.append(f"{stories._cap(W.the(lead))} lead the series {max(wins.values())}-{min(wins.values())}." if lead else
                     f"The series is tied {wins[top]}-{wins[bot]}.")
    ot = [g for g in done if g.get("ot")]
    deep = {2: "double", 3: "triple", 4: "quadruple", 5: "quintuple"}
    if len(ot) == 1:
        g = ot[0]
        sents.append(f"Game {g['game']} went to {deep[g['ot']] + ' ' if g['ot'] >= 2 else ''}overtime.")
    elif ot:
        long = max(ot, key=lambda g: g["ot"])
        sents.append(f"{C.NUM[len(ot)].capitalize()} games went to overtime" +
                     (f", Game {long['game']} to {deep.get(long['ot'], 'multiple')} overtime." if long["ot"] >= 2 else "."))
    one = sum(1 for g in done if abs(g["hs"] - g["as"]) == 1)
    if one >= 3:
        sents.append(f"{C.NUM[one].capitalize()} were decided by one goal.")
    tot = {t: sum((five.get(g["id"], {}).get(t) or {}).get("xgf5", 0) for g in done) for t in (top, bot)}
    if sum(tot.values()):
        share = tot[top] / sum(tot.values())
        lead = top if share >= 0.5 else bot
        pct = round(100 * max(share, 1 - share))
        better = sum(1 for g in done if ((five.get(g["id"], {}).get(lead) or {}).get("xgf5", 0) > (five.get(g["id"], {}).get(lead) or {}).get("xga5", 0)))
        if pct >= 53:
            sents.append(f"At 5-on-5 {W.the(lead)} had {pct}% of the chances and the better of them in {C.NUM[better]} of {C.NUM[len(done)]} games.")
        else:
            sents.append(f"At 5-on-5 the chances were close to even ({pct}% to {100 - pct}%).")
        if s["winner"] and lead != s["winner"] and pct >= 53:
            sents.append(f"{stories._cap(W.the(s['winner']))} won it anyway.")
    return {"head": head, "paras": [" ".join(sents)]}


def recap_items(rec: dict, s: dict, g: dict, places: dict) -> list[dict]:
    """"What it means" for a playoff game: the series score after it, who is through or out, and how teams in the same
    spot have done (as history)."""
    from pipeline.export import stories, wording
    W = stories.Words(wording.places(places))
    top, bot = s["top"], s["bottom"]
    before = dict(zip((top, bot), g["before"]))
    after = dict(before)
    after[g["winner"]] += 1
    w = g["winner"]
    l = bot if w == top else top
    items = []
    if after[w] == 4:
        nxt = C.NEXT.get(s["round"])
        items.append({"tag": "Series", "text": f"{stories._cap(W.the(w))} win the series {after[w]}-{after[l]} and move on to {nxt}." if s["round"] < 4 else
                      f"{stories._cap(W.the(w))} win the Stanley Cup, {after[w]}-{after[l]} in the Final."})
        items.append({"tag": "Out", "text": f"{stories._cap(W.the(l))} are out of the playoffs."})
    else:
        hi, lo = max(after.values()), min(after.values())
        lead = w if after[w] > after[l] else l if after[l] > after[w] else None
        items.append({"tag": "Series", "text": (f"{stories._cap(W.the(lead))} lead the series {hi}-{lo}." if lead else f"The series is tied {hi}-{lo}.")})
        h = C.history_line(hi, lo, lead, g)
        if h:
            items.append({"tag": "History", "text": h})
        if hi == 3 and lo < 3:
            items.append({"tag": "Next", "text": f"{stories._cap(W.the(lead))} can close it out in Game {g['game'] + 1}."})
        elif hi == lo == 3:
            items.append({"tag": "Next", "text": "Game 7."})
        if lead and g["game"] == 1:
            st = C.HISTORY["states"]["1-0"]
            items[-1:] = [{"tag": "History", "text": f"Of {st['series']} teams that won Game 1 since {C.HISTORY['since']}, {st['leader_won']} won the series."}]
    return items


def run(season: int) -> dict:
    teams = json.loads((SITE_DATA / "teams.json").read_text())
    places = {t["abbr"]: t["place"] for t in teams}
    conf = {t["abbr"]: t["conference"] for t in teams}
    allseries = S.build(season, conf)
    games = pl.read_parquet(TABLES / str(season) / "games.parquet")
    tid = {r["home"]: r["home_id"] for r in games.iter_rows(named=True)} | {r["away"]: r["away_id"] for r in games.iter_rows(named=True)}
    abbr_of = {v: k for k, v in tid.items()}
    names = {r["player_id"]: f"{r['first']} {r['last']}" for r in pl.read_parquet(TABLES / str(season) / "players.parquet").sort("game_id").iter_rows(named=True)}
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    out_dir = SITE_DATA / "series"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    fixed = 0
    for s in allseries:
        ids = [g["id"] for g in s["games"] if g["final"]]
        five = _five(season, ids, {t: tid[t] for t in (s["top"], s["bottom"])})
        recaps = [r for r in (_load("recaps", i) for i in ids) if r]
        gl = []
        for g in s["games"]:
            rec = _load("recaps", g["id"]) if g["final"] else None
            snap = _load("previews", g["id"])
            row = {**g, "five": five.get(g["id"]), "preview": snap is not None, "recap": rec is not None}
            if rec:
                verdicts = [c.get("verdict") for c in rec.get("calls", [])]
                row["calls"] = {v: verdicts.count(v) for v in VERDICTS}
                if rec.get("replays"):
                    row["replay"] = rec["replays"]["share"]
                row["star"] = _swing_star(g["id"])
                row["shots"] = {t: rec["numbers"][t]["shots"] for t in rec["numbers"]}
                row["head"] = (rec.get("story") or {}).get("head")
                # the post-game story's "What it means", told as the series
                story = rec.get("story")
                if story is not None and g.get("winner"):
                    story["items"] = recap_items(rec, s, g, places)
                    rec["series"] = {"id": s["id"], "round": s["round"], "round_name": s["round_name"], "game": g["game"],
                                     "label": f"{s['round_name']}, Game {g['game']}"}
                    (SITE_DATA / "recaps" / f"{g['id']}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
                    fixed += 1
            if snap and snap.get("story"):
                row["preview_head"] = snap["story"].get("head")
            gl.append(row)
        g1 = _load("previews", s["games"][0]["id"])
        before = {}
        if g1:
            ser = g1.get("series", {})
            reg = [x for x in sched if x.get("type") != 3 and x["final"] and {x["home"], x["away"]} == {s["top"], s["bottom"]}]
            fp = g1["pregame"]["fingerprints"]["teams"]
            before = {"records": g1["pregame"].get("records"), "met": ser.get("met", []),
                      "meetings": [{"id": x["id"], "date": x["date"], "home": x["home"], "away": x["away"], "hs": x["hs"], "as": x["as"], "end": x["end"]} for x in reg],
                      "claims": [{"id": c["id"], "kind": c["kind"], "head": c["head"], "body": c["body"], "team": c.get("team")} for c in g1.get("claims", [])],
                      "projected": g1.get("projected"), "story": g1.get("story"), "game1": g1["game_id"],
                      "gaps": _gaps(fp, s["top"], s["bottom"])}
        rec_series = {**{k: v for k, v in s.items() if k != "games"}, "games": gl, "before": before,
                      "story": _story(s, places, s["games"], five),
                      "five": {t: {"xgf5": round(sum((five.get(i, {}).get(t) or {}).get("xgf5", 0) for i in ids), 2)} for t in (s["top"], s["bottom"])},
                      "replay": {t: round(sum((g.get("replay") or {}).get(t, 0) for g in gl), 2) for t in (s["top"], s["bottom"])} if any(g.get("replay") for g in gl) else None,
                      "scorers": _scorers(season, ids, names, abbr_of), "goalies": _goalies(recaps), "calls": _calls_across(s["games"])}
        tally = defaultdict(int)
        for g in gl:
            for v, n in (g.get("calls") or {}).items():
                tally[v] += n
        rec_series["tally"] = dict(tally)
        (out_dir / f"{s['id']}.json").write_text(json.dumps(rec_series, separators=(",", ":"), ensure_ascii=False))
        index.append({k: rec_series[k] for k in ("id", "letter", "round", "round_name", "conference", "top", "bottom", "wins", "winner", "loser")}
                     | {"games": [{"id": g["id"], "game": g["game"], "date": g["date"], "home": g["home"], "away": g["away"], "final": g["final"],
                                   "hs": g.get("hs"), "as": g.get("as"), "ot": g.get("ot", 0), "winner": g.get("winner")} for g in gl],
                        "head": rec_series["story"]["head"], "tally": rec_series["tally"]})
    (out_dir / "index.json").write_text(json.dumps({"season": season, "spring": season + 1, "series": index}, separators=(",", ":"), ensure_ascii=False))
    return {"series": len(index), "recaps_retold": fixed}


def _gaps(fp: dict, a: str, b: str, n: int = 6) -> list[dict]:
    """The traits where the two teams sit furthest apart (blended percentiles), for the series preview."""
    da, db = (fp.get(a) or {}).get("dims", {}), (fp.get(b) or {}).get("dims", {})
    out = []
    for k in da:
        x, y = (da.get(k) or {}).get("blend"), (db.get(k) or {}).get("blend")
        if not x or not y or x.get("pct") is None or y.get("pct") is None:
            continue
        out.append({"trait": k, a: {"pct": x["pct"], "rank": x.get("rank")}, b: {"pct": y["pct"], "rank": y.get("rank")}, "gap": abs(x["pct"] - y["pct"])})
    return sorted(out, key=lambda r: -r["gap"])[:n]


if __name__ == "__main__":
    from pipeline.config import CURRENT_SEASON
    print(json.dumps(run(CURRENT_SEASON)))
