"""Game-night watcher: follows tonight's NHL games and asks the nightly workflow for quick runs at the moments that
matter (user decision 2026-10-11, thread "Why the site rebuilds").

- Before each game, about an hour and again about 20 minutes ahead of puck drop: scratches, NHL.com's projected
  starters and the league's injury news reach tonight's preview before it freezes.
- As each game goes final, then again 30 and 75 minutes later: its post-game page goes up within minutes, and the
  parts that wait on the NHL's shift chart fill in on the follow-ups.
- Once every game is done and followed up, one full update rebuilds everything else (ratings, team and player pages).

Runs inside GitHub Actions (gamenight.yml) with only the standard library, so it starts in seconds. Requests are
spaced a few minutes apart and merged: one quick run handles every game that needs it. A run already waiting in the
queue is replaced by the next request, which is fine because each run does everything that is due.

GitHub's timers proved unreliable (on 2026-10-11 the 02:30 shift never started and a 20:30 update ran at 23:53), so
the watcher keeps itself going: between nights it waits for the next slate, and when its shift ends it starts the
next shift itself. The scheduled starts in gamenight.yml only restart the chain if it ever breaks; a start that finds
another watcher already queued or running is harmless, since the queue keeps at most one waiting.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SCORE = "https://api-web.nhle.com/v1/score/{day}"
USER_AGENT = "last-change game-night watcher (github.com/discomystery/last-change)"
FINAL = {"FINAL", "OFF"}
POLL = 120  # seconds between looks at the scoreboard
SPACING = 240  # at least this long between two requests for a run
BEFORE = (timedelta(minutes=60), timedelta(minutes=20))  # pre-game passes, ahead of puck drop
AFTER = (timedelta(0), timedelta(minutes=30), timedelta(minutes=75))  # post-game passes, after a game is seen final
DONE_HOURS = 4.5  # all games final and the last one started this long ago: an earlier watcher already handled the night
SHIFT = 5 * 3600 + 30 * 60  # GitHub stops a job at 6 hours; the watcher then starts its own next shift
LEAD = timedelta(minutes=65)  # wake this long before the next slate's first puck drop (the first pre-game pass is at 60)


def now() -> datetime:
    return datetime.now(timezone.utc)


def game_day(t: datetime) -> str:
    """The NHL's date for tonight's games: a game at 02:00 UTC belongs to the evening before."""
    return (t - timedelta(hours=10)).date().isoformat()


def scoreboard(day: str) -> list[dict]:
    req = urllib.request.Request(SCORE.format(day=day), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [{"id": g["id"], "start": datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00")), "state": g["gameState"]}
            for g in data.get("games", []) if g.get("gameType") in (2, 3)]


def _start(workflow: str, inputs: dict, label: str) -> None:
    """Start a workflow on main. Printed instead when run outside Actions (for a dry run)."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        print(f"{now():%H:%M:%S} would start {label}", flush=True)
        return
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                                 data=json.dumps({"ref": "main", "inputs": inputs}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30):
        pass
    print(f"{now():%H:%M:%S} started {label}", flush=True)


def dispatch(mode: str) -> None:
    """Start a quick or full run of the nightly update."""
    _start("nightly.yml", {"mode": mode}, f"a {mode} run")


def next_shift() -> None:
    """Start the watcher's next shift. Tried a few times: if this fails, only the scheduled starts revive the chain."""
    for wait in (0, 30, 120):
        time.sleep(wait)
        try:
            _start("gamenight.yml", {}, "the next watcher shift")
            return
        except Exception as e:
            print(f"could not start the next shift: {e!r}", flush=True)


def plan(games: list[dict], finals: dict[int, datetime]) -> list[datetime]:
    """Every moment tonight that wants a quick run."""
    out = []
    for g in games:
        if g["state"] not in FINAL and g["id"] not in finals:
            out += [g["start"] - b for b in BEFORE]
    for seen in finals.values():
        out += [seen + a for a in AFTER]
    return sorted(out)


def watch(day: str | None = None, until: datetime | None = None) -> str:
    """Follow one day's games. Returns "none" (no games), "done" (the night is handled) or "shift" (out of time)."""
    began = now()
    day = day or game_day(began)
    until = until or began + timedelta(seconds=SHIFT)
    games = scoreboard(day)
    if not games:
        print(f"no games on {day}")
        return "none"
    finals = {g["id"]: began for g in games if g["state"] in FINAL}
    if len(finals) == len(games) and began - max(g["start"] for g in games) > timedelta(hours=DONE_HOURS):
        print(f"every game on {day} finished a while ago: nothing to do")
        return "done"
    served = began - timedelta(minutes=61)  # moments up to here are handled: anything due in the last hour is caught up at once
    last_request = None
    while True:
        t = now()
        moments = plan(games, finals)
        if len(finals) == len(games) and not [m for m in moments if m > t]:
            dispatch("full")  # the night's full rebuild, once everything is final and followed up: it does the last pass too
            return "done"
        if [m for m in moments if served < m <= t] and (last_request is None or (t - last_request).total_seconds() >= SPACING):
            dispatch("quick")
            last_request, served = t, t
        if t >= until:
            print("shift over")
            return "shift"
        time.sleep(POLL)
        try:
            games = scoreboard(day)
        except Exception as e:  # a hiccup at the NHL's end: look again next time
            print(f"scoreboard unavailable: {e!r}", flush=True)
            continue
        for g in games:
            if g["state"] in FINAL and g["id"] not in finals:
                finals[g["id"]] = now()
                print(f"{finals[g['id']]:%H:%M:%S} game {g['id']} is final", flush=True)


def first_start(day: str) -> datetime | None:
    games = scoreboard(day)
    return min((g["start"] for g in games), default=None)


def shift() -> None:
    """One watcher shift: follow tonight, wait for the next slate if there is time, then hand over to the next shift."""
    began = now()
    end = began + timedelta(seconds=SHIFT)
    day = game_day(began)
    while now() < end:
        try:
            if watch(day, end) == "shift":
                break
            day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
            start = first_start(day)
        except Exception as e:  # the NHL's end is down: look again shortly
            print(f"scoreboard unavailable: {e!r}", flush=True)
            time.sleep(POLL)
            continue
        wake = start - LEAD if start else end
        print(f"next slate {day}: waking at {min(wake, end):%Y-%m-%d %H:%M} UTC", flush=True)
        while now() < min(wake, end):
            time.sleep(min(POLL * 5, max(1, (min(wake, end) - now()).total_seconds())))
    next_shift()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        watch(sys.argv[1])  # one day by hand, no hand-over
    else:
        shift()
