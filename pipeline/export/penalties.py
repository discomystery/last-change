"""Penalty stories: the "whoa" moments of a game's penalty sheet, told in a sentence or two.

Sora asked (2026-10-12) for post-game pages to notice when the whistle did something unusual: a game with hardly any
penalties or a pile of them, fights, line brawls and scrums, misconducts, and one player taking a bunch of penalties
(above all when he took most of a quiet game's). Previews mention bad blood carried over from the teams' last meeting.

Everything reads the play-by-play penalty rows (`events.parquet`: pen_type MIN / MAJ / BEN / MIS / GAM / MAT / PS,
pen_desc like "roughing" or "fighting", p1 = the player who took it). Shootouts and penalty shots are left out.

  penalties called   minors, majors, bench minors and match penalties, not counting fighting majors, instigator minors
                     or misconducts (those ride along with something else). "Few" and "many" come from the 2023-24 to
                     2025-26 regular seasons: few = the largest count that 5% of games or fewer stayed at or under (2),
                     many = the smallest count that 3% of games or fewer reached (14). Measured 2026-10-12: average 6.6
                     a game; 183 of 3,936 games had 2 or fewer, 97 had 14 or more.
  one whistle        every penalty handed out at the same moment of the same game. A line brawl is three or more fights
                     at once (3 times in 3,936 games); a brawl sends six or more players to the box at once (27 times),
                     a scrum four or more players for roughing at once; two fights at once are told as such.
  misconducts        game misconducts and match penalties are always named; a ten-minute misconduct is named when it is
                     not part of a scrum already told (scrums at the horn hand them out by the handful). A head coach's
                     game misconduct names the coach from coaches.parquet.
  one player         three or more penalties called on one player (about 1 game in 18), or two or more of a quiet
                     game's penalties (3 or fewer called).
"""
import zlib
from functools import lru_cache

import polars as pl

from pipeline.config import FULL_SEASONS, REGULAR, TABLES

CALLED = ["MIN", "MAJ", "BEN", "MAT"]
NOT_CALLED = ["fighting", "instigator"]  # ride along with a fight
EJECTIONS = ["GAM", "MAT"]
FEW_SHARE, MANY_SHARE = 0.05, 0.03
LINE_BRAWL, BRAWL_PLAYERS, BRAWL_ROUGHING = 3, 6, 4
BUSY_PLAYER, QUIET_GAME = 3, 3
PERIODS = {1: "first", 2: "second", 3: "third"}
WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:]


@lru_cache(maxsize=None)
def rows(season: int) -> pl.DataFrame:
    """Every penalty of the season's regular-season games (penalty shots and shootouts left out)."""
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return pl.DataFrame()
    g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
    return (pl.read_parquet(d / "events.parquet", columns=["game_id", "type", "period", "period_type", "sec", "team_id", "p1",
                                                           "pen_type", "pen_desc", "pen_min"])
            .filter((pl.col("type") == "penalty") & (pl.col("pen_type") != "PS") & (pl.col("period_type") != "SO"))
            .join(g, on="game_id").drop("type"))


def called(p: pl.DataFrame) -> pl.DataFrame:
    return p.filter(pl.col("pen_type").is_in(CALLED) & ~pl.col("pen_desc").is_in(NOT_CALLED))


@lru_cache(maxsize=1)
def norms() -> dict:
    """How many penalties a normal game has, from the full seasons: the few / many lines and every count's rarity."""
    counts = []
    for s in FULL_SEASONS:
        d = TABLES / str(s)
        if not (d / "games.parquet").exists():
            continue
        games = (pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
                 .select("game_id"))
        n = games.join(called(rows(s)).group_by("game_id").len(), on="game_id", how="left").fill_null(0)["len"].to_list()
        counts += n
    if not counts:
        return {"few": -1, "many": 10 ** 6, "games": 0, "at_most": {}, "at_least": {}}
    total, top = len(counts), max(counts)
    at_most = {k: sum(c <= k for c in counts) / total for k in range(top + 1)}
    at_least = {k: sum(c >= k for c in counts) / total for k in range(top + 1)}
    few = max((k for k, v in at_most.items() if v <= FEW_SHARE), default=-1)
    many = min((k for k, v in at_least.items() if v <= MANY_SHARE), default=10 ** 6)
    return {"few": few, "many": many, "games": total, "at_most": at_most, "at_least": at_least,
            "mean": sum(counts) / total}


def _every(share: float) -> str:
    """'about once every 40 games', rounded the way a person would say it."""
    if share <= 0:
        return "almost never"
    n = 1 / share
    n = round(n / 10) * 10 if n >= 30 else round(n / 5) * 5 if n >= 10 else round(n)
    return f"about once every {n} games"


def whistles(p: pl.DataFrame) -> list[dict]:
    """Penalties handed out at one moment, in order: how many players, fights, roughing minors, misconducts."""
    out = []
    for (period, sec), g in p.group_by("period", "sec", maintain_order=True):
        out.append({"period": period, "sec": sec, "players": g["p1"].drop_nulls().n_unique(),
                    "fights": int((g["pen_desc"] == "fighting").sum()) // 2,
                    "fighters": g.filter(pl.col("pen_desc") == "fighting").select("p1", "team_id").rows(),
                    "roughing": g.filter(pl.col("pen_desc").str.starts_with("roughing"))["p1"].drop_nulls().n_unique(),
                    "misconducts": int(g["pen_type"].is_in(["MIS", "GAM", "MAT"]).sum())})
    return sorted(out, key=lambda w: w["sec"])


def _when(period: int, sec: int) -> str:
    """'early in the second', 'at the end of the second', 'in overtime'."""
    s = sec - 1200 * (period - 1)
    if period > 3:
        return "in overtime"
    if s >= 1195:
        return f"at the end of the {PERIODS[period]}" if period < 3 else "at the final horn"
    if period == 3 and s >= 1080:
        return "in the final minutes"
    part = "early in" if s < 400 else "midway through" if s < 800 else "late in"
    return f"{part} the {PERIODS[period]}"


def _coach(gid: int, season: int, team: str) -> str | None:
    f = TABLES / str(season) / "coaches.parquet"
    if not f.exists():
        return None
    c = pl.read_parquet(f).filter((pl.col("game_id") == gid) & (pl.col("team") == team))
    return c["coach"][0] if c.height else None


def recap_notes(gid: int, season: int, teams: dict[int, str], W, name) -> list[str]:
    """At most two sentences about a finished game's penalties, only when something stood out.

    teams maps team id to abbreviation; W is stories.Words; name(pid) gives a player's name the way the story is
    naming people (full the first time, last name after)."""
    p = rows(season).filter(pl.col("game_id") == gid)
    if p.is_empty() and not norms()["games"]:
        return []
    N = norms()
    c = called(p)
    total = c.height
    notes: list[tuple[int, str]] = []
    told: set[int] = set()  # players already in a sentence, so a fighter isn't told twice
    # one whistle: line brawls and scrums
    ws = whistles(p)
    big = [w for w in ws if w["fights"] >= LINE_BRAWL or w["players"] >= BRAWL_PLAYERS or w["roughing"] >= BRAWL_ROUGHING]
    scrum_secs = {w["sec"] for w in big}
    for w in sorted(big, key=lambda w: (-w["fights"], -w["players"]))[:1]:
        when = _when(w["period"], w["sec"])
        extra = ([f"{num(w['fights'])} {'fight' if w['fights'] == 1 else 'fights'}"] if w["fights"] and w["fights"] < LINE_BRAWL else []) + \
                ([f"{num(w['misconducts'])} misconducts"] if w["misconducts"] >= 2 else [])
        mis = f", with {_and(extra)}" if extra else ""
        if w["fights"] >= LINE_BRAWL:
            notes.append((100, f"A line brawl broke out {when}: {num(w['fights'])} fights at once{mis}."))
        elif w["players"] >= BRAWL_PLAYERS:
            notes.append((95, f"Things boiled over {when}, when one scrum sent {num(w['players'])} players to the box{mis}."))
        else:
            notes.append((76, f"A scrum {when} sent {num(w['players'])} players to the box{mis}."))
        told.update(pid for pid, _ in w["fighters"])
    # a head coach thrown out
    for r in p.filter(pl.col("pen_desc") == "game-misconduct-head-coach").iter_rows(named=True):
        team = teams.get(r["team_id"])
        coach = _coach(gid, season, team) if team else None
        who = f"{W.poss(team)} head coach, {coach}," if coach else f"{_cap(W.poss(team))} head coach" if team else "A head coach"
        notes.append((92, f"{_cap(who)} was thrown out {_when(r['period'], r['sec'])}."))
    # ejections: game misconducts and match penalties (players)
    ej = p.filter(pl.col("pen_type").is_in(EJECTIONS) & pl.col("p1").is_not_null() & ~pl.col("sec").is_in(list(scrum_secs)))
    ej_names = []
    for r in ej.unique("p1", keep="first", maintain_order=True).iter_rows(named=True):
        ej_names.append((r, name(r["p1"])))
        told.add(r["p1"])
    if ej_names:
        r, who = ej_names[0]
        what = "a match penalty" if r["pen_type"] == "MAT" else "a game misconduct"
        if len(ej_names) == 1:
            notes.append((90, f"{who} was thrown out with {what} {_when(r['period'], r['sec'])}."))
        else:
            notes.append((90, f"{_and([n for _, n in ej_names])} were {'both' if len(ej_names) == 2 else 'all'} thrown out of the game."))
    # fights not already told in a brawl
    fights = [w for w in ws if w["fights"] and w["sec"] not in scrum_secs]
    pairs = []
    for w in fights:
        by_team: dict[int, list[int]] = {}
        for pid, tid in w["fighters"]:
            by_team.setdefault(tid, []).append(pid)
        sides = list(by_team.values())
        for i in range(w["fights"]):
            if len(sides) == 2 and i < min(len(sides[0]), len(sides[1])):
                pairs.append((w, sides[0][i], sides[1][i]))
    if pairs:
        if len(pairs) == 1:
            w, a, b = pairs[0]
            verb = ["dropped the gloves", "squared off", "fought"][zlib.crc32(str(gid).encode()) % 3]
            notes.append((70, f"{name(a)} and {name(b)} {verb} {_when(w['period'], w['sec'])}."))
        elif len(pairs) == 2:
            (w1, a1, b1), (w2, a2, b2) = pairs
            same = w1["sec"] == w2["sec"]
            notes.append((80, f"There were two fights{' at once' if same else ''}: {name(a1)} with {name(b1)}, and {name(a2)} with {name(b2)}."))
        else:
            notes.append((85, f"There were {num(len(pairs))} fights in all."))
        told.update(x for _, a, b in pairs for x in (a, b))
    # how many penalties were called, and who took them
    per = c.filter(pl.col("p1").is_not_null()).group_by("p1").agg(n=pl.len()).sort("n", descending=True)
    busy = per.row(0, named=True) if per.height else None
    if total <= N["few"]:
        if total == 0:
            notes.append((75, "Not a single penalty was called all night."))
        elif busy and busy["n"] >= 2 and busy["n"] == total:
            notes.append((82, f"Only {num(total)} penalties were called all night, and {name(busy['p1'])} took {'both' if total == 2 else 'all ' + num(total)}."))
            told.add(busy["p1"])
        else:
            one = "penalty was" if total == 1 else "penalties were"
            notes.append((60, f"Only {num(total)} {one} called all night, something the league sees {_every(N['at_most'].get(total, 0))}."))
    elif total >= N["many"]:
        rare = N["at_least"].get(total, 0)
        tail = f", a total the league sees {_every(rare)}" if rare > 0 else ", more than any game since 2023-24"
        notes.append((65, f"The officials called {num(total)} penalties{tail}."))
    if busy and busy["p1"] not in told:
        if busy["n"] >= 2 and total <= QUIET_GAME:
            notes.append((78, f"Only {num(total)} penalties were called, and {num(busy['n'])} of them were {name(busy['p1'])}’s."))
        elif busy["n"] >= BUSY_PLAYER:
            share = f" of the game’s {num(total)}" if busy["n"] * 2 > total else ""
            notes.append((68, f"{name(busy['p1'])} took {num(busy['n'])} penalties{share} on his own."))
    # ten-minute misconducts not already part of a scrum or an ejection
    tens = p.filter((pl.col("pen_desc") == "misconduct") & pl.col("p1").is_not_null() & ~pl.col("sec").is_in(list(scrum_secs))
                    & ~pl.col("p1").is_in(list(told)))
    tens = tens.unique("p1", keep="first", maintain_order=True)
    if 1 <= tens.height <= 2:
        who = _and([name(x) for x in tens["p1"].to_list()])
        notes.append((50, f"{who} picked up {'a ten-minute misconduct' if tens.height == 1 else 'ten-minute misconducts'}."))
    elif tens.height >= 3:
        notes.append((72, f"{_cap(num(tens.height))} players picked up ten-minute misconducts."))
    notes.sort(key=lambda n: -n[0])
    return [t for _, t in notes[:2]]


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def bad_blood(gid: int, season: int, N: dict[int, str]) -> dict | None:
    """A preview item when the teams' last meeting had a brawl, two or more fights, or someone thrown out."""
    p = rows(season).filter(pl.col("game_id") == gid)
    if p.is_empty():
        return None
    ws = whistles(p)
    fights = sum(w["fights"] for w in ws)
    big = next((w for w in ws if w["fights"] >= LINE_BRAWL or w["players"] >= BRAWL_PLAYERS or w["roughing"] >= BRAWL_ROUGHING), None)
    ej = p.filter(pl.col("pen_type").is_in(EJECTIONS) & pl.col("p1").is_not_null())
    last = "Last time these two met"
    if big and big["fights"] >= LINE_BRAWL:
        return {"tag": "Bad blood", "score": 74, "text": f"{last}, a line brawl broke out {_when(big['period'], big['sec'])}."}
    if big:
        return {"tag": "Bad blood", "score": 72 if big["players"] >= BRAWL_PLAYERS else 62,
                "text": f"{last}, one scrum sent {num(big['players'])} players to the box."}
    if fights >= 2:
        return {"tag": "Bad blood", "score": 70, "text": f"{last}, there were {num(fights)} fights."}
    if ej.height:
        r = ej.row(0, named=True)
        who = N.get(r["p1"])
        if who:
            what = "a match penalty" if r["pen_type"] == "MAT" else "a game misconduct"
            return {"tag": "Bad blood", "score": 66, "text": f"{last}, {who} was thrown out with {what}."}
    if fights == 1:
        w = next(w for w in ws if w["fights"])
        a, b = (N.get(pid) for pid, _ in w["fighters"][:2])
        if a and b:
            return {"tag": "Bad blood", "score": 55, "text": f"{a} and {b} fought the last time these two met."}
    return None
