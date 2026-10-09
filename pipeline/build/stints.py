"""Turn shift charts into stints: stretches of play with the same players on the ice."""
from dataclasses import dataclass

PERIOD_SECONDS = 1200


def clock_to_seconds(mmss: str) -> int:
    m, s = mmss.split(":")
    return int(m) * 60 + int(s)


def game_seconds(period: int, mmss: str) -> int:
    return (period - 1) * PERIOD_SECONDS + clock_to_seconds(mmss)


@dataclass(frozen=True)
class Shift:
    player_id: int
    home: bool
    goalie: bool
    start: int
    end: int


def clean_shifts(rows: list[dict], home_team_id: int, goalie_ids: set[int], *, max_period: int) -> list[Shift]:
    """Keep real shifts (typeCode 517), drop duplicates and empty rows, convert to game seconds."""
    seen, out = set(), []
    for r in rows:
        if r.get("typeCode") != 517 or not r.get("startTime") or not r.get("endTime"):
            continue
        if r["period"] > max_period:
            continue
        start, end = game_seconds(r["period"], r["startTime"]), game_seconds(r["period"], r["endTime"])
        key = (r["playerId"], start, end)
        if end <= start or key in seen:
            continue
        seen.add(key)
        out.append(Shift(r["playerId"], r["teamId"] == home_team_id, r["playerId"] in goalie_ids, start, end))
    return _merge_overlaps(out)


def _merge_overlaps(shifts: list[Shift]) -> list[Shift]:
    """A player cannot be on the ice twice at once: overlapping rows for one player become one shift.

    Shifts that merely touch (one ends as the next begins) are kept separate so line changes survive.
    """
    merged: list[Shift] = []
    for s in sorted(shifts, key=lambda x: (x.player_id, x.start, x.end)):
        last = merged[-1] if merged else None
        if last and last.player_id == s.player_id and s.start < last.end:
            merged[-1] = Shift(last.player_id, last.home, last.goalie, last.start, max(last.end, s.end))
        else:
            merged.append(s)
    return merged


def build_stints(shifts: list[Shift]) -> list[dict]:
    """Split the game at every line change. Each stint is [start, end) with fixed personnel."""
    points = sorted({s.start for s in shifts} | {s.end for s in shifts})
    by_start = sorted(shifts, key=lambda s: s.start)
    stints, active, i = [], [], 0
    for t0, t1 in zip(points, points[1:]):
        while i < len(by_start) and by_start[i].start <= t0:
            active.append(by_start[i])
            i += 1
        active = [s for s in active if s.end > t0]
        on = [s for s in active if s.end >= t1]
        stints.append(
            {
                "start": t0,
                "end": t1,
                "home_skaters": sorted({s.player_id for s in on if s.home and not s.goalie}),
                "away_skaters": sorted({s.player_id for s in on if not s.home and not s.goalie}),
                "home_goalie": next((s.player_id for s in on if s.home and s.goalie), None),
                "away_goalie": next((s.player_id for s in on if not s.home and s.goalie), None),
            }
        )
    return stints


def on_ice(shifts: list[Shift], t: int, *, faceoff: bool) -> list[Shift]:
    """Players credited with an event at time t.

    A faceoff belongs to the players coming on (start <= t < end); everything else belongs to the
    players who were out there as it happened (start < t <= end).
    """
    if faceoff:
        return [s for s in shifts if s.start <= t < s.end]
    return [s for s in shifts if s.start < t <= s.end]


def strength(own_skaters: int, opp_skaters: int, own_goalie: bool, opp_goalie: bool) -> str:
    """Strength bucket from one team's point of view."""
    if own_goalie and opp_goalie:
        pair = (own_skaters, opp_skaters)
        names = {(5, 5): "5v5", (5, 4): "5v4", (4, 5): "4v5", (5, 3): "5v3", (3, 5): "3v5", (4, 4): "4v4", (3, 3): "3v3", (4, 3): "4v3", (3, 4): "3v4"}
        return names.get(pair, "other")
    if not own_goalie and opp_goalie:
        return "EA"  # own net empty, extra attacker
    if own_goalie and not opp_goalie:
        return "EN"  # shooting at an empty net
    return "other"
