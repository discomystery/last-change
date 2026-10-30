"""Wording picks that don't repeat for a team (Sora picked this 2026-10-30, thread "Improve game previews and automate
tuning": "Unstoppable force vs. immovable object" had run 17 times in 259 previews).

Every wording pool on preview pages is picked by a CRC of the game and the call, so a page reads the same between
runs. Inside `game(avoid)`, `pick` first leaves out any pool entry whose CRC is in `avoid` (the entries both teams'
last few previews used, read from their saved `picks`), as long as one is left, and records what it picked; the
preview saves that list as `picks`. Outside a `game()` block nothing is left out, so post-game pages and everything
else pick exactly as before.
"""
import json
import zlib
from contextlib import contextmanager
from contextvars import ContextVar

RECENT = 5  # previews per team whose wording a new preview stays away from

_avoid: ContextVar[frozenset] = ContextVar("variety_avoid", default=frozenset())
_picked: ContextVar[list | None] = ContextVar("variety_picked", default=None)


def crc(entry: str) -> int:
    return zlib.crc32(entry.encode())


def pick(pool: list[str], *key) -> str:
    avoid = _avoid.get()
    options = [p for p in pool if crc(p) not in avoid] if avoid else pool
    options = options or pool
    out = options[zlib.crc32("-".join(map(str, key)).encode()) % len(options)]
    picked = _picked.get()
    if picked is not None and len(pool) > 1:
        picked.append(crc(out))
    return out


@contextmanager
def game(avoid=()):
    """Pick for one preview, staying away from `avoid`; yields the list the picks are recorded in."""
    picked: list[int] = []
    a, p = _avoid.set(frozenset(avoid)), _picked.set(picked)
    try:
        yield picked
    finally:
        _avoid.reset(a)
        _picked.reset(p)


_seen: dict | None = None  # game id -> (start, away, home, picks), the saved previews this run has read or written


def _load(folder) -> dict:
    global _seen
    if _seen is None:
        _seen = {}
        for p in folder.glob("2*.json"):
            try:
                s = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            remember(s)
    return _seen


def remember(snap: dict) -> None:
    """Note a preview just built, so later games in the same run stay away from its wording too."""
    if _seen is not None and snap.get("game_id"):
        _seen[snap["game_id"]] = (snap.get("start") or "", snap.get("away"), snap.get("home"), snap.get("picks") or [])


def recent(folder, teams, start: str, n: int = RECENT) -> set[int]:
    """What the teams' last `n` previews before `start` picked (each team's own previews, not the pair's)."""
    seen = _load(folder)
    out: set[int] = set()
    for t in teams:
        past = sorted((v for v in seen.values() if v[0] < start and t in (v[1], v[2])), key=lambda v: v[0])[-n:]
        for v in past:
            out.update(v[3])
    return out
