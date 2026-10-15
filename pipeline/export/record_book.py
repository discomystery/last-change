"""Record-book notes for player pages (Sora, 2026-10-15: "Sebastian Aho ranks 2nd in all-time 3v3 overtime goals with
N, behind Leon Draisaitl with N").

Same lists as the post-game "NHL history" and "Club history" items, so a page never says something the records sites
don't back up:
- NHL all-time (ingest/records.league_update, top 100 each): career goals, assists and points (told inside the top
  LEAGUE_TOP), game-winning goals and overtime goals (top 25), overtime goals since 3-on-3 overtime began in 2015-16
  (top 10). Totals are the latest snapshot plus every game we have seen after the date it covers (league_lists.coverage);
  a list whose coverage can't be measured is left out.
- Franchise all-time (ingest/records.update, top 100 by points per franchise): points, goals and assists, told inside
  the top ten, for franchises with ten or more seasons (as in stories._club_lists). His total and any active rival's
  come from our own game logs; retired rivals from the records site. A player is only told on a franchise list when his
  career is complete on file.

"Active" means the player has a page here this season (he has dressed, or a club holds him), never the records site's
own flag. Writes site/public/data/records.json: {asof, players: {id: [{tag, text}]}}.
"""
from __future__ import annotations

import json

import polars as pl

from pipeline.config import SITE_DATA

LEAGUE_TOP = {"goals": 50, "assists": 50, "points": 50, "gwg": 25, "otg": 25, "otg3": 10}
CLUB_TOP = 10
MOST = 4  # notes on one page
WHAT = {"goals": "goals", "assists": "assists", "points": "points", "gwg": "game-winning goals", "otg": "overtime goals",
        "otg3": "overtime goals since the NHL went to 3-on-3 overtime in 2015-16"}
WHERE = {k: ("" if k == "otg3" else " in NHL history") for k in WHAT}


def _ord(n: int) -> str:
    from pipeline.export.league_lists import _ord as o
    return o(n)


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else f"{', '.join(xs[:-1])} and {xs[-1]}"


def _name(r: dict, names: dict[int, str]) -> str:
    return _name_raw(r, names).replace("'", "’")  # the records site types "Brind'Amour"


def _name_raw(r: dict, names: dict[int, str]) -> str:
    from pipeline.ingest import names as names_
    if names.get(r["playerId"]):
        return names[r["playerId"]]
    if r.get("skaterFullName"):
        f, l = names_.spell(r["playerId"], *r["skaterFullName"].split(" ", 1)) if " " in r["skaterFullName"] else (None, None)
        return f"{f} {l}" if f and l else r["skaterFullName"]
    f, l = names_.spell(r["playerId"], r.get("firstName"), r.get("lastName"))
    return f"{f} {l}"


def sentence(who: str, a: int, ahead: list[tuple[str, int]], level: list[str], what: str, where: str = "",
             club: str | None = None, active_first: bool = False) -> str:
    """One note. `ahead` is everyone with more (best first), `level` everyone on the same total. With `club` the note is
    about a club's all-time list (`club` = "the Hurricanes’"), else a league category (`what` + `where`)."""
    rank = len(ahead) + 1
    lst = club and f"{club} all-time {'scoring' if what == 'points' else what} list"
    unit = f" {what}" if lst and what != "points" else " points" if lst else ""
    with_ = f"with {a:,}{unit}"
    from pipeline.export.stories import num
    tied = "" if not level else f", level with {_and(level) if len(level) <= 2 else f'{num(len(level))} others'}"
    behind = (", behind " + _and([f"{n} ({v:,})" for n, v in ahead])) if 0 < len(ahead) <= 2 else \
        f", {num(ahead[-1][1] - a)} behind {ahead[-1][0]}" if ahead else ""
    if lst:
        if rank == 1 and not level:
            text = f"{who} is {club} all-time leader in {what}, with {a:,}."
        elif rank == 1:
            text = f"{who} is tied for first on {lst}, {with_}{tied}."
        else:
            text = f"{who} {'is tied for' if level else 'ranks'} {_ord(rank)} on {lst}, {with_}{tied}{behind}."
    elif rank == 1 and not level:
        text = f"{who} has the most {what}{where}, {with_}."
    elif rank == 1:
        text = f"{who} is tied for the most {what}{where}, {with_}{tied}."
    else:
        text = f"{who} {'is tied for' if level else 'ranks'} {_ord(rank)} in {what}{where}, {with_}{tied}{behind}."
    if active_first and rank > 1:
        text += " That’s the most of any active player."
    return text


def league_notes(season: int, names: dict[int, str], last: dict[int, str], active: set[int], logs) -> dict[int, list[dict]]:
    from pipeline.export import league_lists
    from pipeline.ingest import records
    snaps = records.league_snapshots()
    if not snaps:
        return {}
    snap = snaps[-1]
    counts = league_lists.season_counts(season)
    out: dict[int, list[dict]] = {}
    for name, top in LEAGUE_TOP.items():
        rows = snap["lists"].get(name) or []
        cov = league_lists.coverage(snap, name, season, logs) if rows else None
        if not cov:
            continue
        since = counts.filter(pl.col("date") > cov).group_by("player_id").agg(pl.col(name).sum())
        add = dict(zip(since["player_id"].to_list(), since[name].to_list()))
        field = league_lists.STAT[name]
        tot = [(r, (r.get(field) or 0) + add.get(r["playerId"], 0)) for r in rows]
        tot.sort(key=lambda x: -x[1])
        floor = tot[-1][1] if len(tot) >= records.LEAGUE_DEPTH else 0
        for r, a in tot:
            pid = r["playerId"]
            if pid not in active or a <= floor:  # at the list's last total, someone off the list may be level
                continue
            ahead = [(_name(x, names), v) for x, v in tot if v > a]
            if len(ahead) + 1 > top:
                continue
            level = [_name(x, names) for x, v in tot if v == a and x["playerId"] != pid]
            first_active = not any(x["playerId"] in active for x, v in tot if v >= a and x["playerId"] != pid)
            out.setdefault(pid, []).append({
                "tag": "NHL history", "list": name, "rank": len(ahead) + 1, "score": (len(ahead) + 1) / top,
                "text": sentence(last[pid], a, ahead, level, WHAT[name], WHERE[name],
                                 active_first=first_active)})
    return out


def club_notes(season: int, names: dict[int, str], last: dict[int, str], active: set[int], logs, W) -> dict[int, list[dict]]:
    """Each active player inside a franchise's top ten in points, goals or assists, his best of the three per club."""
    from pipeline.export import stories
    from pipeline.ingest import records
    day = "9999-12-31"
    code = {}  # franchise -> today's team code, for "the Hurricanes’"
    for c, fid in records._seasons().get(max(records._seasons(), default=""), {}).items():
        code[fid] = c
    out: dict[int, list[dict]] = {}
    for fid, t in code.items():
        lead = records.leaders(fid)
        if not lead or records.seasons_of(fid) < stories.CLUB_SEASONS:
            continue
        totals = {}
        for r in lead:
            own = stories._club_totals(r["playerId"], fid, day, logs) if r.get("activePlayer") or r["playerId"] in active else None
            totals[r["playerId"]] = {k: v[1] for k, v in own.items()} if own else None
        for r in lead:
            pid = r["playerId"]
            if pid not in active or totals[pid] is None:
                continue
            best = None
            for stat in ("points", "goals", "assists"):
                val = lambda x: totals[x["playerId"]][stat] if totals[x["playerId"]] else x[stat]
                a = val(r)
                ahead = sorted(((_name(x, names), val(x)) for x in lead if val(x) > a), key=lambda y: -y[1])
                if len(ahead) + 1 > CLUB_TOP or (best and len(ahead) + 1 >= best["rank"]):
                    continue
                level = [_name(x, names) for x in lead if val(x) == a and x["playerId"] != pid]
                best = {"tag": "Club history", "rank": len(ahead) + 1, "score": 0.5 * (len(ahead) + 1) / CLUB_TOP,
                        "text": sentence(last[pid], a, ahead, level, stat, club=W.poss(t))}
            if best:
                out.setdefault(pid, []).append(best)
    return out


def run(season: int) -> dict:
    from pipeline.export import stories
    from pipeline.ingest import records
    idx = SITE_DATA / "players" / "index.json"
    if not idx.exists():
        return {"players": 0}
    people = json.loads(idx.read_text())
    names = {p["id"]: p["name"] for p in people}
    last = {p["id"]: p.get("last") or p["name"].split(" ")[-1] for p in people}  # his own page: "Aho ranks third", as the intro does
    active = set(names)
    logs = stories._club_logs()
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    W = stories.Words(places)
    notes: dict[int, list[dict]] = {}
    for part in (league_notes(season, names, last, active, logs), club_notes(season, names, last, active, logs, W)):
        for pid, xs in part.items():
            notes.setdefault(pid, []).extend(xs)
    from pipeline.export.league_lists import season_counts
    dates = season_counts(season)["date"]
    out = {}
    for pid, xs in notes.items():
        xs.sort(key=lambda x: x["score"])
        ot = [x for x in xs if x.get("list") in ("otg", "otg3")]
        xs = [x for x in xs if x not in ot[1:]]  # career and 3-on-3 overtime goals are mostly the same goals: the better rank
        out[str(pid)] = [{"tag": x["tag"], "text": x["text"]} for x in xs[:MOST]]
    (SITE_DATA / "records.json").write_text(json.dumps({"asof": dates.max() if len(dates) else None, "players": out},
                                                       separators=(",", ":"), ensure_ascii=False))
    records.leaders.cache_clear()
    return {"players": len(out)}
