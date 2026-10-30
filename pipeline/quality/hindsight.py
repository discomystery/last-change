"""Yesterday's previews against what happened (option B of the 2026-10-30 plan).

For every finished game with a preview, the preview readers saw before puck drop (the live original kept in
raw/previews_live/ when the page was later rebuilt, else the saved preview):
  starters  the projected starting goalie (NHL.com's pick or our own likely one) against who started
  out       players a Who to watch "Out" item named who dressed for the game after all
A miss on its own is normal (a late scratch, a surprise backup), so single misses are only listed; the alarm is for
a rate: NHL.com's picks right less than 80% of the time, or names in Out items dressing more than 5% of the time,
over the last 14 days with enough games to judge.
"""
import json
import re
from datetime import date, timedelta

import polars as pl

from pipeline.config import RAW, SITE_DATA, TABLES
from pipeline.ingest.names import fold

DAYS = 14
NHL_FLOOR, OUT_CEILING, MIN_CASES = 0.80, 0.05, 20
OUT_TAGS = {"Out"}  # "Missing" items say what already happened ("has missed their last two games")
SENT = re.compile(r"(?<=[.!?])\s+")


def preview_seen(season: int, gid: int) -> dict | None:
    live = RAW / "previews_live" / str(season) / f"{gid}.json"
    for p in (live, SITE_DATA / "previews" / f"{gid}.json"):
        if p.exists():
            return json.loads(p.read_text())
    return None


def said_out(items: list[dict], dressed: dict[str, int]) -> list[tuple[str, int, str]]:
    """(name, player id, sentence) for each dressed player an Out item named, leaving out sentences about
    who starts in goal instead ("Akira Schmid is expected to start")."""
    out = []
    for it in items or []:
        if it.get("tag") not in OUT_TAGS:
            continue
        for s in SENT.split(it.get("text") or ""):
            if re.search(r"\bexpected to start\b|\bstarts?\b in goal", s):
                continue
            low = fold(s)
            out += [(n, pid, s) for n, pid in dressed.items() if re.search(rf"\b{re.escape(n)}\b", low)]
    return out


def run(season: int, today: date | None = None) -> dict:
    today = today or date.today()
    since = (today - timedelta(days=DAYS)).isoformat()
    rosters = TABLES / str(season) / "game_rosters.parquet"
    gr = pl.read_parquet(rosters).filter(pl.col("status") == "dressed") if rosters.exists() else None
    tally = {"nhl": [0, 0], "model": [0, 0]}  # [right, picks] over the window
    recent_out, out_items, misses, wrong_out = 0, 0, [], []
    season_tally = {"nhl": [0, 0], "model": [0, 0]}
    for p in sorted((SITE_DATA / "recaps").glob("2*.json")):
        r = json.loads(p.read_text())
        gid, day = r["game_id"], r.get("date") or ""
        snap = preview_seen(season, gid)
        if not snap:
            continue
        started = {g["team"]: g["id"] for g in r.get("goalies") or [] if g.get("started")}
        for t, pick in (snap.get("projected") or {}).items():
            src = pick.get("from") if pick.get("from") in tally else "model"
            if t not in started:
                continue
            ok = pick.get("id") == started[t]
            season_tally[src][0] += ok
            season_tally[src][1] += 1
            if day >= since:
                tally[src][0] += ok
                tally[src][1] += 1
                if not ok:
                    misses.append({"game": gid, "date": day, "team": t, "source": src, "projected": pick.get("id"),
                                   "started": started[t]})
        if gr is None or day < since:
            continue
        g = gr.filter(pl.col("game_id") == gid)
        dressed = {fold(f"{a} {b}"): pid for a, b, pid in zip(g["first"], g["last"], g["player_id"])}
        items = (snap.get("story") or {}).get("items") or []
        out_items += sum(it.get("tag") in OUT_TAGS for it in items)
        for n, pid, s in said_out(items, dressed):
            recent_out += 1
            wrong_out.append({"game": gid, "date": day, "player": pid, "name": n, "text": s})
    rate = lambda k: tally[k][0] / tally[k][1] if tally[k][1] else None
    problems = []
    if tally["nhl"][1] >= MIN_CASES and rate("nhl") < NHL_FLOOR:
        problems.append({"what": f"NHL.com's projected starters were right only {tally['nhl'][0]} of {tally['nhl'][1]} "
                                 f"times in the last {DAYS} days (usually about 9 in 10): check that the NHL.com "
                                 "previews are still being read and matched"})
    if out_items >= MIN_CASES and recent_out / out_items > OUT_CEILING:
        problems.append({"what": f"{recent_out} players named in {out_items} Out items in the last {DAYS} days dressed "
                                 "for the game after all: the absence notes may be going stale"})
    return {"level": "problem" if problems else ("watch" if misses or wrong_out else "ok"), "problems": problems,
            "starters": {k: {"right": v[0], "picks": v[1]} for k, v in tally.items()},
            "starters_season": {k: {"right": v[0], "picks": v[1]} for k, v in season_tally.items()},
            "out_items": out_items, "said_out_but_played": wrong_out, "starter_misses": misses}
