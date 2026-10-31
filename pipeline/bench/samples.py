"""Before-and-after text samples (plan item E): every sentence a reader sees on the previews, post-game pages and
report cards of a fixed set of games, rebuilt by the code being tested, so a wording change can be read as a diff.

The games are listed in sample_games.json, picked by `choose` to cover as many kinds of call, story item and game
(overtime, shootouts, shutouts, fights, comebacks, players who left) as possible on a few game days, since each day's
previews need the day's fingerprints and lines rebuilt (about 20 s a day). The list stays fixed so two runs compare
the same pages; run `python -m pipeline.bench choose` to pick a fresh set later in the season.

Everything is rebuilt in a temporary copy of the site's data files: the site's own files are never touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import CURRENT_SEASON, SITE_DATA

LIST = Path(__file__).with_name("sample_games.json")
WORDS = {"head", "body", "text", "plain", "title", "happened", "call", "see", "gist", "kicker", "paras", "lean", "note", "stakes",
         "lede", "foot", "list_head"}
PAGES = (("preview", "previews"), ("post-game", "recaps"), ("report cards", "jobs"), ("line matchups", "matchups"))


def texts(node, key: str | None = None, path: str = ""):
    """(where, sentence) for each reader-facing string in a page's JSON."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("pregame", "picks"):  # the saved data behind the page, and the wording picks (CRCs)
                continue
            yield from texts(v, k, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from texts(v, key, f"{path}[{i}]")
    elif isinstance(node, str) and key in WORDS and len(node.split()) >= 3:
        yield path, node


# Choosing the games -------------------------------------------------------------------------------------------------

FEATURES = {"dropped the gloves": "fight", "didn’t return": "left the game", "penalty shot": "penalty shot", "empty net": "empty net",
            "hat trick": "hat trick", "challenge": "challenge", "first NHL goal": "first goal", "Still coming in": "pending"}


def tags(gid: int) -> set[str]:
    out = set()
    p, r = SITE_DATA / "previews" / f"{gid}.json", SITE_DATA / "recaps" / f"{gid}.json"
    if p.exists():
        snap = json.loads(p.read_text())
        out |= {f"call: {c['kind']}" for c in snap.get("claims", [])}
        out |= {f"preview: {i['tag']}" for i in (snap.get("story") or {}).get("items", []) if isinstance(i, dict) and i.get("tag")}
        out |= {f"preview: {k}" for k in ("lately", "key_players", "stakes", "nhl_lineup") if snap.get(k)}
    if r.exists():
        rec = json.loads(r.read_text())
        out |= {f"post-game: {i['tag']}" for i in (rec.get("story") or {}).get("items", []) if isinstance(i, dict) and i.get("tag")}
        out |= {f"verdict: {c.get('verdict')}" for c in rec.get("calls", []) if c.get("verdict")}
        if rec.get("surprises"):
            out.add("surprises")
        end = rec.get("end")
        if end in ("OT", "SO"):
            out.add(f"ended in {end}")
        score = list((rec.get("score") or {}).values())
        if len(score) == 2:
            if min(score) == 0:
                out.add("shutout")
            if abs(score[0] - score[1]) >= 4:
                out.add("blowout")
        words = " ".join(t for _, t in texts(rec.get("story") or {}))
        out |= {tag for phrase, tag in FEATURES.items() if phrase in words}
        if "came back" in words or "storm back" in words.lower() or "erase" in words:
            out.add("comeback")
    return out


def choose(n: int = 36, max_days: int = 10) -> list[dict]:
    """Greedy cover: each step adds the game with the most kinds not yet covered, a new game day costing two kinds."""
    index = json.loads((SITE_DATA / "recaps" / "index.json").read_text())["games"]
    games = {g["id"]: {**g, "tags": tags(g["id"])} for g in index if (SITE_DATA / "previews" / f"{g['id']}.json").exists()}
    covered, days, picked = set(), set(), []
    while len(picked) < n and games:
        def gain(g):
            return len(g["tags"] - covered) - (0 if g["date"] in days else (2 if len(days) < max_days else 99))
        best = max(games.values(), key=lambda g: (gain(g), -g["id"]))
        if gain(best) <= 0 and len(days) >= max_days:
            # every kind covered on the days chosen: fill up with the richest games of those days
            rest = [g for g in games.values() if g["date"] in days]
            if not rest:
                break
            best = max(rest, key=lambda g: (len(g["tags"]), -g["id"]))
        covered |= best["tags"]
        days.add(best["date"])
        picked.append({"id": best["id"], "date": best["date"], "game": f"{best['away']} at {best['home']}", "covers": sorted(best["tags"])})
        del games[best["id"]]
    picked.sort(key=lambda g: (g["date"], g["id"]))
    return picked


def save_choice(picked: list[dict]) -> None:
    kinds = sorted({t for g in picked for t in g["covers"]})
    LIST.write_text(json.dumps({"chosen": datetime.now(timezone.utc).date().isoformat(), "season": CURRENT_SEASON,
                                "kinds_covered": kinds, "games": picked}, indent=1, ensure_ascii=False) + "\n")


# Rebuilding them ----------------------------------------------------------------------------------------------------

def _rebuild_here(season: int, ids: list[int]) -> None:
    """In the child process, with SITE_DATA pointed at the copy: rebuild the sample games' previews from the data
    before each game, then every post-game file, report card, Swing, matchup note and story for them."""
    from pipeline.export import game_scores, jobs, matchups, pregame, previews, recaps, stories
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    want = set(ids)
    by_day = defaultdict(list)
    for g in sched:
        if g["id"] in want and g["final"]:
            by_day[g["date"]].append(g)
    now = datetime.now(timezone.utc)
    chances = previews._chances([g for day in by_day.values() for g in day])
    for day in sorted(by_day):
        site, n = pregame.site_as_of(season, day)
        through = max((g["date"] for g in sched if g["final"] and g["date"] < day), default=None)
        for g in by_day[day]:
            snap = previews.snapshot(g, site, places, sched, now, chances.get(g["id"]), rebuilt=True, games_before=n, data_through=through,
                                     methods=pregame.METHODS)
            (SITE_DATA / "previews" / f"{g['id']}.json").write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
    recaps.run(season)
    jobs.run(season, only=want)
    game_scores.run(season, only=want)
    matchups.run(season)
    for gid in ids:
        p = SITE_DATA / "recaps" / f"{gid}.json"
        if not p.exists():
            continue
        r = json.loads(p.read_text())
        if r.get("pending"):
            continue
        story = stories.recap_story(r, places, season)
        if story:
            r["story"] = story
            p.write_text(json.dumps(r, separators=(",", ":"), ensure_ascii=False))


def collect(folder: Path, games: list[dict]) -> dict:
    out = {}
    for g in games:
        pages = {}
        for name, sub in PAGES:
            p = folder / sub / f"{g['id']}.json"
            if p.exists():
                pages[name] = [[w, t] for w, t in texts(json.loads(p.read_text()))]
        out[str(g["id"])] = {"game": g["game"], "date": g["date"], "pages": pages}
    return out


def run(season: int = CURRENT_SEASON, keep: Path | None = None) -> dict:
    picked = json.loads(LIST.read_text())["games"]
    with tempfile.TemporaryDirectory(prefix="samples-") as tmp:
        copy = Path(tmp) / "site"
        shutil.copytree(SITE_DATA, copy, symlinks=True)
        env = {**os.environ, "LAST_CHANGE_SITE_DATA": str(copy)}
        subprocess.run([sys.executable, "-m", "pipeline.bench", "samples-here", str(season), *[str(g["id"]) for g in picked]],
                       env=env, check=True, cwd=Path(__file__).resolve().parents[2], stdout=subprocess.DEVNULL)
        body = collect(copy, picked)
        if keep:
            shutil.copytree(copy, keep, dirs_exist_ok=True)
    from pipeline.bench.calls import _sha
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": _sha(), "games": body}
