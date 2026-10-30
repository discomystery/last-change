"""Moves up the NHL's all-time lists for the post-game story (Sora, 2026-10-15: "Sebastian Aho is second in 3-on-3
overtime game-winning goals").

Lists (ingest/records.py, a snapshot each update): career goals, assists and points (top 25), game-winning goals and
overtime goals (top 10), and overtime goals since overtime went to 3-on-3 in 2015-16 (top 10). Every overtime goal in
the regular season is a winner, so the last list is the "3-on-3 overtime winners" one.

A snapshot is taken some time before a game and the NHL's sites update on their own schedule, so each snapshot's
coverage is measured, not assumed: the latest game date through which our own game logs give the same totals as the
snapshot for the players on it with a complete career on file. Totals before tonight are then the snapshot plus the
games we have seen since that date. Game-winning goals follow the NHL: the winner's goal that put them one past the
loser's final total, none in a shootout.
"""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from pipeline.config import REGULAR, TABLES

TOP = {"goals": 25, "assists": 25, "points": 25, "gwg": 10, "otg": 10, "otg3": 10}
STANDING_TOP = 5  # on the overtime and game-winning lists, a top-five player's total is told whenever it grows
TIE_TOP = 5  # drawing level is told only near the top, since most passes come a game or two after a tie
ORDER = ("goals", "points", "assists", "otg3", "otg", "gwg")  # one list per player, the first that changed
STAT = {"goals": "goals", "assists": "assists", "points": "points", "gwg": "gameWinningGoals", "otg": "overtimeGoals",
        "otg3": "otGoals"}
WHERE = {"goals": "on the NHL’s all-time goals list", "assists": "on the NHL’s all-time assists list",
         "points": "on the NHL’s all-time scoring list", "gwg": "on the NHL’s all-time list of game-winning goals",
         "otg": "on the NHL’s all-time list of overtime goals",
         "otg3": "in overtime goals since the NHL went to 3-on-3 overtime in 2015-16"}
CHECK = 12  # players checked when measuring a snapshot's coverage


@lru_cache(maxsize=4)
def season_counts(season: int) -> pl.DataFrame:
    """One row per player and finished regular-season game: date, goals, assists, points, gwg, otg."""
    d = TABLES / str(season)
    g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
    ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "type", "sort", "period", "period_type", "team_id", "p1", "p2", "p3"])
    goals = ev.filter(pl.col("type") == "goal")
    shootout = set(goals.filter(pl.col("period_type") == "SO")["game_id"].to_list())
    goals = goals.filter(pl.col("period_type") != "SO").sort("game_id", "sort")
    info = {r["game_id"]: r for r in g.iter_rows(named=True)}
    rows = []
    seen: dict[tuple[int, int], int] = {}
    for r in goals.iter_rows(named=True):
        x = info.get(r["game_id"])
        if not x or x["home_score"] is None:
            continue
        win_id = x["home_id"] if x["home_score"] > x["away_score"] else x["away_id"]
        lose_final = min(x["home_score"], x["away_score"])
        k = (r["game_id"], r["team_id"])
        seen[k] = seen.get(k, 0) + 1
        gwg = r["team_id"] == win_id and seen[k] == lose_final + 1 and r["game_id"] not in shootout
        otg = r["period"] >= 4
        base = {"game_id": r["game_id"], "date": x["date"]}
        if r["p1"]:
            rows.append({**base, "player_id": r["p1"], "goals": 1, "assists": 0, "gwg": int(gwg), "otg": int(otg)})
        for p in (r["p2"], r["p3"]):
            if p:
                rows.append({**base, "player_id": p, "goals": 0, "assists": 1, "gwg": 0, "otg": 0})
    if not rows:
        return pl.DataFrame(schema={"game_id": pl.Int64, "date": pl.Utf8, "player_id": pl.Int64, "goals": pl.Int64,
                                    "assists": pl.Int64, "points": pl.Int64, "gwg": pl.Int64, "otg": pl.Int64})
    df = pl.DataFrame(rows).group_by("game_id", "date", "player_id").agg(pl.col("goals", "assists", "gwg", "otg").sum())
    return df.with_columns(points=pl.col("goals") + pl.col("assists"), otg3=pl.col("otg"))


def _ours(pid: int, logs, through: str, first: int | None) -> tuple[int, int] | None:
    """(goals, points) from our logs through a date, counting seasons from `first` on."""
    games = logs.get(pid)
    if games is None:
        return None
    use = [x for x in games if x[1] <= through and (first is None or int(x[0]) >= first)]
    return sum(x[3] for x in use), sum(x[4] for x in use)


_COVERAGE: dict = {}


def coverage(snap: dict, name: str, season: int, logs) -> str | None:
    """The latest date through which the snapshot's list agrees with our logs (goals and points of checked players).
    The same for every game of a run, so it is worked out once per snapshot and list."""
    key = (id(snap), snap.get("fetched"), name, season, id(logs))
    hit = _COVERAGE.get(key)
    if hit is None or hit[0] is not snap or hit[1] is not logs:
        hit = _COVERAGE[key] = (snap, logs, _coverage(snap, name, season, logs))
    return hit[2]


def _coverage(snap: dict, name: str, season: int, logs) -> str | None:
    from pipeline.ingest import records
    first = records.LISTS[name][2]
    rows = [r for r in snap["lists"].get(name, []) if r["playerId"] in logs][:CHECK]
    if len(rows) < 3:
        return None
    import time as _t
    taken = _t.strftime("%Y-%m-%d", _t.gmtime(snap["fetched"]))
    dates = sorted({d for d in season_counts(season)["date"].to_list() if d <= taken}, reverse=True) + [f"{season}-07-01"]
    for d in dates:
        ok = 0
        for r in rows:
            got = _ours(r["playerId"], logs, d, first)
            ok += got == (r.get("goals"), r.get("points"))
        if ok == len(rows):
            return d
    return None


def moves(gid: int, day: str, tonight_pids: list[int], N: dict, season: int, logs) -> list[dict]:
    """Items for players who passed or drew level with someone near the top of a league-wide list tonight. Uses the
    latest snapshot, wound back or forward to the night before this game with our own game counts, so a game's story
    is the same whenever its post-game page is rebuilt."""
    from pipeline.ingest import names as names_, records
    snaps = records.league_snapshots()
    snap = snaps[-1] if snaps else None
    if not snap:
        return []
    counts = season_counts(season)
    night = counts.filter(pl.col("game_id") == gid)
    if night.is_empty():
        return []
    out, done = [], set()
    for name in ORDER:
        rows = snap["lists"].get(name) or []
        cov = coverage(snap, name, season, logs) if rows else None
        if not cov:
            continue
        since = counts.filter((pl.col("date") > cov) & (pl.col("date") < day)).group_by("player_id").agg(pl.col(name).sum())
        back = counts.filter((pl.col("date") >= day) & (pl.col("date") <= cov)).group_by("player_id").agg(pl.col(name).sum())
        add = dict(zip(since["player_id"].to_list(), since[name].to_list()))
        for k, v in zip(back["player_id"].to_list(), back[name].to_list()):
            add[k] = add.get(k, 0) - v
        tn = dict(zip(night["player_id"].to_list(), night[name].to_list()))
        field = STAT[name]
        tot = {r["playerId"]: (r.get(field) or 0) + add.get(r["playerId"], 0) for r in rows}
        for pid in tonight_pids:
            if pid in done or not tn.get(pid) or pid not in tot or not N.get(pid):
                continue
            b = tot[pid]
            a = b + tn[pid]
            others = [(rid, rb, rb + tn.get(rid, 0), r) for r in rows if (rid := r["playerId"]) != pid for rb in (tot[rid],)]
            rank = 1 + sum(1 for _, _, ra, _ in others if ra > a)
            passed = [r for _, rb, ra, r in others if b <= rb and a > ra]
            tied = [r for _, rb, ra, r in others if b < rb and a == ra]
            big = name in ("goals", "points", "assists")
            level = [r for _, _, ra, r in others if ra == a]
            standing = not big and rank <= STANDING_TOP  # where he stands is news on its own on the short lists
            if rank > TOP[name] or not (passed or tied and rank <= TIE_TOP or standing):
                continue

            def who(r):
                if N.get(r["playerId"]):
                    return N[r["playerId"]]
                if r.get("skaterFullName"):
                    return r["skaterFullName"]
                f, l = names_.spell(r["playerId"], r.get("firstName"), r.get("lastName"))
                return f"{f} {l}"
            unit = " points" if name == "points" else ""
            usa = _usa_leader(name, pid, b, a, others, N, who)
            if usa:  # "the NHL record for U.S.-born players" is the story, not a place on the whole list (Kane, 2026-10-15)
                out.append({"tag": "NHL history", "text": usa, "score": 90 - rank / 2, "pid": pid})
                done.add(pid)
                continue
            if standing:
                ahead = [r for _, _, ra, r in others if ra > a]
                phrase = ("the most" if rank == 1 and not level else f"tied for the most with {who(level[0])}" if rank == 1 and len(level) == 1
                          else "tied for the most" if rank == 1 else f"second only to {who(ahead[0])}" if rank == 2 and not level
                          else f"tied with {who(level[0])} for {_ord(rank)}" if len(level) == 1 else f"tied for {_ord(rank)}" if level
                          else f"the {_ord(rank)} most")
                hist = "" if name == "otg3" or phrase.startswith("second only") or phrase.startswith("tied with") else " in NHL history"
                if name == "otg3" and phrase.startswith(("tied", "second")):
                    phrase += " in that time"
                what = {"otg3": "overtime goals since the NHL went to 3-on-3 overtime in 2015-16", "otg": "career overtime goals",
                        "gwg": "career game-winning goals"}[name]
                text = f"{N[pid]} has {a:,} {what}, {phrase}{hist}."
            elif rank == 1 and passed and not tied:
                text = f"{N[pid]} passed {who(passed[0])} for first {WHERE[name]}, with {a:,}{unit}."
            elif passed and (len(passed) <= 2 or not tied):
                passed.sort(key=lambda r: -(r.get(field) or 0))
                over = who(passed[0]) if len(passed) == 1 else f"{who(passed[-1])} and {who(passed[0])}" if len(passed) == 2 else f"{_num(len(passed))} players"
                place = _ord(rank) if not tied else f"a tie for {_ord(rank)}"
                text = f"{N[pid]} passed {over} for {place} {WHERE[name]}, with {a:,}{unit}."
            elif passed:
                text = f"{N[pid]} moved into a tie for {_ord(rank)} {WHERE[name]}, with {a:,}{unit}."
            else:
                text = f"{N[pid]} tied {who(tied[0])} for {_ord(rank)} {WHERE[name]}, with {a:,}{unit}."
            out.append({"tag": "NHL history", "text": text, "score": (86 if big else 74) - rank / 2, "pid": pid})
            done.add(pid)
    return sorted(out, key=lambda x: -x["score"])[:2]


def _usa_leader(name: str, pid: int, b: int, a: int, others: list, N: dict, who) -> str | None:
    """A U.S.-born player who tonight passed the U.S.-born leader on goals, assists or points: the all-time record for
    American players. Needs birth countries for the list (`names.fill_countries`); a list without them says nothing."""
    from pipeline.ingest import names
    if name not in ("goals", "assists", "points"):
        return None
    c = names.countries()
    if c.get(str(pid)) != "USA":
        return None
    usa = [(ra, rb, r) for _, rb, ra, r in others if c.get(str(r["playerId"])) == "USA"]
    if not usa:
        return None
    best, was, lead = max(usa, key=lambda x: x[0])
    if a <= best or b > was:  # passed him tonight (not a record he already held)
        return None
    what = {"goals": "goals", "assists": "assists", "points": "points"}[name]
    return f"{N[pid]} set the NHL record for {what} by a U.S.-born player, passing {who(lead)}, with {a:,}."


def _ord(n: int) -> str:
    from pipeline.export.stories import ORDINAL_N
    if n <= 10:
        return ORDINAL_N(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _num(n: int) -> str:
    from pipeline.export.stories import num
    return num(n)
