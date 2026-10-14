"""Roster check: does the site's list of players match the clubs' official rosters?

Sora found the Rangers' Players page showing two goalies while the club listed four (2026-10-14): goalies who had
only dressed as the backup, or not dressed at all, got no player page. So every update compares the players the clubs
list today (`/v1/roster`, saved by ingest/rosters.py) with the player pages the export wrote, and the nightly workflow
opens a GitHub issue when they disagree:

- every listed player has a page, under the club that lists him;
- each club's listed goalies all have pages (the Rangers' case, counted separately because goalies are few).

The result is saved to data/raw/roster_check.json. `python -m pipeline.export.roster_check --alarm` prints the issue
text when the check failed, nothing otherwise.
"""
import json
import sys

from pipeline.config import CURRENT_SEASON, RAW, SITE_DATA
from pipeline.ingest import rosters

FILE = RAW / "roster_check.json"


def check(season: int = CURRENT_SEASON) -> dict:
    listing = rosters.listed_today(season)
    index_file = SITE_DATA / "players" / "index.json"
    if not listing or not index_file.exists():
        return {"season": season, "status": "skipped", "problems": []}
    pages = {p["id"]: p for p in json.loads(index_file.read_text())}
    t = rosters._read(season)
    day = t["date"].max()
    t = t.filter(t["date"] == day)
    problems = []
    for r in t.sort("team", "last").iter_rows(named=True):
        name, pid = f'{r["first"]} {r["last"]}', r["player_id"]
        page = pages.get(pid)
        if page is None:
            problems.append({"team": r["team"], "id": pid, "name": name, "pos": r["pos"], "problem": "no player page"})
        elif page["team"] != r["team"]:
            problems.append({"team": r["team"], "id": pid, "name": name, "pos": r["pos"], "problem": f"page shows him with {page['team']}"})
    goalies = {}
    for r in t.filter(t["pos"] == "G").iter_rows(named=True):
        g = goalies.setdefault(r["team"], {"listed": 0, "pages": 0})
        g["listed"] += 1
        g["pages"] += int(pages.get(r["player_id"], {}).get("team") == r["team"])
    short = {team: g for team, g in sorted(goalies.items()) if g["pages"] < g["listed"]}
    return {"season": season, "date": day, "listed": t.height, "status": "ok" if not problems else "mismatch",
            "problems": problems, "goalies_short": short}


def save(result: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(result, indent=1, ensure_ascii=False))


def run(season: int = CURRENT_SEASON) -> str:
    r = check(season)
    save(r)
    if r["status"] == "mismatch":
        print(alarm_text(r))
    return r["status"]


def alarm_text(r: dict) -> str:
    lines = [f"The update on {r['date']} found {len(r['problems'])} of the {r['listed']} players the clubs list today "
             "missing from the site's player pages or shown with the wrong club.", ""]
    for team, g in r.get("goalies_short", {}).items():
        lines.append(f"- {team} lists {g['listed']} goalies; the site has pages for {g['pages']} of them.")
    for p in r["problems"]:
        lines.append(f"- {p['team']}: {p['name']} ({p['pos']}, {p['id']}): {p['problem']}")
    return "\n".join(lines)


if __name__ == "__main__":
    res = json.loads(FILE.read_text()) if FILE.exists() else check()
    if "--alarm" in sys.argv:
        if res.get("status") == "mismatch":
            print(alarm_text(res))
    else:
        print(json.dumps(res, indent=1, ensure_ascii=False))
