"""An outside source against what we publish: ESPN's public scoreboard and standings (site.api.espn.com, already used
for injuries and roster moves). ESPN keeps its own feed, so a final score or a record that disagrees with ours points
at a game we saved wrong, a game we missed, or a standings table we read too early. Skipped offline.
  scores     final scores for the last DAYS game days against our post-game pages (a game ESPN has final and we
             have no page for after a day is listed too)
  standings  each club's games played, wins, losses, overtime losses and points against teams.json, for clubs whose
             games played agree
ESPN's numbers are used for checking only, never shown.
"""
import json
from datetime import date, timedelta

from pipeline.config import OFFLINE, SITE_DATA
from pipeline.ingest.transactions import ESPN_ABBR

BASE = "https://site.api.espn.com/apis"
DAYS = 3


def _abbr(a: str) -> str:
    return ESPN_ABBR.get(a, a)


def espn_finals(day: date) -> dict:
    """{(away, home): (away score, home score)} for ESPN's final games on `day`."""
    from pipeline.ingest.client import get
    d = get(f"{BASE}/site/v2/sports/hockey/nhl/scoreboard?dates={day:%Y%m%d}").json()
    out = {}
    for e in d.get("events") or []:
        if (e.get("status") or {}).get("type", {}).get("name") != "STATUS_FINAL":
            continue
        side = {c["homeAway"]: c for c in e["competitions"][0]["competitors"]}
        a, h = side["away"], side["home"]
        out[(_abbr(a["team"]["abbreviation"]), _abbr(h["team"]["abbreviation"]))] = (int(a["score"]), int(h["score"]))
    return out


def espn_standings() -> dict:
    from pipeline.ingest.client import get
    d = get(f"{BASE}/v2/sports/hockey/nhl/standings").json()
    out = {}
    for conf in d.get("children") or []:
        for e in (conf.get("standings") or {}).get("entries") or []:
            st = {s["name"]: s.get("value") for s in e["stats"]}
            out[_abbr(e["team"]["abbreviation"])] = {k: int(st.get(v) or 0) for k, v in
                                                     (("gp", "gamesPlayed"), ("w", "wins"), ("l", "losses"), ("otl", "otLosses"), ("pts", "points"))}
    return out


def run(season: int, today: date | None = None) -> dict:
    if OFFLINE:
        return {"level": "skipped", "why": "offline run"}
    today = today or date.today()
    ours = {}
    for p in (SITE_DATA / "recaps").glob("2*.json"):
        r = json.loads(p.read_text())
        ours.setdefault((r["away"], r["home"]), []).append((r.get("date") or "", r["score"][r["away"]], r["score"][r["home"]], r["game_id"]))
    problems, watch, compared = [], [], {"games": 0, "teams": 0}
    for k in range(1, DAYS + 1):
        day = today - timedelta(days=k)
        try:
            finals = espn_finals(day)
        except Exception as e:
            watch.append({"what": f"ESPN's scoreboard for {day} did not load: {e!r}"})
            continue
        for (a, h), score in finals.items():
            near = {(day + timedelta(days=d)).isoformat() for d in (-1, 0, 1)}  # ESPN dates late western games a day on
            mine = next((x[1:] for x in ours.get((a, h), []) if x[0] in near), None)
            if not mine:
                if k > 1:  # a game from last night may still be on its way to a page
                    problems.append({"what": f"{a} at {h} on {day} is final on ESPN ({score[0]}-{score[1]}) but has no post-game page"})
                continue
            compared["games"] += 1
            if score != mine[:2]:
                problems.append({"what": f"{a} at {h} on {day}: ESPN has {score[0]}-{score[1]}, our page {mine[0]}-{mine[1]}",
                                 "game": mine[2]})
    try:
        theirs = espn_standings()
    except Exception as e:
        theirs = {}
        watch.append({"what": f"ESPN's standings did not load: {e!r}"})
    teams = json.loads((SITE_DATA / "teams.json").read_text()) if (SITE_DATA / "teams.json").exists() else []
    for t in teams:
        e = theirs.get(t["abbr"])
        if not e or e["gp"] != t.get("gp"):
            continue
        compared["teams"] += 1
        diff = {k: {"ours": t.get(k), "espn": e[k]} for k in ("w", "l", "otl", "pts") if t.get(k) != e[k]}
        if diff:
            problems.append({"what": f"{t['abbr']}: our standings line differs from ESPN's", "detail": diff})
    return {"level": "problem" if problems else ("watch" if watch else "ok"), "compared": compared,
            "problems": problems, "watch": watch}
