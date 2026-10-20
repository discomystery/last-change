"""Forward lines in the order fans write them: left wing, center, right wing (Sora, 2026-10-20).

Listed positions are not enough: Seth Jarvis is listed as a center and plays left wing, and a winger moves to the middle
when a center is out (Martinook for Jankowski and Staal). So each spot goes through a series of checks, and the first
one that can tell the players apart decides it.

Center, in order:
  1. Draws taken while these three were on the ice together, over the team's last ten games: the one who took most
     (3+ draws, more than either linemate).
  2. Draws per game over the same games, whoever he played with: the busiest faceoff man (2+ a game, ahead of the
     other two by half a draw a game).
  3. NHL.com's latest projected lineup that has him in the middle of one of this club's lines.
  4. The only listed center of the three.
  5. Otherwise the order the line came in.

Wings, in order (the two left over):
  1. NHL.com's latest projected lineup that has him on this club's left or right side.
  2. Handedness, when one shoots left and the other right: the left shot on the left.
  3. Listed position: a listed left winger goes left, a listed right winger right (so Jarvis, a listed center, goes
     left beside Jackson Blake, a listed right winger; both shoot right).
  4. Which side of the ice he is usually on: his 5-on-5 shots, hits, takeaways and giveaways near the boards (more
     than 20 ft from the middle) this season and last; the one more often on the left goes left.
  5. Otherwise the order the line came in.

Tested on NHL.com's projected lines for 2026-27 (170 lines in 22 games, each ordered with what was known the day
before and without NHL.com's own earlier projections): the center matched 94% of the time. For the wings, over the 133
different winger pairs, handedness put them on NHL.com's sides 60 times in 67 (90%), listed positions 67 in 90 (74%)
and the side of the ice 99 in 133 (74%); in this order the checks get 101 of 133 right, more than any other order
but one (102, side before listed position, which is harder to explain).

Everything is counted through the given day, so a game's lines are ordered with what was known by then."""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from datetime import date, timedelta

import polars as pl

from pipeline.config import RAW, TABLES

WINDOW = 10            # team games looked back for faceoffs
TRIO_DRAWS = 3         # draws together before rule 1 can pick the center
DRAWS_PG = 2.0         # draws a game before rule 2 can pick the center
DRAWS_MARGIN = 0.5     # and how far ahead of the next linemate he must be
BOARDS_FT = 20         # events this far from the middle of the ice count toward a player's side
SIDE_EVENTS = 20       # events near the boards before rule 4 can say
FORWARD = ("C", "L", "R")


class LineOrder:
    def __init__(self, season: int):
        self.season = season
        d = TABLES / str(season)
        games = pl.read_parquet(d / "games.parquet", columns=["game_id", "date"]) if (d / "games.parquet").exists() else pl.DataFrame(schema={"game_id": pl.Int64, "date": pl.String})
        self.date = dict(games.iter_rows())
        roster = pl.read_parquet(d / "players.parquet", columns=["game_id", "player_id", "team_id", "pos"]) if (d / "players.parquet").exists() else None
        # Each team's games in order, and who dressed in each.
        self.games: dict[int, list[tuple[str, int]]] = defaultdict(list)
        self.pos_game: dict[int, str] = {}
        dressed: dict[tuple[int, int], int] = {}
        self.dressed: dict[tuple[int, int], set] = defaultdict(set)
        if roster is not None:
            for (gid, tid), _ in roster.group_by("game_id", "team_id"):
                if gid in self.date:
                    self.games[tid].append((self.date[gid], gid))
            for g in self.games.values():
                g.sort()
            for gid, pid, tid, pos in roster.iter_rows():
                self.pos_game[pid] = pos
                dressed[(gid, pid)] = tid
                self.dressed[(gid, tid)].add(pid)
        # Draws: per game and team, who took them with which forwards on the ice, and each player's total.
        self.trio_draws: dict[tuple[int, int], dict[frozenset, Counter]] = defaultdict(lambda: defaultdict(Counter))
        self.draws: dict[tuple[int, int], Counter] = defaultdict(Counter)
        self.side: Counter = Counter()  # player -> events near the left boards minus the right, this season and last
        self.side_n: Counter = Counter()
        for s in (season - 1, season):
            p = TABLES / str(s) / "events.parquet"
            if not p.exists():
                continue
            ev = pl.read_parquet(p, columns=["game_id", "type", "is_home", "p1", "p2", "y_norm", "situation_code", "home_on", "away_on"])
            near = ev.filter(pl.col("type").is_in(["shot-on-goal", "missed-shot", "goal", "hit", "takeaway", "giveaway"]) & (pl.col("situation_code") == "1551")
                             & pl.col("p1").is_not_null() & (pl.col("y_norm").abs() > BOARDS_FT))
            for pid, y in near.select("p1", "y_norm").iter_rows():
                self.side[pid] += 1 if y > 0 else -1  # positive y is the attacking player's left
                self.side_n[pid] += 1
            if s != season:
                continue
            fo = ev.filter((pl.col("type") == "faceoff") & pl.col("p1").is_not_null() & pl.col("p2").is_not_null() & pl.col("is_home").is_not_null())
            for gid, is_home, p1, p2, home_on, away_on in fo.select("game_id", "is_home", "p1", "p2", "home_on", "away_on").iter_rows():
                # The play-by-play files a faceoff under the winner: p1 won, p2 lost.
                for taker, on in ((p1, home_on if is_home else away_on), (p2, away_on if is_home else home_on)):
                    tid = dressed.get((gid, taker))
                    if tid is None:
                        continue
                    self.draws[(gid, tid)][taker] += 1
                    fwd = frozenset(p for p in (on or []) if self.pos_game.get(p) in FORWARD and dressed.get((gid, p)) == tid)
                    if len(fwd) == 3 and taker in fwd:
                        self.trio_draws[(gid, tid)][fwd][taker] += 1
        # Listed position and handedness: the latest roster listing, else the game sheets.
        self.listed: dict[int, str] = dict(self.pos_game)
        self.shoots: dict[int, str] = {}
        for s in (season - 1, season):
            p = TABLES / str(s) / "rosters.parquet"
            if p.exists():
                for pid, pos, shoots in pl.read_parquet(p, columns=["date", "player_id", "pos", "shoots"]).sort("date").select("player_id", "pos", "shoots").iter_rows():
                    if pos:
                        self.listed[pid] = pos
                    if shoots:
                        self.shoots[pid] = shoots
        # The last day this run knows about: the day after the latest game played, so today's NHL.com projections count.
        last = max((g[-1][0] for g in self.games.values() if g), default="0000-00-00")
        self.horizon = (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last[0] != "0" else last
        self.nhl = self._projections()

    def _projections(self) -> dict[int, list[tuple[str, list[list[int]]]]]:
        """NHL.com's projected forward lines per team id (by date), written left wing, center, right wing."""
        out: dict[int, list] = defaultdict(list)
        folder = RAW / "nhl_previews" / str(self.season)
        if not folder.exists():
            return out
        teams = self._team_ids()
        for f in sorted(folder.glob("*.json.gz")):
            try:
                got = json.loads(gzip.open(f).read())
            except (OSError, ValueError):
                continue
            gid = int(f.name.split(".")[0])
            day = self.date.get(gid) or (got.get("fetched") or "")[:10]
            if day > self.horizon:
                continue  # a game after the tables' last day plus one (a preview rebuilt from cut tables reads them all)
            for abbr, lu in (got.get("lineups") or {}).items():
                tid = teams.get(abbr)
                lines = [x for x in lu.get("lines") or [] if len(x) == 3]
                if tid is not None and lines:
                    out[tid].append((day, lines))
        for v in out.values():
            v.sort(key=lambda x: x[0])
        return out

    def _team_ids(self) -> dict[str, int]:
        p = TABLES / str(self.season) / "games.parquet"
        if not p.exists():
            return {}
        g = pl.read_parquet(p, columns=["home", "home_id", "away", "away_id"])
        return {**dict(g.select("home", "home_id").iter_rows()), **dict(g.select("away", "away_id").iter_rows())}

    def _window(self, tid: int, day: str | None) -> list[int]:
        games = [gid for d, gid in self.games.get(tid, []) if day is None or d <= day]
        return games[-WINDOW:]

    def _slot(self, tid: int, pid: int, day: str | None) -> int | None:
        """Where NHL.com's latest projection (through `day`) put him on one of this club's lines: 0 left, 1 center, 2 right."""
        for d, lines in reversed(self.nhl.get(tid, [])):
            if day is not None and d > day:
                continue
            for line in lines:
                if pid in line:
                    return line.index(pid)
        return None

    def center(self, tid: int, ids: list[int], day: str | None = None) -> int | None:
        window = self._window(tid, day)
        trio = frozenset(ids)
        together = Counter()
        for gid in window:
            together.update(self.trio_draws.get((gid, tid), {}).get(trio, Counter()))
        top = together.most_common()
        if top and top[0][1] >= TRIO_DRAWS and (len(top) == 1 or top[0][1] > top[1][1]):
            return top[0][0]
        per_game = {}
        for p in ids:
            played = [gid for gid in window if p in self.dressed.get((gid, tid), ())]
            per_game[p] = sum(self.draws.get((gid, tid), Counter())[p] for gid in played) / len(played) if played else 0.0
        ranked = sorted(ids, key=lambda p: -per_game[p])
        if per_game[ranked[0]] >= DRAWS_PG and per_game[ranked[0]] - per_game[ranked[1]] >= DRAWS_MARGIN:
            return ranked[0]
        mid = [p for p in ids if self._slot(tid, p, day) == 1]
        if len(mid) == 1:
            return mid[0]
        listed = [p for p in ids if self.listed.get(p) == "C"]
        if len(listed) == 1:
            return listed[0]
        return None

    def wings(self, tid: int, a: int, b: int, day: str | None = None) -> tuple[int, int]:
        """The two wingers as (left, right)."""
        sa, sb = self._slot(tid, a, day), self._slot(tid, b, day)
        if sa in (0, 2) and sb != sa:
            return (a, b) if sa == 0 else (b, a)
        if sb in (0, 2):
            return (b, a) if sb == 0 else (a, b)
        ha, hb = self.shoots.get(a), self.shoots.get(b)
        if ha in ("L", "R") and hb in ("L", "R") and ha != hb:
            return (a, b) if ha == "L" else (b, a)
        la, lb = self.listed.get(a), self.listed.get(b)
        if (la == "L") != (lb == "L"):
            return (a, b) if la == "L" else (b, a)
        if (la == "R") != (lb == "R"):
            return (b, a) if la == "R" else (a, b)
        lean = lambda p: self.side[p] / self.side_n[p] if self.side_n[p] >= SIDE_EVENTS else 0.0
        if lean(a) != lean(b):
            return (a, b) if lean(a) > lean(b) else (b, a)
        return a, b

    def order(self, tid: int, ids, day: str | None = None) -> list[int]:
        """A forward line as [left wing, center, right wing]; anything that isn't a trio comes back as it was."""
        ids = [int(p) for p in ids]
        if len(ids) != 3 or len(set(ids)) != 3:
            return ids
        c = self.center(tid, ids, day)
        if c is None:
            c = ids[1]
        left, right = self.wings(tid, *[p for p in ids if p != c], day)
        return [left, c, right]


_built: dict[int, tuple[tuple, LineOrder]] = {}


def for_season(season: int) -> LineOrder:
    """One LineOrder per season, rebuilt when the tables it reads have been rewritten since."""
    stamp = tuple((TABLES / str(season) / f).stat().st_mtime if (TABLES / str(season) / f).exists() else None
                  for f in ("players.parquet", "events.parquet", "rosters.parquet"))
    if season not in _built or _built[season][0] != stamp:
        _built[season] = (stamp, LineOrder(season))
    return _built[season][1]


def order(season: int, tid: int, ids, day: str | None = None) -> list[int]:
    return for_season(season).order(tid, ids, day)
