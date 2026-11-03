"""The live feed's loop (Sora, 2026-11-02): while games are on, rebuild each live game's file every two minutes and
hand the folder to a publish command, which puts it on the small live site (its own repository and GitHub Pages
site, because publishing the main site takes minutes and replaces all of it).

Runs inside the live site's workflow in shifts like the game-night watcher (pipeline/watch_games.py, whose scoreboard
and hand-over it reuses): follow tonight's games, wait for the next slate if the shift has time left, then start the
next shift. Writes {out}/{game id}.json (build.game) and {out}/index.json (the day's games and their states), and runs
the publish command only when something changed. A game's file is written one last time once it is final, then left;
the post-game page takes over.

    python -m pipeline.live.loop --out feed --publish "bash publish.sh"   # a shift
    python -m pipeline.live.loop --out feed --once 2026-11-02             # one pass over one day, nothing published
"""
import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from pipeline import watch_games as W
from pipeline.live import build
from pipeline.metrics import xg_model

PBP = "https://api-web.nhle.com/v1/gamecenter/{gid}/play-by-play"
POLL = 120  # seconds between passes
LIVE = {"LIVE", "CRIT"}
LEAD = timedelta(minutes=10)  # wake this long before the next slate's first puck drop
DONE_HOURS = 4.5  # every game final and the last started this long ago: the night is over


def fetch(gid: int) -> dict:
    req = urllib.request.Request(PBP.format(gid=gid), headers={"User-Agent": W.USER_AGENT.replace("game-night watcher", "live feed")})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def write(path: Path, data: dict) -> bool:
    """Write the file if its content (not counting when it was made) changed; True when it did."""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    key = hashlib.sha1(json.dumps({k: v for k, v in data.items() if k != "made"}, sort_keys=True, default=str).encode()).hexdigest()
    mark = path.with_suffix(".key")
    if mark.exists() and mark.read_text() == key and path.exists():
        return False
    path.write_text(body)
    mark.write_text(key)
    return True


def one_pass(day: str, out: Path, model: dict, done: set[int]) -> tuple[bool, list[dict]]:
    """Rebuild every live game's file (and once more each newly final game the feed had followed). Returns whether any
    file changed and the day's games."""
    games = W.scoreboard(day)
    changed = False
    for g in games:
        path = out / f"{g['id']}.json"
        if g["id"] in done or not (g["state"] in LIVE or (g["state"] in W.FINAL and path.exists())):
            continue
        try:
            data = build.game(fetch(g["id"]), model)
        except Exception as e:  # one game's hiccup must not stop the others
            print(f"{W.now():%H:%M:%S} game {g['id']} skipped: {e!r}", flush=True)
            continue
        data["live"]["made"] = W.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        changed |= write(path, data)
        if g["state"] in W.FINAL:
            done.add(g["id"])
    index = {"day": day, "games": [{"id": g["id"], "state": g["state"], "start": g["start"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "file": (out / f"{g['id']}.json").exists()} for g in games]}
    changed |= write(out / "index.json", index | {"made": W.now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    return changed, games


def publish(cmd: str | None) -> None:
    if not cmd:
        return
    r = subprocess.run(cmd, shell=True)
    print(f"{W.now():%H:%M:%S} published (exit {r.returncode})", flush=True)


def follow(day: str, out: Path, model: dict, cmd: str | None, until: datetime) -> str:
    """Follow one day's games until the night is over ("done"), there are none ("none") or the shift ends ("shift")."""
    done: set[int] = set()
    while True:
        try:
            changed, games = one_pass(day, out, model, done)
        except Exception as e:  # the NHL's end is down: try again next pass
            print(f"{W.now():%H:%M:%S} scoreboard unavailable: {e!r}", flush=True)
            changed, games = False, None
        if changed:
            publish(cmd)
        if games is not None:
            if not games:
                return "none"
            if all(g["state"] in W.FINAL for g in games) and W.now() - max(g["start"] for g in games) > timedelta(hours=1):
                return "done"
        if W.now() >= until:
            return "shift"
        time.sleep(POLL)


def shift(out: Path, cmd: str | None) -> None:
    model = xg_model.saved_live()
    began = W.now()
    end = began + timedelta(seconds=W.SHIFT)
    day = W.game_day(began)
    while W.now() < end:
        try:
            games = W.scoreboard(day)
            if games and min(g["start"] for g in games) - LEAD <= W.now() and not (
                    all(g["state"] in W.FINAL for g in games) and W.now() - max(g["start"] for g in games) > timedelta(hours=DONE_HOURS)):
                if follow(day, out, model, cmd, end) == "shift":
                    break
            elif games and min(g["start"] for g in games) > W.now():
                wake = min(g["start"] for g in games) - LEAD  # tonight's slate hasn't started: wait for it
                print(f"waiting for {day}'s games: waking at {min(wake, end):%H:%M} UTC", flush=True)
                while W.now() < min(wake, end):
                    time.sleep(min(600, max(1, (min(wake, end) - W.now()).total_seconds())))
                continue
            day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
            start = W.first_start(day)
        except Exception as e:
            print(f"scoreboard unavailable: {e!r}", flush=True)
            time.sleep(POLL)
            continue
        wake = start - LEAD if start else end
        print(f"next slate {day}: waking at {min(wake, end):%Y-%m-%d %H:%M} UTC", flush=True)
        while W.now() < min(wake, end):
            time.sleep(min(600, max(1, (min(wake, end) - W.now()).total_seconds())))
    for wait in (0, 30, 120):  # the next shift; the scheduled starts revive the chain if this fails
        time.sleep(wait)
        try:
            W._start("live.yml", {}, "the next live shift")
            return
        except Exception as e:
            print(f"could not start the next shift: {e!r}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="feed")
    ap.add_argument("--publish")
    ap.add_argument("--once", metavar="DAY")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.once:
        print(one_pass(a.once, out, xg_model.saved_live(), set())[0])
    else:
        shift(out, a.publish)
