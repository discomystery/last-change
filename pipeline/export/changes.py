"""Lineup changes for the "Worth knowing" notes: who is out, who is back and who is new, and what it changes.

Built from the availability table (metrics/availability.py: the league's game-day rosters, roster listings and the
NHL.com Status Report), so a player hurt last night shows up before the team has played without him. Each note says
what the league has reported (injured reserve, week to week, suspended...) and never guesses a reason the league has
not given: a player with nothing reported "has not dressed".

How much a player matters comes from the same numbers the win model uses: his isolated 5-on-5 impact over the two
seasons before this one (win_model._rates) times his usual 5-on-5 ice time, ranked within the team, alongside where he
sits in the team's ice time (top six forwards, top four defensemen). Who has taken his place comes from the line
data (metrics/patterns.py): whoever has played most beside his usual linemates since he last played.

Notes, most important first: out (regulars only), back (returned in the latest game, or reported as playing tonight
after missing games) and new (joined from another club or added to the roster in the last two weeks and dressed).
"""
from datetime import date as Date, timedelta
from functools import lru_cache

import polars as pl

from pipeline import tables
from pipeline.config import REGULAR, TABLES
from pipeline.export import wording
from pipeline.metrics import patterns

STATUS_WORDS = {
    "season": "is out for the season",
    "long": "is out long term",
    "ir": "is on injured reserve",
    "week": "is out week to week",
    "day": "is day to day",
    "out": "is out",
    "suspended": "is suspended",
    "personal": "is away for personal reasons",
}
OUT_STATUSES = {"injured", "suspended", "personal", "scratched", "off_roster", "not_listed"}
LONG_GONE = 4  # a listed player with nothing reported who has missed this many games in a row counts as out (Demko, Hyman)
UNREPORTED_MIN_MISSED = 2  # a scratch or roster move with nothing reported needs this many games in a row to count
# regulars worth a note: top twelve forwards, top six defensemen by ice time, as the league-ranked absences count them
# (Sora, 2026-10-30: Staal, Carolina's third-line center, and Nikishin were missing from the Hurricanes' Out line)
TOP = {"F": 12, "D": 6}
NEW_DAYS = 14
GONE_FROM_ROSTER = {"not_listed", "off_roster", "no_club"}  # today's statuses that mean he is no longer with the club
SHARE_MIN_SEC = 1800  # 30 minutes together at 5-on-5 before a line's share of the chances is told
SHARE_GAP = 5  # points: closer than this to the old group's share reads "about what it had"
RELIST_GRACE = 2  # days a listing can trail the lineup: dressing this close before a move still counts as new
MAX_OUT = 4
LONG_OUT = 20  # games missed after which only a star is still listed as out


def clock(seconds: float) -> str:
    """Ice time as minutes:seconds (site_json.clock; kept here so this module stays light to import)."""
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _name(r: dict) -> str:
    return f'{r["first"]} {r["last"]}' if r.get("first") else (r.get("last") or "?")


def _team_ids(season: int) -> dict[str, int]:
    out = {}
    for s in (season - 1, season):
        f = TABLES / str(s) / "games.parquet"
        if f.exists():
            g = pl.read_parquet(f)
            out |= {**dict(zip(g["home"], g["home_id"])), **dict(zip(g["away"], g["away_id"]))}
    return out


def _roles(season: int, before: str | None = None) -> dict[int, dict[int, dict]]:
    """Per team id, per skater: usual 5v5 seconds a game, ice-time rank at his position (F or D), how much he is worth
    (expected goals a game) and his rank on the team by worth. This season's games (before `before`, for a past
    preview) when he has 3+, else last season's. Kept for the run while the tables are unchanged (Out notes and the
    post-game "Filling in" item both ask for the same day). Read only: callers must not change what comes back."""
    files = [TABLES / str(s) / f for s in (season - 2, season - 1, season) for f in ("player_game.parquet", "games.parquet")]
    return _roles_for(season, before, tables.stamp(*files))


@lru_cache(maxsize=16)
def _roles_for(season: int, before: str | None, _stamp: tuple) -> dict[int, dict[int, dict]]:
    from pipeline.metrics import win_model

    have = [s for s in (season - 2, season - 1, season) if (TABLES / str(s) / "player_game.parquet").exists()]
    rate = win_model._rates(season, have)
    frames = []
    for s in (season - 1, season):
        f = TABLES / str(s) / "player_game.parquet"
        if f.exists():
            g = pl.read_parquet(TABLES / str(s) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "date")
            frames.append(pl.read_parquet(f).filter(pl.col("pos") != "G").join(g, on="game_id").with_columns(season=pl.lit(s))
                          .select("season", "date", "team_id", "player_id", "pos", "sec", "sec5", "sec_pp"))
    pg = pl.concat(frames)
    if before:
        pg = pg.filter((pl.col("season") < season) | (pl.col("date") < before))
    now = pg.filter(pl.col("season") == season).group_by("team_id", "player_id").agg(pl.len().alias("gp"), pl.col("sec").mean(), pl.col("sec5").mean(), pl.col("sec_pp").mean(), pl.col("pos").last())
    old = (pg.filter(pl.col("season") == season - 1).sort("date").group_by("team_id", "player_id")
           .agg(pl.len().alias("gp"), pl.col("sec").tail(25).mean(), pl.col("sec5").tail(25).mean(), pl.col("sec_pp").tail(25).mean(), pl.col("pos").last()))
    old = old.filter(pl.col("gp") >= 20)
    both = pl.concat([now.filter(pl.col("gp") >= 3), old.join(now.filter(pl.col("gp") >= 3), on=["team_id", "player_id"], how="anti")])
    out: dict[int, dict[int, dict]] = {}
    for (tid,), t in both.group_by(["team_id"]):
        rows = t.with_columns(grp=pl.when(pl.col("pos") == "D").then(pl.lit("D")).otherwise(pl.lit("F")),
                              worth=pl.col("player_id").replace_strict(rate, default=0.0, return_dtype=pl.Float64) * pl.col("sec5") / 3600)
        # Ranked by 5-on-5 plus power-play time, as lines are numbered (metrics/units._player_weight): all-situations time
        # lifted penalty killers and dropped Taylor Hall, on Carolina's second line, to 10th (Sora, 2026-10-15).
        rows = rows.with_columns(toi_rank=(pl.col("sec5") + pl.col("sec_pp")).rank("ordinal", descending=True).over("grp"),
                                 worth_rank=pl.col("worth").rank("ordinal", descending=True))
        out[tid] = {r["player_id"]: r for r in rows.iter_rows(named=True)}
    return out


def _matters(role: dict | None, team_size: int) -> str | None:
    """Plain words for how much he matters to the team, or None for a depth player."""
    if role is None:
        return None
    if role["worth_rank"] <= 3 and role["worth"] > 0.03:
        return "one of their most valuable skaters"
    if role["grp"] == "F" and role["toi_rank"] <= 6:
        return "a top-six forward"
    if role["grp"] == "D" and role["toi_rank"] <= 4:
        return "a top-four defenseman" if role["toi_rank"] > 2 else "a top-pair defenseman"
    if role["worth_rank"] <= 5 and role["worth"] > 0.03:
        return "one of their more valuable skaters"
    return None


def _injury(text: str | None) -> str:
    words, _ = wording.injury(text)
    return f" ({words})" if words else ""


def _status_text(r: dict, now_games: int) -> str:
    """What the league has said (or that it has said nothing), and how long he has been out."""
    n = r["games_missed"] or 0
    never = r["last_played"] is None and now_games > 0 and n >= now_games
    games = "game" if n == 1 else "games"
    if r["status"] in ("injured", "suspended", "personal") and r["report_status"] in STATUS_WORDS:
        rep = r["report_status"]
        text = STATUS_WORDS[rep]
        if rep == "long" and r["report_timeline"] and not r["report_timeline"].lower().startswith(("week", "day")):
            text = f'is {r["report_timeline"]}'
        text += _injury(r["report_injury"])
        if never:
            return text + " and has not played this season"
        if n == 0:
            return text + ", reported after he played in their last game"
        return text + (" and missed their last game" if n == 1 else f" and has missed the last {n} {games}")
    if r["status"] == "scratched":
        return "has been a scratch all season" if never else f"has been a scratch for the last {n} {games}"
    return "has not played this season" if never else f"has not dressed for the last {n} {games}"


def _usual(tid: int, pid: int, kind: str, units_now, units_old, games_with: list[int], old_games: list[int]):
    """His usual linemates (or partner): this season's when he has played 2+ games, else last season's."""
    usual = patterns._mates(units_now, tid, games_with, pid, kind) if len(games_with) >= 2 else None
    return usual or patterns._mates(units_old, tid, old_games, pid, kind)


def _cover(tid: int, pid: int, kind: str, units_now, usual, games_without: list[int], names: dict, away: set[int],
           games_with: list[int] | None = None) -> str | None:
    """Who has played beside his usual linemates (`_usual`) since he last played."""
    if usual is None or not games_without:
        return None
    partners = usual[0]
    if set(partners) & away:
        return None  # his usual linemates are out too: that line no longer exists as it was
    cover = [c for c in patterns._with(units_now, tid, games_without, partners, kind) if c[0] != pid and c[0] not in away]
    if not cover or cover[0][1] < 240:
        return None
    mates = " and ".join(names.get(x, "?") for x in partners)
    text = f"{names.get(cover[0][0], '?')} has taken his spot beside {mates}."
    return text + _how_its_gone(tid, pid, kind, units_now, partners, cover[0][0], games_without, games_with or [], names)


def _share(units, tid: int, kind: str, members: set[int], game_ids: list[int]) -> tuple[float, float] | None:
    """Seconds together at 5-on-5 and their share of the expected goals, for exactly this group over these games."""
    rows = units.filter((pl.col("team_id") == tid) & (pl.col("kind") == kind) & pl.col("game_id").is_in(game_ids))
    sec = xgf = xga = 0.0
    for r in rows.iter_rows(named=True):
        if {int(x) for x in r["unit"].split("-")} == members:
            sec, xgf, xga = sec + r["sec"], xgf + r["xgf"], xga + r["xga"]
    return (sec, xgf / (xgf + xga)) if xgf + xga > 0 else None


def _how_its_gone(tid: int, pid: int, kind: str, units, partners: tuple[int, ...], fill: int, games_without: list[int],
                  games_with: list[int], names: dict) -> str:
    """How the line has done with the fill-in, and beside how it did with him this season (Sora, 2026-10-31: "Has it
    been working?"). Shares of 5-on-5 chances; told only with SHARE_MIN_SEC together, and the comparison only when
    the old group had that much too. Facts about the chances, never a verdict on the fill-in."""
    now = _share(units, tid, kind, set(partners) | {fill}, games_without)
    if not now or now[0] < SHARE_MIN_SEC:
        return ""
    group = "line" if kind == "F" else "pair"
    text = f" That {group} has had {round(100 * now[1])}% of the chances at 5-on-5 in {clock(now[0])} together"
    before = _share(units, tid, kind, set(partners) | {pid}, games_with) if games_with else None
    if before and before[0] >= SHARE_MIN_SEC:
        last = names.get(pid, "him").split(" ", 1)[-1]
        gap = round(100 * now[1]) - round(100 * before[1])
        text += (f", about what it had with {last} ({round(100 * before[1])}%)" if abs(gap) < SHARE_GAP else
                 f", {'up' if gap > 0 else 'down'} from {round(100 * before[1])}% with {last}")
    return text + "."


def notes(season: int, day: str | None = None) -> dict[str, list[dict]] | None:
    """Per team abbreviation: lineup-change notes as of `day` (default: the table's latest day). None when the
    availability table has not been built, so callers can fall back to the older absence notes."""
    path, moves_path = TABLES / str(season) / "availability.parquet", TABLES / str(season) / "moves.parquet"
    if not path.exists():
        return None
    av = pl.read_parquet(path)
    day = day or av["date"].max()
    av = av.filter(pl.col("date") <= day)
    today_rows = av.filter(pl.col("date") == av["date"].max())
    ids = _team_ids(season)
    past = day if day < Date.today().isoformat() else None
    roles = _roles(season, past)
    from pipeline.metrics import relevancy
    # always worth an Out note, even when this season's games barely show him; as of `day` for a past preview
    stars = relevancy.standouts(season, past)
    g_now, d_now, u_now = patterns._team_frame(season)
    g_old, _, u_old = patterns._team_frame(season - 1)
    names = {}
    for s in (season - 1, season):
        f = TABLES / str(s) / "players.parquet"
        if f.exists():
            for r in pl.read_parquet(f).select("player_id", "first", "last").unique("player_id").iter_rows(named=True):
                names[r["player_id"]] = _name(r)
    names |= wording.number_clashes(season, wording.full)  # "Albert Johansson (#47)" when two teammates match
    moves = pl.read_parquet(moves_path) if moves_path.exists() else None
    last_gp: dict[tuple[int, int], int] = {}  # (team id, player) -> games dressed for that club last season
    f = TABLES / str(season - 1) / "players.parquet"
    if f.exists():
        for r in pl.read_parquet(f, columns=["team_id", "player_id"]).group_by("team_id", "player_id").len().iter_rows(named=True):
            last_gp[(r["team_id"], r["player_id"])] = r["len"]
    since = (Date.fromisoformat(day) - timedelta(days=NEW_DAYS)).isoformat()

    out: dict[str, list[dict]] = {}
    for team, rows in today_rows.group_by("team"):
        team = team[0]
        tid = ids.get(team)
        if tid is None:
            continue
        role = roles.get(tid, {})
        games = g_now.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid)).sort("date")
        game_ids, game_dates = games["game_id"].to_list(), games["date"].to_list()
        old_games = g_old.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid))["game_id"].to_list()[-patterns.LOOKBACK:]
        dressed = d_now.filter(pl.col("team_id") == tid)
        un, uo = u_now.filter(pl.col("team_id") == tid), u_old.filter(pl.col("team_id") == tid)  # the club's units, cut once
        played = {pid: set(g["game_id"]) for (pid,), g in dressed.group_by(["player_id"])}
        found = []

        # Out: regulars not available, most valuable first.
        gone = pl.col("status").is_in(list(OUT_STATUSES)) | ((pl.col("status") == "available") & (pl.col("games_missed") >= LONG_GONE))
        away = set(rows.filter(gone)["player_id"].to_list())
        for r in rows.filter(gone).iter_rows(named=True):
            pid, ro = r["player_id"], role.get(r["player_id"])
            reported = r["status"] in ("injured", "suspended", "personal")
            star = pid in stars
            if not star and (ro is None or ro["toi_rank"] > TOP[ro["grp"]] and ro["worth_rank"] > 5):
                continue
            if not reported and (r["games_missed"] or 0) < UNREPORTED_MIN_MISSED:
                continue
            # off the NHL roster with nothing said is a player in the minors, not one who is out (Unger Sörum, a call-up
            # sent back down, once depth players counted)
            if r["status"] in ("not_listed", "off_roster") and not r["report_status"] and not star:
                continue
            # out a long time or sent down: no longer part of what the team is now (eye test on 2025-26: Engvall after 79
            # games and Rinzel in the AHL were "one of their most valuable skaters"), so only stars are still told
            long_out = (r["games_missed"] or 0) >= LONG_OUT or r["report_status"] == "assigned" or r["status"] == "off_roster"
            if long_out and not star:
                continue
            who = names.get(pid) or _name(r)
            text = f"{who} {_status_text(r, len(game_ids))}."
            matters = "one of their best players" if long_out else _matters(ro, len(role)) or ("one of their best players" if star else None)
            if matters:
                text += f" He is {matters}."
            kind = "D" if (ro["grp"] if ro else ("D" if r["pos"] == "D" else "F")) == "D" else "F"
            with_ids = [g for g in game_ids if g in played.get(pid, set())]
            without = [g for g, d in zip(game_ids, game_dates) if g not in played.get(pid, set()) and (r["last_played"] is None or d > r["last_played"])]
            usual = _usual(tid, pid, kind, un, uo, with_ids, old_games)
            cover = _cover(tid, pid, kind, un, usual, without, names, away, with_ids)
            if cover:
                text += " " + cover
            found.append({"pid": pid, "last_played": r["last_played"], "player": who, "kind": "absence", "change": "out", "now": 100, "before": 0, "games": r["games_missed"] or 0,
                          "grp": kind, "partners": list(usual[0]) if usual else [],
                          "status": r["report_status"] if reported else None, "text": text, "order": ro["worth_rank"] if ro else 0})
        found = sorted(found, key=lambda n: n["order"])[:MAX_OUT]

        # Back: returned in the latest game after missing two or more, or reported as playing after missing games.
        last_game_day = game_dates[-1] if game_dates else None
        for r in rows.iter_rows(named=True):
            pid, ro = r["player_id"], role.get(r["player_id"])
            if ro is None or _matters(ro, len(role)) is None:
                continue
            who = names.get(pid) or _name(r)
            if r["report_status"] == "playing" and (r["games_missed"] or 0) >= 1 and r["status"] != "playing":
                n = r["games_missed"]
                text = f"{who} is expected back after missing {n} {'game' if n == 1 else 'games'}."
            elif last_game_day and r["last_played"] == last_game_day:
                before = av.filter((pl.col("player_id") == pid) & (pl.col("team") == team) & (pl.col("date") < last_game_day) & pl.col("game_id").is_not_null()).sort("date")
                n = before["games_missed"][-1] if before.height else 0
                if not n or n < 2:
                    continue
                text = f"{who} returned in their last game after missing {n} games."
            else:
                continue
            if n >= 3:
                text += " Their recent numbers were built mostly without him."
            found.append({"player": who, "pid": pid, "kind": "back", "change": "back", "now": 100, "before": 0, "games": n, "text": text, "order": ro["worth_rank"]})

        # New: joined in the last two weeks, has dressed, and is still with the club.
        if moves is not None:
            here = set(rows.filter(~pl.col("status").is_in(list(GONE_FROM_ROSTER)))["player_id"].to_list())
            date_of = dict(zip(game_ids, game_dates))
            for m in moves.filter((pl.col("to_team") == team) & (pl.col("date") >= since)).sort("date").unique("player_id", keep="last", maintain_order=True).iter_rows(named=True):
                pid = m["player_id"]
                gp = len(played.get(pid, set()))
                if gp == 0:
                    continue
                # sent back down or moved on since (Sora, 2026-10-31: Unger Sörum and Brind'Amour were still "added to
                # the roster" a week after going back to the minors)
                if pid not in here:
                    continue
                # back on the listing after injured reserve or a stint away, not new: a player the club dressed before
                # the move (a couple of days' grace, as listings lag the lineup) or 20+ times last season (Jarvis and
                # Nikishin read "added to the roster" when they came off injured reserve)
                cut = (Date.fromisoformat(m["date"]) - timedelta(days=RELIST_GRACE)).isoformat()
                if not m["from_team"] and (any(date_of.get(g, "9") < cut for g in played.get(pid, set())) or last_gp.get((tid, pid), 0) >= 20):
                    continue
                who = names.get(pid) or _name(m)
                when = Date.fromisoformat(m["date"]).strftime("%b %-d")
                src = f"from {m['from_team']}" if m["from_team"] else "to the roster"
                text = f"{who} joined {src} on {when}" if m["from_team"] else f"{who} was added to the roster on {when}"
                kind = "D" if (role.get(pid) or {}).get("grp") == "D" else "F"
                mates = patterns._mates(un, tid, sorted(played[pid]), pid, kind)
                text += f" and has played beside {' and '.join(names.get(x, '?') for x in mates[0])}." if mates and mates[0] else "."
                found.append({"player": who, "pid": pid, "kind": "new", "change": "new", "now": 100, "before": 0, "games": gp, "text": text, "order": 50})
        if found:
            out[team] = found
    return out
