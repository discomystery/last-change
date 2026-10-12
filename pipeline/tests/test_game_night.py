"""The game-night watcher's timeline, and when a quick request turns itself into a full update."""
import json
from datetime import datetime, timedelta, timezone

from pipeline import run, watch_games as W

T0 = datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)


def _night(monkeypatch, ends: dict[int, timedelta]):
    """Two games, at 23:00 and 23:30; each goes final at its start plus ends[id]. Returns the requests made, timed."""
    clock = {"t": T0}
    games = {1: T0 + timedelta(hours=1), 2: T0 + timedelta(hours=1, minutes=30)}
    sent = []
    monkeypatch.setattr(W, "now", lambda: clock["t"])
    monkeypatch.setattr(W, "scoreboard", lambda day: [{"id": i, "start": s, "state": "OFF" if clock["t"] >= s + ends[i] else "FUT"} for i, s in games.items()])
    monkeypatch.setattr(W, "dispatch", lambda mode: sent.append((clock["t"], mode)))
    monkeypatch.setattr(W.time, "sleep", lambda s: clock.update(t=clock["t"] + timedelta(seconds=s)))
    W.watch("2026-10-11")
    return sent


def test_quick_runs_before_and_after_each_game_then_one_full(monkeypatch):
    sent = _night(monkeypatch, {1: timedelta(hours=2, minutes=40), 2: timedelta(hours=2, minutes=35)})
    quick = [t - T0 for t, m in sent if m == "quick"]
    assert [m for _, m in sent][-1] == "full" and sum(m == "full" for _, m in sent) == 1
    mins = [q.total_seconds() / 60 for q in quick]
    assert any(0 <= m <= 2 for m in mins)  # 60 minutes before the first game (22:00)
    assert any(38 <= m <= 42 for m in mins)  # 20 minutes before it
    assert any(220 <= m <= 224 for m in mins)  # first game final at 01:40 (220 min)
    assert any(245 <= m <= 249 for m in mins)  # second game final at 02:05
    assert all((b - a).total_seconds() >= W.SPACING for a, b in zip(quick, quick[1:]))
    assert sent[-1][0] >= T0 + timedelta(minutes=245 + 75)  # the full update waits for the last follow-up


def test_nothing_to_do_when_the_night_is_long_over(monkeypatch):
    monkeypatch.setattr(W, "now", lambda: T0 + timedelta(hours=9))
    monkeypatch.setattr(W, "scoreboard", lambda day: [{"id": 1, "start": T0 + timedelta(hours=1), "state": "OFF"}])
    monkeypatch.setattr(W, "dispatch", lambda mode: (_ for _ in ()).throw(AssertionError("no runs expected")))
    W.watch("2026-10-11")


def test_quick_becomes_full_when_owed(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "STATE", tmp_path / "run_state.json")
    monkeypatch.setenv("GITHUB_SHA", "abc")
    at = datetime(2026, 10, 11, 23, 0, tzinfo=timezone.utc)
    assert run.mode("quick", at) == "full"  # no full update recorded yet
    run.STATE.write_text(json.dumps({"full_at": "2026-10-11T20:40:00+00:00", "full_sha": "abc"}))
    assert run.mode("quick", at) == "quick"
    assert run.mode("full", at) == "full"
    monkeypatch.setenv("GITHUB_SHA", "def")
    assert run.mode("quick", at) == "full"  # new code merged since the last full update
    monkeypatch.setenv("GITHUB_SHA", "abc")
    assert run.mode("quick", datetime(2026, 10, 12, 10, 5, tzinfo=timezone.utc)) == "full"  # the 10:00 update came due
    run.STATE.write_text(json.dumps({"full_at": "2026-10-11T20:20:00+00:00", "full_sha": "abc"}))
    assert run.mode("quick", at) == "full"  # the 20:30 update it may have replaced in the queue


def test_shift_waits_for_the_next_slate_then_hands_over(monkeypatch):
    """A shift that starts after the night is done sleeps until an hour before tomorrow's first game, follows it, and
    starts the next shift when its time is up."""
    start = datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)  # yesterday's games long over
    clock = {"t": start}
    slates = {"2026-10-11": [(T0 + timedelta(hours=1), 1)], "2026-10-12": [(datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc), 2)]}
    sent, handed = [], []
    monkeypatch.setattr(W, "now", lambda: clock["t"])
    monkeypatch.setattr(W, "scoreboard", lambda day: [{"id": i, "start": s, "state": "OFF" if clock["t"] >= s + timedelta(hours=3) else "FUT"} for s, i in slates.get(day, [])])
    monkeypatch.setattr(W, "dispatch", lambda mode: sent.append((clock["t"], mode)))
    monkeypatch.setattr(W, "next_shift", lambda: handed.append(clock["t"]))
    monkeypatch.setattr(W.time, "sleep", lambda s: clock.update(t=clock["t"] + timedelta(seconds=s)))
    W.shift()
    assert not [t for t, _ in sent if t < datetime(2026, 10, 12, 8, 55, tzinfo=timezone.utc)]  # nothing while waiting
    assert sent[0][1] == "quick" and sent[0][0] <= datetime(2026, 10, 12, 9, 2, tzinfo=timezone.utc)  # 60 min before puck drop
    assert len(handed) == 1 and handed[0] >= start + timedelta(seconds=W.SHIFT)  # hands over once, at the end of the shift
    assert handed[0] < start + timedelta(minutes=350)  # before GitHub's job limit


def test_a_day_without_games_still_hands_over(monkeypatch):
    clock = {"t": T0}
    handed = []
    monkeypatch.setattr(W, "now", lambda: clock["t"])
    monkeypatch.setattr(W, "scoreboard", lambda day: [])
    monkeypatch.setattr(W, "dispatch", lambda mode: (_ for _ in ()).throw(AssertionError("no runs expected")))
    monkeypatch.setattr(W, "next_shift", lambda: handed.append(clock["t"]))
    monkeypatch.setattr(W.time, "sleep", lambda s: clock.update(t=clock["t"] + timedelta(seconds=s)))
    W.shift()
    assert len(handed) == 1 and handed[0] - T0 <= timedelta(seconds=W.SHIFT + W.POLL * 5)
