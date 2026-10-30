"""Goalie streaks for the post-game story (Sora, 2026-10-30: Kochetkov's nine straight wins to open 2026-27, the
longest season-opening run by any goalie in franchise history, were never mentioned on a Carolina post-game page).

A goalie's streak counts his DECISIONS, as the NHL does: a win, a regulation loss or an overtime/shootout loss. A
relief appearance without a decision neither extends nor breaks a run, a relief win counts as a win, and an overtime
or shootout loss ends a win streak (but not a run "without a regulation loss"). Ties (before 2005-06) end win runs.

This season's decisions come from our own tables (`decisions`: the boxscore's "W" / "L" / "O" when saved, else the
NHL's rule worked out from the play-by-play: the goalie in net when the winners scored the goal that put them ahead
for good gets the win, the other team's goalie in net for that goal the loss), so a rebuilt page tells the streak as
it stood that night. Earlier seasons come from the NHL's game-by-game goalie report (`ingest/goalie_games.py`).

What a night can carry (`for_game`), one per goalie:
  win      his run of straight wins after tonight, RUN_TOLD+ (5+: about one goalie a club a season); "to start the
           season" when the run is all his decisions this season
  points   a run without a regulation loss of POINT_TOLD+ decisions with an overtime loss in it ("8-0-2 in his last ten")
  ended    tonight's loss ended a win streak of RUN_TOLD+
  winless  a run of WINLESS_TOLD+ decisions without a win (told for the loser only, without blame)
Records, checked against every season the record book covers (`goalie_games.franchise_from`; claims say "in franchise
history" only when it covers the franchise's first season, else "since 1918-19"): the club's longest season-opening
run and longest run in one season, set, tied or extended tonight (clubs with CLUB_SEASONS+ seasons), and a league-wide
season-opening run that is the NHL's longest since some earlier season (LEAGUE_OPEN+, 7: about one every two seasons).
"""
from __future__ import annotations

import gzip
import json
from datetime import date as Date, timedelta
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, TABLES

RUN_TOLD = 5  # straight wins told in the story (about 27 goalies reach five in a season, 9 reach seven, 3 reach nine)
POINT_TOLD = 8  # decisions without a regulation loss, with an overtime loss in them
WINLESS_TOLD = 6  # decisions without a win
OPEN_TOLD = 5  # a run that is all his decisions this season reads "to start the season" (about 2.5 a season reach 5)
HEAD_RUN = 8  # a run this long heads the page when nothing bigger happened (about 6 goalies a season)
LEAGUE_OPEN = 7  # a season-opening run the NHL's longest since some earlier season: told with the season (0.5 a season)
LEAGUE_SINCE = 5  # ... only when that earlier season is at least this many seasons back
CLUB_SEASONS = 10  # club records only for franchises with this many seasons (as the club scoring lists)


def _season_id(season: int) -> int:
    return int(f"{season}{season + 1}")


def season_words(season: int) -> str:
    """2021 -> "2021-22"."""
    return f"{season}-{str(season + 1)[-2:]}"


@lru_cache(maxsize=8)
def decisions(season: int) -> dict[int, dict[int, str]]:
    """game_id -> {goalie: "W" / "L" / "O"} for every finished regular-season game."""
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return {}
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    ev = (pl.read_parquet(d / "events.parquet", columns=["game_id", "sort", "period_type", "type", "team_id", "home_goalie", "away_goalie"])
          .filter(pl.col("period_type") != "SO").sort("game_id", "sort"))
    by_game = ev.partition_by("game_id", as_dict=True)
    out = {}
    for g in games.iter_rows(named=True):
        gid = g["game_id"]
        box = RAW / str(season) / "box" / f"{gid}.json.gz"
        if box.exists():
            stats = json.loads(gzip.open(box).read()).get("playerByGameStats", {})
            dec = {x["playerId"]: x["decision"] for side in ("homeTeam", "awayTeam") for x in stats.get(side, {}).get("goalies", [])
                   if x.get("decision") in ("W", "L", "O")}
            if len(dec) == 2:
                out[gid] = dec
                continue
        e = by_game.get((gid,))
        if e is None or g["home_score"] == g["away_score"]:
            continue
        home_won = g["home_score"] > g["away_score"]
        wid = g["home_id"] if home_won else g["away_id"]
        wcol, lcol = ("home_goalie", "away_goalie") if home_won else ("away_goalie", "home_goalie")
        lose = min(g["home_score"], g["away_score"])
        goals = e.filter((pl.col("type") == "goal") & (pl.col("team_id") == wid))
        at = goals.row(lose, named=True) if g["last_period"] != "SO" and goals.height > lose else None

        def last_in(col):
            seen = e.filter(pl.col(col).is_not_null())
            return seen[col][-1] if seen.height else None
        w = (at or {}).get(wcol) or last_in(wcol)
        lo = (at or {}).get(lcol)
        if lo is None:  # a shootout, or the losers' net was empty for the goal: their last goalie in before it
            lo = last_in(lcol) if at is None else (e.filter((pl.col("sort") <= at["sort"]) & pl.col(lcol).is_not_null())[lcol].to_list() or [None])[-1]
        if w is not None and lo is not None:
            out[gid] = {int(w): "W", int(lo): "O" if g["last_period"] in ("OT", "SO") else "L"}
    return out


@lru_cache(maxsize=4)
def _season_log(season: int) -> pl.DataFrame:
    """This season's decisions in order: game_id, date, goalie_id, team, decision, started."""
    d = TABLES / str(season) / "games.parquet"
    if not d.exists():
        return pl.DataFrame(schema={"game_id": pl.Int64, "date": pl.Utf8, "goalie_id": pl.Int64, "team": pl.Utf8, "decision": pl.Utf8,
                                    "started": pl.Boolean})
    g = pl.read_parquet(d)
    info = {r["game_id"]: r for r in g.iter_rows(named=True)}
    side = {}
    p = TABLES / str(season) / "players.parquet"
    if p.exists():
        for gid, pid, home in pl.read_parquet(p, columns=["game_id", "player_id", "is_home"]).iter_rows():
            if gid in info:
                side[(gid, pid)] = info[gid]["home" if home else "away"]
    from pipeline.metrics import goalies
    started = {(g_, p_) for g_, p_ in goalies.per_game(season).filter(pl.col("started")).select("game_id", "goalie_id").iter_rows()}
    rows = []
    for gid, dec in decisions(season).items():
        for pid, x in dec.items():
            r = info[gid]
            team = side.get((gid, pid))
            if team is None:  # a goalie not on the skater/goalie lines: his club is the side his decision says
                won_home = r["home_score"] > r["away_score"]
                team = r["home"] if (x == "W") == won_home else r["away"]
            rows.append((gid, r["date"], pid, team, x, (gid, pid) in started))
    return pl.DataFrame(rows, schema={"game_id": pl.Int64, "date": pl.Utf8, "goalie_id": pl.Int64, "team": pl.Utf8, "decision": pl.Utf8,
                                      "started": pl.Boolean},
                        orient="row").sort("date", "game_id")


def _runs(decs: list[str]) -> dict:
    """The trailing runs of a list of decisions, oldest first."""
    def tail(ok):
        n = 0
        for x in reversed(decs):
            if not ok(x):
                break
            n += 1
        return n
    win = tail(lambda x: x == "W")
    pts = tail(lambda x: x in ("W", "O"))
    nowin = tail(lambda x: x != "W")
    rec = lambda k: [sum(1 for x in decs[len(decs) - k:] if x == c) for c in ("W", "L", "O")]
    return {"win": win, "points": pts, "points_rec": rec(pts), "winless": nowin, "winless_rec": rec(nowin), "n": len(decs)}


def _best(seqs: list[tuple[int, str, int, list[str]]]) -> tuple[int, list[tuple[int, str, int]], int, list[tuple[int, str, int]]]:
    """Over (player id, name, season, decisions) per goalie-season: the longest opening run and the longest run, with holders."""
    best_open, open_by, best_run, run_by = 0, [], 0, []
    for pid, nm, s, decs in seqs:
        o = 0
        for x in decs:
            if x != "W":
                break
            o += 1
        n = m = 0
        for x in decs:
            n = n + 1 if x == "W" else 0
            m = max(m, n)
        if o > best_open:
            best_open, open_by = o, [(pid, nm, s)]
        elif o == best_open and o:
            open_by.append((pid, nm, s))
        if m > best_run:
            best_run, run_by = m, [(pid, nm, s)]
        elif m == best_run and m:
            run_by.append((pid, nm, s))
    return best_open, open_by, best_run, run_by


@lru_cache(maxsize=64)
def _history(fid: int, before: int) -> list[tuple[int, str, int, list[str]]]:
    """(player id, name, season, decisions) for every goalie-season of a franchise inside its record book, before `before`."""
    from pipeline.ingest import goalie_games
    body = goalie_games.load(fid)
    start = goalie_games.franchise_from(fid)
    if not body or start is None:
        return []
    by = {}
    for s, _gid, _day, pid, nm, dec in body["rows"]:
        if start <= s < before:
            by.setdefault((pid, s), [nm, []])[1].append(dec)
    return [(pid, nm, s, decs) for (pid, s), (nm, decs) in by.items()]


@lru_cache(maxsize=4)
def _league_openers(before: int) -> list[tuple[int, int, str, int]]:
    """(season, longest season-opening run that season, goalie, player id), from the league's record book (a goalie's
    run follows him across clubs, as the NHL counts it)."""
    from pipeline.ingest import goalie_games
    start = goalie_games.league_from()
    if start is None:
        return []
    by = {}
    for fid in json.loads((goalie_games.DIR / "franchises.json").read_text()):
        for s, gid, day, pid, nm, dec in (goalie_games.load(fid) or {}).get("rows", []):
            if start <= s < before:
                by.setdefault((pid, s), {"nm": nm, "games": {}})["games"][gid] = (day, dec)
    out = []
    for (pid, s), v in by.items():
        o = 0
        for _, (_, dec) in sorted(v["games"].items(), key=lambda kv: (kv[1][0], kv[0])):
            if dec != "W":
                break
            o += 1
        out.append((s, o, v["nm"], pid))
    return out


def _since(fid: int) -> str | None:
    """"in franchise history", or "since 1918-19" when the record book starts later than the franchise."""
    from pipeline.ingest import goalie_games
    start, first = goalie_games.franchise_from(fid), goalie_games.first_season(fid)
    if start is None:
        return None
    return "in franchise history" if start == first else f"since {season_words(start)}"


def for_game(gid: int, season: int = CURRENT_SEASON, N: dict | None = None) -> list[dict]:
    """Streak facts for each goalie with a decision tonight: {pid, team, dec, win, opening, points, points_rec, ended,
    winless, winless_rec, record: [...], league: {...} | None, head}."""
    log = _season_log(season)
    tonight = log.filter(pl.col("game_id") == gid)
    if tonight.is_empty():
        return []
    day = tonight["date"][0]
    upto = log.filter((pl.col("date") < day) | (pl.col("game_id") == gid))
    out = []
    for pid, team, dec in tonight.select("goalie_id", "team", "decision").iter_rows():
        mine = upto.filter(pl.col("goalie_id") == pid)
        decs = mine["decision"].to_list()
        r = _runs(decs)
        before = _runs(decs[:-1])
        flags = mine["started"].to_list()
        n_starts = sum(flags)  # starts with a decision; a start without one (pulled, then a comeback) makes "decisions"
        all_starts = goalies_started(season, pid, day)
        x = {"pid": pid, "team": team, "dec": dec, "win": r["win"], "opening": r["win"] == r["n"], "points": r["points"],
             # "starts" when every game of a run was his start, else "decisions"; "all nine of his starts this season"
             # only when he has no other start and no relief decision
             "unit": {k: "starts" if all(flags[len(flags) - r[k]:]) else "decisions" for k in ("win", "points", "winless")},
             "every_start": n_starts == len(flags) == all_starts,
             "points_rec": r["points_rec"], "ended": before["win"] if dec != "W" else 0, "ended_opening": before["win"] == before["n"],
             "winless": r["winless"], "winless_rec": r["winless_rec"], "record": [], "league": None}
        if dec == "W" and r["win"] >= OPEN_TOLD:
            x["record"] = _club_record(pid, team, season, day, gid, mine, upto, N)
        if dec == "W" and x["opening"] and r["win"] >= LEAGUE_OPEN:
            x["league"] = _league_record(pid, season, r["win"], upto)
        out.append(x)
    return out


def goalies_started(season: int, pid: int, day: str) -> int:
    """His starts this season through `day`."""
    return int(_starts(season).filter((pl.col("goalie_id") == pid) & (pl.col("date") <= day)).height)


@lru_cache(maxsize=4)
def _starts(season: int) -> pl.DataFrame:
    from pipeline.metrics import goalies
    return goalies.per_game(season).filter(pl.col("started")).select("goalie_id", "date")


def _club_record(pid, team, season, day, gid, mine, upto, N) -> list[dict]:
    """Tonight's run against the club's record book: the longest season-opening run and the longest run in one season."""
    from pipeline.ingest import records
    fid = records.franchise_of(str(_season_id(season)), team)
    if not fid or records.seasons_of(fid) < CLUB_SEASONS:
        return []
    since = _since(fid)
    if since is None:
        return []
    club = mine.filter(pl.col("team") == team)["decision"].to_list()  # a record counts his games for this club only
    run = _runs(club)["win"]
    opening = run == len(club) and run == mine.height
    names = N or {}
    seqs = list(_history(fid, season))
    for other, rows in upto.filter((pl.col("team") == team) & (pl.col("goalie_id") != pid)).group_by("goalie_id"):
        seqs.append((other[0], names.get(other[0], ""), season, rows.sort("date", "game_id")["decision"].to_list()))
    earlier = club[:len(club) - run]  # his own earlier runs this season count against tonight's
    if earlier:
        seqs.append((pid, names.get(pid, ""), season, earlier))
    best_open, open_by, best_run, run_by = _best(seqs)
    out = []
    for kind, mark, by, applies in (("opening", best_open, open_by, opening), ("run", best_run, run_by, True)):
        if not applies or not mark or run < mark:
            continue
        how = "set" if run == mark + 1 else "extended" if run > mark + 1 else "tied"
        out.append({"kind": kind, "how": how, "mark": mark, "run": run, "by": by, "since": since})
    return out


def _league_record(pid, season, run, upto) -> dict | None:
    """The NHL's longest season-opening run since some earlier season: {since, mark, by} for the last season with one
    at least as long (this season's other runs count too)."""
    from pipeline.ingest import goalie_games
    past = _league_openers(season)
    if not past:
        return None
    others = []
    for other, rows in upto.filter(pl.col("goalie_id") != pid).group_by("goalie_id"):
        o = 0
        for x in rows.sort("date", "game_id")["decision"].to_list():
            if x != "W":
                break
            o += 1
        others.append(o)
    if any(o >= run for o in others):
        return None
    longer = [(s, o, nm) for s, o, nm, _ in past if o >= run]
    start = goalie_games.league_from()
    if not longer:
        return {"since": None, "from": start, "mark": None, "by": []}
    last = max(s for s, _, _ in longer)
    if season - last < LEAGUE_SINCE:
        return None
    by = [(p, nm, o) for s, o, nm, p in past if o >= run and s == last]
    return {"since": last, "from": start, "mark": max(o for _, _, o in by), "by": by}


# --- the story ----------------------------------------------------------------------------------------------------

def _holders(by, N) -> list[str]:
    """Record holders as "Cam Ward (2008-09)", oldest first, spelled as the site spells them."""
    return [f"{(N or {}).get(pid) or nm} ({season_words(yr)})" for pid, nm, yr in sorted(by, key=lambda b: b[2])]


def story(gid: int, season: int, N: dict, name, W, winner: str, loser: str, pk) -> dict:
    """What the goalies' runs add to the story: `sents` (paragraph two, after the goalies' line), `items` (What it
    means) and `heads` (weighted headlines). `name(pid)` gives the full name first, the last name after."""
    from pipeline.export.grammar import and_list, cap, num, ordinal_word, possessive
    out = {"sents": [], "items": [], "heads": []}
    for x in for_game(gid, season, N):
        pid, team = x["pid"], x["team"]
        if pid not in N:
            continue
        last = N[pid].split(" (#")[0].split(" ", 1)[-1]
        nick = W.nick.get(team, team)
        if x["dec"] == "W" and team == winner and x["win"] >= RUN_TOLD:
            n, unit = x["win"], x["unit"]["win"]
            nth = ordinal_word(n, 12)
            if x["opening"] and n >= OPEN_TOLD:
                lead = (f"{name(pid)} has won all {num(n)} of his starts this season" if x["every_start"] else
                        f"{name(pid)} has won all {num(n)} of his decisions this season")
            else:
                lead = f"{name(pid)} has won {num(n)} straight {unit}"
            rec = {r["kind"]: r for r in x["record"]}
            tail, more = "", []
            o, run = rec.get("opening"), rec.get("run")
            if o:
                book = (f"the longest winning run to open a season by any goalie {o['since']}" if o["since"] == "in franchise history" else
                        f"the longest winning run to open a season by a {nick} goalie {o['since']}")
                held = and_list(_holders(o["by"], N))
                tail = (f", {book}, one more than {held}" if o["how"] == "set" else
                        f", matching {held} for {book}" if o["how"] == "tied" else f", already {book}")
            if run and not (o and run["mark"] == o["mark"] and run["by"] == o["by"]):
                held = and_list(_holders(run["by"], N))
                book = (f"the franchise record for straight wins in one season" if run["since"] == "in franchise history" else
                        f"the longest {nick} winning streak in one season {run['since']}")
                if run["how"] == "tied":
                    more.append(f"He also tied {book}, {'shared by' if len(run['by']) > 1 else 'set by'} {held}.")
                elif run["how"] == "set":
                    more.append(f"That is also {book}, one more than {held}.")
                else:
                    more.append(f"That is already {book}.")
            lg = x.get("league")
            if lg:
                if lg["since"] is None:
                    more.append(f"No NHL goalie has opened a season with more straight wins since at least {season_words(lg['from'])}.")
                else:
                    who = and_list([(N or {}).get(p) or nm for p, nm, _ in lg["by"]])
                    more.append(f"No NHL goalie had opened a season with this many straight wins since {who} won his first "
                                f"{num(lg['mark'])} in {season_words(lg['since'])}.")
            out["sents"].append(f"{lead}{tail}.")
            out["sents"] += more
            # headlines: a club record leads the page; a long run or the league's best start in years is a strong one
            if o and o["how"] == "extended" or run and run["how"] == "extended":  # each win after the record: still big
                out["heads"].append((88, pk([f"{last} is still perfect at {n}-0-0" if x["opening"] else f"{last} makes it {num(n)} straight wins",
                                             f"{last} wins his {nth} straight"], "g-more")))
            elif o and o["how"] == "set":
                out["heads"].append((95, pk([f"{last} wins his first {num(n)}, a {nick} record",
                                             f"{last} sets a {nick} record by winning his first {num(n)}"], "g-rec")))
            elif run and run["how"] == "set":
                out["heads"].append((95, pk([f"{last} sets a {nick} record with his {nth} straight win"], "g-rec")))
            elif o:
                out["heads"].append((90, pk([f"{last} wins his first {num(n)}, tying a {nick} record",
                                             f"{last} ties a {nick} record by winning his first {num(n)}"], "g-tie")))
            elif run:
                out["heads"].append((90, pk([f"{last} ties a {nick} record with his {nth} straight win"], "g-tie")))
            elif lg:
                out["heads"].append((90, pk([f"{last} wins his first {num(n)} starts" if x["every_start"] else f"{last} wins his first {num(n)} decisions",
                                             f"{last} is still perfect at {n}-0-0"], "g-lg")))
            elif n >= HEAD_RUN:
                out["heads"].append((min(88, 76 + n), pk([f"{last} wins his {nth} straight", f"{last} makes it {num(n)} straight wins"] +
                                                        ([f"{last} is still perfect at {n}-0-0"] if x["opening"] else []), "g-run")))
        elif x["dec"] == "W" and team == winner and x["points"] >= POINT_TOLD and x["points"] > x["win"]:
            w_, l_, o_ = x["points_rec"]
            out["sents"].append(f"{name(pid)} has earned a point in {num(x['points'])} straight {x['unit']['points']} ({w_}-{l_}-{o_}).")
        elif x["dec"] != "W" and team == loser and x["ended"] >= RUN_TOLD:
            n = x["ended"]
            out["sents"].append(f"It was {possessive(name(pid))} first loss of the season, after {num(n)} straight wins to open it."
                                if x["ended_opening"] else f"The loss ended {possessive(name(pid))} {num(n)}-game winning streak.")
            if n >= HEAD_RUN:
                out["heads"].append((min(87, 76 + n), pk([f"{cap(W.the(winner))} end {possessive(last)} {num(n)}-game win streak",
                                                         f"{cap(W.the(winner))} snap {possessive(last)} {num(n)}-game win streak"], "g-end")))
        elif x["dec"] != "W" and team == loser and x["winless"] >= WINLESS_TOLD:
            w_, l_, o_ = x["winless_rec"]
            out["items"].append({"tag": "In net", "score": 38, "pid": pid,
                                 "text": f"{N[pid]} is winless in his last {num(x['winless'])} {x['unit']['winless']} ({w_}-{l_}-{o_})."})
    return out


def preview_item(pid: int, team: str, season: int, day: str, N: dict, W) -> dict | None:
    """A Who to watch item for a likely starter on a run of RUN_TOLD+ straight wins going into `day`: "Pyotr Kochetkov
    has won all eight of his starts this season, matching Frederik Andersen (2021-22) for the longest winning run to
    open a season by any goalie in franchise history. One more win would make it a club record." """
    from pipeline.export.grammar import and_list, num
    if pid not in N:
        return None
    upto = _season_log(season).filter(pl.col("date") < day)
    mine = upto.filter(pl.col("goalie_id") == pid)
    if mine.is_empty() or mine["team"][-1] != team:
        return None
    decs, flags = mine["decision"].to_list(), mine["started"].to_list()
    r = _runs(decs)
    n = r["win"]
    if n < RUN_TOLD:
        return None
    opening = n == r["n"]
    every = opening and sum(flags) == len(flags) == goalies_started(season, pid, str(Date.fromisoformat(day) - timedelta(days=1)))
    unit = "starts" if all(flags[len(flags) - n:]) else "decisions"
    text = (f"{N[pid]} has won all {num(n)} of his {'starts' if every else 'decisions'} this season" if opening else
            f"{N[pid]} has won {num(n)} straight {unit}")
    score = 58 + n
    rec = {x["kind"]: x for x in _club_record(pid, team, season, day, None, mine, upto, N)}
    o, run = rec.get("opening"), rec.get("run")
    nick = W.nick.get(team, team)
    for x in (o, run):
        if not x:
            continue
        book = ((f"the longest winning run to open a season by any goalie {x['since']}" if x["since"] == "in franchise history" else
                 f"the longest winning run to open a season by a {nick} goalie {x['since']}") if x["kind"] == "opening" else
                (f"the franchise record for straight wins in one season" if x["since"] == "in franchise history" else
                 f"the longest {nick} winning streak in one season {x['since']}"))
        held = and_list(_holders(x["by"], N))
        if x["how"] == "tied":
            text += f", matching {held} for {book}. One more win would make it his alone"
        else:
            text += f", already {book}"
        score = 80
        break
    return {"tag": "In net", "text": text + ".", "score": score, "pid": pid}
