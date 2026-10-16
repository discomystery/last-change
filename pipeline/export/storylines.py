"""What makes a game interesting: the storylines Sora named in her notes on the game list (2026-10-14).

Used three ways: as sentences and items in the preview and post-game stories (export/stories.py), and as the parts
of each game's interest score on the home page (export/home.py). Everything comes from our own tables, the NHL's
play-by-play and Wikidata: nothing is typed in by hand, so rivalries come from divisions, arena distances and last
spring's playoff series, never from a list.

  playoff rematch   the two met in last spring's playoffs (round, winner, games, how close)
  brothers          players Wikidata lists as siblings (P3373), both with NHL ids
  natural hat trick three straight goals by one player, nobody else scoring in between
  set up all        one teammate assisted on every goal of a player's hat trick
  reviews           a goal that stood after a coach's challenge, a league review that found no goal
  star fights       a star or franchise player in a fight, and how rarely he fights (since 2022-23)
  neighbors         arenas close enough that fans make the trip (site/src/data/arenas.json)
  basement          two of the league's worst records meeting
  summit            two of the league's best teams meeting ("best against best"): this season's points percentage
                    blended with last season's, and both either made last spring's playoffs or are winning this season
"""
import gzip
import json
import math
from functools import lru_cache
from pathlib import Path

import polars as pl

from pipeline.config import RAW, REGULAR, TABLES

PLAYOFFS = 3
NEIGHBOR_MILES = 150  # Rangers-Devils, Islanders-Rangers, Sabres-Leafs, Kings-Ducks: a drive, not a flight
BASEMENT = 6  # both teams among this many worst points percentages (about the bottom fifth)
BASEMENT_GP = 4  # games each team must have played before a record says much
# Best against best (Sora, 2026-10-16: the words should mean the league's best teams, not two strong habits). A club's
# standing is this season's points percentage pulled toward last season's by SUMMIT_K games, so a fast start alone or
# last spring's run alone isn't enough; both clubs must be among the SUMMIT best by it, and each must have made last
# spring's playoffs or be at SUMMIT_FLOOR or better this season, and no club below the floor after SUMMIT_GP games
# counts (Philadelphia made the second round in 2026 and then fell apart).
SUMMIT, SUMMIT_K, SUMMIT_FLOOR, SUMMIT_GP = 6, 20, 0.600, 8
FIGHT_SEASONS = (2022, 2023, 2024, 2025, 2026)  # seasons with penalty rows on file
RARE_FIGHTS = 2  # fights in the seasons on file before this one, at most, for a fight to be out of character
ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}
ROUNDS = {1: "first round", 2: "second round", 3: "conference final", 4: "Stanley Cup Final"}
ARENAS = Path(__file__).resolve().parents[2] / "site" / "src" / "data" / "arenas.json"


# --- playoff rematches ---------------------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _playoff_games(season: int) -> pl.DataFrame:
    f = TABLES / str(season) / "games.parquet"
    if not f.exists():
        return pl.DataFrame()
    return (pl.read_parquet(f).filter((pl.col("game_type") == PLAYOFFS) & pl.col("state").is_in(["OFF", "FINAL"]))
            .select("game_id", "date", "home", "away", "home_score", "away_score", "last_period").sort("game_id"))


def playoff_series(a: str, b: str, season: int) -> dict | None:
    """The two clubs' series in the playoffs that ended the season before `season`: winner, loser, games, round,
    overtime games and one-goal games. None if they didn't meet."""
    g = _playoff_games(season - 1)
    if g.is_empty():
        return None
    s = g.filter(((pl.col("home") == a) & (pl.col("away") == b)) | ((pl.col("home") == b) & (pl.col("away") == a)))
    if s.is_empty():
        return None
    wins = {a: 0, b: 0}
    for r in s.iter_rows(named=True):
        wins[r["home"] if r["home_score"] > r["away_score"] else r["away"]] += 1
    winner = max(wins, key=wins.get)
    # the round: how many series the winner had finished before this one
    first = s["game_id"].min()
    before = g.filter((pl.col("game_id") < first) & ((pl.col("home") == winner) | (pl.col("away") == winner)))
    opps = {r["away"] if r["home"] == winner else r["home"] for r in before.iter_rows(named=True)}
    return {"winner": winner, "loser": b if winner == a else a, "games": s.height, "won": wins[winner], "lost": wins[a if winner == b else b],
            "round": ROUNDS.get(len(opps) + 1, "playoffs"), "ot": int((s["last_period"] != "REG").sum()),
            "one_goal": int(((s["home_score"] - s["away_score"]).abs() == 1).sum())}


def series_words(sr: dict, W) -> str:
    """'the Canadiens beat the Sabres in seven games in last spring’s second round'."""
    n = {4: "four", 5: "five", 6: "six", 7: "seven"}.get(sr["games"], str(sr["games"]))
    how = "swept" if sr["lost"] == 0 else "beat"
    return f"{W.the(sr['winner'])} {how} {W.the(sr['loser'])} in {n} games in last spring’s {sr['round']}" if how == "beat" \
        else f"{W.the(sr['winner'])} swept {W.the(sr['loser'])} in last spring’s {sr['round']}"


# --- brothers --------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def siblings() -> dict[int, set[int]]:
    """Player id -> the ids of brothers who also have NHL ids (Wikidata P3373, saved weekly by ingest/names.py)."""
    from pipeline.ingest import names
    out: dict[int, set[int]] = {}
    for a, b in names.siblings():
        out.setdefault(int(a), set()).add(int(b))
        out.setdefault(int(b), set()).add(int(a))
    return out


# --- goals -----------------------------------------------------------------------------------------------------------

def natural_hat(goals: list[dict]) -> int | None:
    """A player who scored three goals in a row with nobody (either team) scoring in between."""
    run, who = 0, None
    for g in goals:
        if g["p1"] == who:
            run += 1
        else:
            who, run = g["p1"], 1
        if run == 3 and not g.get("goalie"):
            return who
    return None


def set_up_all(goals: list[dict], pid: int) -> int | None:
    """The teammate who assisted on every one of a player's goals (three or more), if there is one."""
    mine = [g for g in goals if g["p1"] == pid]
    if len(mine) < 3:
        return None
    common = set.intersection(*({g["p2"], g["p3"]} - {None} for g in mine))
    return min(common) if common else None


# --- reviews ---------------------------------------------------------------------------------------------------------

REVIEW_WHY = {"goal-interference": "goalie interference", "off-side": "offside", "offside": "offside", "missed-stoppage": "a missed stoppage"}


def reviews(gid: int, season: int) -> list[dict]:
    """Reviews the goal sheet can't show: a goal that stood after a coach's challenge (the challenger takes a delay of
    game penalty), and a league video review that found no goal (no goal at that second). Goals taken off are told by
    stories._overturned. Each is {kind: stood|no_goal, period, sec, home (the side that benefited), scorer, why}."""
    f = RAW / str(season) / "pbp" / f"{gid}.json.gz"
    if not f.exists():
        return []
    d = json.loads(gzip.open(f).read())
    home_id = (d.get("homeTeam") or {}).get("id")
    plays = d.get("plays", [])
    out = []
    for i, p in enumerate(plays):
        if p.get("typeDescKey") != "stoppage":
            continue
        det = p.get("details") or {}
        why = det.get("secondaryReason") or det.get("reason") or ""
        per = (p.get("periodDescriptor") or {}).get("number")
        when = p.get("timeInPeriod") or "00:00"
        m, s = when.split(":")
        sec = int(m) * 60 + int(s)
        same = [x for x in plays if x.get("timeInPeriod") == when and (x.get("periodDescriptor") or {}).get("number") == per]
        goal = next((x for x in same if x.get("typeDescKey") == "goal"), None)
        if why.startswith("chlg-") and goal and ("-hm-" in why or "-vis-" in why):
            failed = any((x.get("details") or {}).get("descKey", "").endswith("unsuccessful-challenge") for x in same)
            if failed:
                kind = why.split("-", 2)[2]
                gd = goal.get("details") or {}
                out.append({"kind": "stood", "period": per, "sec": sec, "home": gd.get("eventOwnerTeamId") == home_id,
                            "scorer": gd.get("scoringPlayerId"), "why": REVIEW_WHY.get(kind, "a challenge")})
        elif why == "video-review" and not goal and per and per <= 3:
            shot = next((x for x in reversed(plays[max(0, i - 4):i]) if x.get("typeDescKey") in ("shot-on-goal", "missed-shot")
                         and x.get("timeInPeriod") == when), None)
            if shot:  # the chance that was looked at, so whose it was is known; the defending side benefited
                owner = (shot.get("details") or {}).get("eventOwnerTeamId")
                out.append({"kind": "no_goal", "period": per, "sec": sec, "home": owner != home_id, "shooter_home": owner == home_id})
            else:
                out.append({"kind": "no_goal", "period": per, "sec": sec, "home": None, "shooter_home": None})
    return out


# --- fights ----------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _fights() -> pl.DataFrame:
    """Every fighting major on file since 2022-23: game, date, player."""
    from pipeline.export import penalties
    out = []
    for s in FIGHT_SEASONS:
        p = penalties.rows(s)
        g = TABLES / str(s) / "games.parquet"
        if p.is_empty() or not g.exists():
            continue
        dates = pl.read_parquet(g).filter(pl.col("game_type") == REGULAR).select("game_id", "date", "season")
        out.append(p.filter(pl.col("pen_desc") == "fighting").select("game_id", "p1").join(dates, on="game_id"))
    if not out:
        return pl.DataFrame(schema={"game_id": pl.Int64, "p1": pl.Int64, "date": pl.Utf8, "season": pl.Int64})
    return pl.concat(out).drop_nulls("p1")


def fight_record(pid: int, before: str, season: int) -> dict:
    """A player's fights before a date: this season, and in the seasons on file before it."""
    f = _fights().filter((pl.col("p1") == pid) & (pl.col("date") < before[:10]))
    now = f.filter(pl.col("season") == season).height
    earlier = f.filter(pl.col("season") < season)
    return {"season": now, "earlier": earlier.height, "seasons_back": season - min(FIGHT_SEASONS),
            "last": earlier["date"].max() if earlier.height else None}


def stars(season: int, day: str) -> dict[int, str]:
    """Star and franchise skaters as of a day (the hidden relevancy tiers; used only to choose what to tell)."""
    from pipeline.metrics import relevancy
    return relevancy.standouts(season, day)


def star_fights(gid: int, season: int, day: str) -> list[dict]:
    """Stars who fought in a game, with how rarely they fight: {pid, record}."""
    f = _fights().filter(pl.col("game_id") == gid)
    if f.is_empty():
        return []
    st = stars(season, day)
    return [{"pid": pid, "record": fight_record(pid, day, season)} for pid in dict.fromkeys(f["p1"].to_list()) if pid in st]


def rare_fight_words(rec: dict) -> str:
    """How rarely a player fights, from his record before the game: 'his first fight in at least four seasons',
    'his second fight this season, after one in the four seasons before'. Empty when he fights often enough."""
    from pipeline.export.stories import num
    back, nth = num(rec["seasons_back"]), rec["season"] + 1
    if rec["earlier"] > RARE_FIGHTS:
        return ""
    if nth == 1 and rec["earlier"] == 0:
        return f"his first fight in at least {back} seasons"
    before = "none" if rec["earlier"] == 0 else num(rec["earlier"])
    if nth == 1:
        return f"his first fight since {_month_year(rec['last'])}"
    return f"his {ORDINALS.get(nth, str(nth) + 'th')} fight this season, after {before} in the {back} seasons before"


def _month_year(d: str) -> str:
    from datetime import date
    return date.fromisoformat(d).strftime("%B %Y")


# --- geography and standings -------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _arenas() -> dict[str, list[float]]:
    return json.loads(ARENAS.read_text())["teams"] if ARENAS.exists() else {}


def miles(a: str, b: str) -> float | None:
    """Great-circle miles between two clubs' home arenas."""
    A = _arenas()
    if a not in A or b not in A:
        return None
    (la1, lo1), (la2, lo2) = A[a][:2], A[b][:2]
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 3958.8 * math.acos(max(-1.0, min(1.0, c)))


def neighbors(a: str, b: str) -> bool:
    m = miles(a, b)
    return m is not None and m <= NEIGHBOR_MILES


def table_before(G: pl.DataFrame, season: int, before: str) -> dict[str, dict]:
    """Every club's record before a date this season: gp, points, points percentage, worst rank (1 = worst)."""
    g = G.filter((pl.col("season") == season) & (pl.col("date") < before[:10]))
    rec: dict[str, dict] = {}
    for r in g.iter_rows(named=True):
        for t, mine, theirs in ((r["home"], r["home_score"], r["away_score"]), (r["away"], r["away_score"], r["home_score"])):
            x = rec.setdefault(t, {"gp": 0, "pts": 0})
            x["gp"] += 1
            x["pts"] += 2 if mine > theirs else (1 if r["last_period"] != "REG" else 0)
    for x in rec.values():
        x["pct"] = x["pts"] / (2 * x["gp"]) if x["gp"] else 0.5
    order = sorted(rec, key=lambda t: (rec[t]["pct"], rec[t]["pts"]))
    for i, t in enumerate(order):
        rec[t]["worst"] = i + 1
        rec[t]["best"] = len(order) - i
    return rec


def basement(G: pl.DataFrame, a: str, b: str, season: int, before: str) -> bool:
    """Both clubs among the league's few worst records, with enough games played to mean something."""
    rec = table_before(G, season, before)
    return all(t in rec and rec[t]["gp"] >= BASEMENT_GP and rec[t]["worst"] <= BASEMENT for t in (a, b))



def _playoff_teams(season: int) -> set[str]:
    g = _playoff_games(season)
    return set() if g.is_empty() else set(g["home"].to_list()) | set(g["away"].to_list())


def standing(G: pl.DataFrame, season: int, before: str) -> dict[str, dict]:
    """Each club's blended standing before a date: this season's points percentage pulled toward last season's by
    SUMMIT_K games, its rank by it (1 = best), and whether it made last spring's playoffs."""
    now = table_before(G, season, before)
    last = table_before(G, season - 1, "9999-12-31")
    po = _playoff_teams(season - 1)
    out = {}
    for t in set(now) | set(last):
        gp = now.get(t, {}).get("gp", 0)
        prior = last[t]["pct"] if t in last else 0.5
        cur = now[t]["pct"] if gp else prior
        out[t] = {"gp": gp, "pct": cur, "blend": (gp * cur + SUMMIT_K * prior) / (gp + SUMMIT_K), "playoffs": t in po}
    for i, t in enumerate(sorted(out, key=lambda t: -out[t]["blend"])):
        out[t]["rank"] = i + 1
    return out


def summit(G: pl.DataFrame, a: str, b: str, season: int, before: str) -> bool:
    """Best against best: both clubs among the league's best by blended standing, each with last spring's playoffs or
    a strong record this season behind it, and neither below the floor once the season is under way."""
    st = standing(G, season, before)
    for t in (a, b):
        x = st.get(t)
        if x is None or x["rank"] > SUMMIT:
            return False
        if x["gp"] >= SUMMIT_GP and x["pct"] < SUMMIT_FLOOR:
            return False
        if not x["playoffs"] and not (x["gp"] >= SUMMIT_GP and x["pct"] >= SUMMIT_FLOOR):
            return False
    return True
