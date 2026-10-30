"""The short version: a plain-English story at the top of every preview and post-game page.

Sora's reviewer asked for a numbers-light read of each game: before it, what to expect and who to watch; after it,
what happened and what it means. Every sentence comes from a fixed rule over our own tables (no free text), and the
only numbers allowed in the prose are the counts a fan says out loud: scores, records, streaks, goals, games. Ranks
and rates are told in words, and every figure stays in the call cards below.

A story is {kicker, head, paras, list_head, items}, where each item is {tag, text}. A preview's first paragraph ends
with the matchup's lean, which is written from one team's side (the outlook note); `lean` holds it for each team and
the page shows the visitor's own team's version, or the home team's.

Narratives found (preview):
  last meeting  the earlier games between the two this season
  form          winning and losing streaks, the latest result if it was a rout
  old team      a player facing a club he played for, and whether it is the first time since he left
  owns them     a player's point streak or scoring record against tonight's opponent since 2023-24
  hot streak    a point streak this season
  slow start    a regular scorer last season with no goals yet
  missing       a regular who sat out the team's latest game or games ("has missed", never "injured")
  still out     a regular from last season who hasn't played this season (the lines' absence note)
  milestone     a skater who dressed last game and is close to a round number: his 500th, 600th... NHL game next,
                or within two of 100, 200... goals, 200+ assists or 300+ points (complete NHL careers only)
  bad blood     the last meeting this season had a line brawl, a big scrum, two or more fights, an ejection or a fight
  storylines    Sora's notes on what makes a game interesting (2026-10-14, export/storylines.py): last spring's playoff
                series, brothers on opposite sides, a club without both usual goalies, two of the worst records, a
                star who fought last time. Post-game: natural hat tricks, a teammate (or brother) on every goal,
                challenges that failed, league reviews with no goal, how rarely a star fights.
History starts in 2023-24, so records against a team say "since 2023-24".

Everything a preview story reads is from before the game's date, so a rebuilt preview tells it as a live one would.
"""
import gzip
import math
import json
import re
import zlib
from datetime import date as Date
from functools import lru_cache

import polars as pl

from pipeline.export.grammar import and_list as _and, cap as _cap, num
from pipeline.config import CURRENT_SEASON, FULL_SEASONS, RAW, REGULAR, SITE_DATA, TABLES
from pipeline.export import absences, grammar, penalties, storylines, wording
from pipeline.export import surprises as surprise_rules

SEASONS = [*FULL_SEASONS, CURRENT_SEASON]
PERIODS = {1: "first", 2: "second", 3: "third"}
ORDINAL = {4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}
LIKELY = 0.6  # how sure starters.py must be of the visitors' goalie before his record on tonight's ice is told (right about 7 in 10)
MAX_ITEMS, MIN_ITEMS, MAX_PER_TAG = 4, 2, 2
HEAD_FACT = 50  # a preview fact headline needs this score to beat the top call's headline


def a_num(n: int) -> str:
    """"an eight" / "an eleven" / "a six": the article a spelled number takes."""
    w = num(n)
    return f"an {w}" if w[0] in "aeiou" and w not in ("one",) else f"a {w}"


GOAL_RUN = 3  # straight games with a goal that make a streak worth telling (intros use the same bar)


def run_of(xs) -> int:
    """Games in a row, most recent last, with more than zero of something."""
    n = 0
    for x in reversed(list(xs)):
        if x <= 0:
            break
        n += 1
    return n


INLINE_GOAL_RUN = 4  # straight games with a goal told on tonight's goal
LOGS_START = "2023-11-01"  # our game logs begin with 2023-24; a run reaching back to there may be longer in the NHL's books (Josi: 11, ours 9)


def streak_text(gn: int, pn: int, games: int, vs: str = "", atleast: bool = False) -> str:
    """The verb phrase for a scoring run, goals first (Sora, 2026-10-14: a goal streak is the more impressive one, so it
    leads whenever there is one). `gn` straight games with a goal, `pn` with a point, `games` played in all; `vs` is
    " against Dallas" or empty. "has" / "have" is left to the caller."""
    if gn >= GOAL_RUN and gn >= pn:
        return "scored in every game this season" if gn == games and not vs else f"scored in {'at least ' if atleast else ''}{num(gn)} straight games{vs}"
    run = "every game this season" if pn == games and not vs else f"{'at least ' if atleast else ''}{num(pn)} straight games{vs}"
    if gn >= GOAL_RUN:
        return f"a point in {run}, with a goal in each of the last {num(gn)}"
    return f"a point in {run}"


def _pick(pool: list[str], *key) -> str:
    from pipeline.export import variety
    return variety.pick(pool, *key)  # by a CRC of the key, staying away from the teams' recent previews


def _secs(toi: str | None) -> int:
    """Seconds in a "mm:ss" clock string (0 when missing)."""
    if not toi:
        return 0
    m, _, sec = toi.partition(":")
    return int(m) * 60 + int(sec or 0)


def weekday(d: str) -> str:
    return Date.fromisoformat(d[:10]).strftime("%A")


def _history_line(c: dict) -> str:
    """A goalie-history call with the record itself (Sora, 2026-10-30: "What is that record?"): "In goal, Wolf has a .850
    save percentage in five starts against the Kraken, well under his usual .905. Don’t read much into it: ..."."""
    k, g = c.get("check") or {}, c.get("gist") or ""
    m = re.match(r"Don’t read much into (.+?)’s? record against (.+)\.$", g)
    if not m or k.get("record") is None or k.get("baseline") is None or not k.get("starts"):
        return g
    who, opp = m.group(1), m.group(2)
    fmt = lambda v: f"{v:.3f}".lstrip("0")
    side = "under" if k["record"] < k["baseline"] else "above"
    return (f"In goal, {who} has a {fmt(k['record'])} save percentage in {num(k['starts'])} starts against {opp}, well {side} his usual "
            f"{fmt(k['baseline'])}. Don’t read much into it: a goalie’s record against one team rarely holds up.")


def when_before(d: str, game_day: str | None) -> str:
    """When an earlier game was, counted from the game a preview is about: "last night", "on Tuesday" within the week,
    else "on Oct 21" (Sora, 2026-10-30). The preview page moves these to the reader's own day (Base.astro, `past`)."""
    if not game_day:
        return "last time out"
    gap = (Date.fromisoformat(game_day[:10]) - Date.fromisoformat(d[:10])).days
    if gap == 1:
        return "last night"
    if 2 <= gap <= 6:
        return f"on {weekday(d)}"
    x = Date.fromisoformat(d[:10])
    return f"on {x.strftime('%b')} {x.day}"


# --- history -------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def book() -> tuple[pl.DataFrame, pl.DataFrame, dict[int, str]]:
    """Finished regular-season games since 2023-24, every skater's line in them (team, opponent, points), and names."""
    games, rows, names = [], [], []
    for s in SEASONS:
        d = TABLES / str(s)
        if not (d / "games.parquet").exists():
            continue
        g = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
        games.append(g.select("game_id", "season", "date", "home", "away", "home_id", "away_id", "home_score", "away_score", "last_period"))
        if (d / "player_game.parquet").exists():
            rows.append(pl.read_parquet(d / "player_game.parquet", columns=["game_id", "player_id", "team_id", "pos", "sec", "g", "a1", "a2"]))
        if (d / "players.parquet").exists():
            names.append(pl.read_parquet(d / "players.parquet", columns=["game_id", "player_id", "first", "last"]))
    G = pl.concat(games).sort("date", "game_id")
    P = (pl.concat(rows).join(G.select("game_id", "season", "date", "home", "away", "home_id"), on="game_id")
         .with_columns(team=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("home")).otherwise(pl.col("away")),
                       opp=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("away")).otherwise(pl.col("home")),
                       pts=pl.col("g") + pl.col("a1") + pl.col("a2"))
         .drop("home", "away", "home_id").sort("date", "game_id"))
    N = pl.concat(names).sort("game_id").group_by("player_id").agg(pl.col("first").last(), pl.col("last").last())
    # two teammates with the same name (Vancouver's Elias Petterssons) get their numbers
    return G, P, {r["player_id"]: f"{r['first']} {r['last']}" for r in N.iter_rows(named=True)} | wording.number_clashes(CURRENT_SEASON, wording.full)


def results(G: pl.DataFrame, team: str, season: int) -> list[dict]:
    """A team's finished games this season in order: win/loss, scores, opponent, home or away, how it ended."""
    out = []
    for r in G.filter((pl.col("season") == season) & ((pl.col("home") == team) | (pl.col("away") == team))).iter_rows(named=True):
        home = r["home"] == team
        us, them = (r["home_score"], r["away_score"]) if home else (r["away_score"], r["home_score"])
        out.append({"win": us > them, "us": us, "them": them, "opp": r["away"] if home else r["home"], "home": home,
                    "end": r["last_period"], "date": r["date"], "game_id": r["game_id"]})
    return out


def streak(res: list[dict]) -> tuple[str, int]:
    """("won", n) or ("winless", n) for the run the team is on."""
    if not res:
        return "", 0
    kind, n = res[-1]["win"], 0
    for r in reversed(res):
        if r["win"] != kind:
            break
        n += 1
    return ("won" if kind else "winless"), n


def slump(t: str, res: list[dict], n: int, W) -> str:
    """"The Flyers are on a four-game losing streak", or "winless in four (0-3-1)" when an overtime or shootout loss is
    in it: writers don't call a run with a point in it a losing streak (eye test, 2026-10-13)."""
    run = res[-n:]
    if any(r["end"] != "REG" for r in run):
        return f"{_cap(W.the(t))} are winless in {num(n)} ({record(run)})."
    return f"{_cap(W.the(t))} are on {a_num(n)}-game losing streak."


def defining_run(res: list[dict]) -> bool:
    """A run that is the story of a team's season: no win yet (4+ games), perfect so far (4+), or a streak of 6+."""
    k, n = streak(res)
    return (n >= 4 and n == len(res)) or n >= 6


def ORDINAL_N(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}.get(n) or wording.ordinal(n)


def record(res: list[dict]) -> str:
    w = sum(r["win"] for r in res)
    otl = sum(not r["win"] and r["end"] != "REG" for r in res)
    return f"{w}-{len(res) - w - otl}-{otl}"


def score_text(r: dict, win: bool = False) -> str:
    """ "5-2", or "3-2 in overtime" / "in a shootout", always winner first; with `win`, "3-2 overtime win"."""
    a, b = max(r["us"], r["them"]), min(r["us"], r["them"])
    if win:
        return f"{a}-{b} " + {"OT": "overtime win", "SO": "shootout win"}.get(r["end"], "win")
    tail = {"OT": " in overtime", "SO": " in a shootout"}.get(r["end"], "")
    return f"{a}-{b}{tail}"


class Words:
    """How sentences name teams: "the Hurricanes" (plural verbs), "Carolina" (a place), "Raleigh"-style home cities
    are not known, so places stand in for them."""

    def __init__(self, places: dict[str, str]):
        from pipeline.export import previews
        self.places = places
        self.nick = previews.names(places)

    def the(self, t: str) -> str:
        return f"the {self.nick.get(t, t)}"

    def poss(self, t: str) -> str:
        n = self.the(t)
        return n + ("’" if n.endswith("s") else "’s")

    def place(self, t: str) -> str:
        p = self.places.get(t, t)
        return self.the(t) if p.startswith("NY ") else p  # "NY Rangers" reads badly as a place

    # `place` names the team ("against the Rangers"); `at` names where it plays ("in New York", "on Long Island").
    def at(self, t: str) -> str:
        return wording.at(self.places.get(t, t))

    def nicknames(self, paras: list[str], key) -> list[str]:
        """At most one fan nickname ("the Canes") in the whole story, by `wording.nickname_once`'s rule, in whichever
        paragraph a stable hash of `key` reaches first."""
        full = {self.nick.get(t, t): self.places.get(t, t) for t in self.places}
        start = zlib.crc32(str(key).encode()) % max(len(paras), 1)
        out = list(paras)
        for i in [*range(start, len(paras)), *range(start)]:
            out[i] = wording.nickname_para(paras[i], f"{key}-{i}", full)
            if out[i] != paras[i]:
                break
        return out


# --- preview -------------------------------------------------------------------------------------------------------

def _short(pid: int, P: pl.DataFrame, N: dict[int, str], season: int) -> str:
    """His last name, or his full name when a teammate this season shares it (the Tkachuks, the two Elias Petterssons)."""
    surname = lambda p: N.get(p, "").split(" (#")[0].split(" ", 1)[-1]
    teams = P.filter((pl.col("season") == season) & (pl.col("player_id") == pid))["team"].unique().to_list()
    mates = P.filter((pl.col("season") == season) & pl.col("team").is_in(teams) & (pl.col("player_id") != pid))["player_id"].unique().to_list()
    return N.get(pid, "") if any(surname(m) == surname(pid) for m in mates) else surname(pid)


def _current_team(P: pl.DataFrame, season: int) -> dict[int, str]:
    rows = P.filter(pl.col("season") == season).group_by("player_id").agg(pl.col("team").last())
    return dict(zip(rows["player_id"], rows["team"]))


REPORTED_OUT = ("injured", "suspended", "personal")
LONG_AWAY = 4  # unreported: "hasn't played since Oct 3" from this many missed games


@lru_cache(maxsize=8)
def _availability(season: int, day: str) -> dict[str, dict[int, dict]]:
    """Per team, players the league has reported out (injured, suspended, away for personal reasons) as of `day`: each
    player's latest row on or before it. Empty when the table has not been built."""
    f = TABLES / str(season) / "availability.parquet"
    if not f.exists():
        return {}
    av = pl.read_parquet(f).filter(pl.col("date") <= day)
    if not av.height:
        return {}
    av = av.filter(pl.col("date") == av["date"].max())
    out = {}
    from pipeline.export.changes import LONG_GONE  # missed games in a row with nothing reported: out all the same
    # A player no club lists any more (unsigned, retired: Quick) is gone, not out: never "the Rangers are without him".
    gone = (pl.col("status") != "no_club") & (pl.col("status").is_in(list(REPORTED_OUT)) | ((pl.col("status") != "playing") & (pl.col("games_missed") >= LONG_GONE)))
    for r in av.filter(gone).iter_rows(named=True):
        out.setdefault(r["team"], {})[r["player_id"]] = r
    return out


@lru_cache(maxsize=8)
def _elsewhere(season: int, day: str, teams: tuple[str, str]) -> set[int]:
    """Players who as of `day` are with neither club, or with no NHL club at all: a preview never names them as if
    they were part of tonight's game (a check on every item, whatever detector wrote it)."""
    f = TABLES / str(season) / "availability.parquet"
    if not f.exists():
        return set()
    av = pl.read_parquet(f, columns=["date", "team", "player_id", "status"]).filter(pl.col("date") <= day)
    if not av.height:
        return set()
    av = av.filter(pl.col("date") == av["date"].max())
    here = set(av.filter(pl.col("team").is_in(list(teams)) & (pl.col("status") != "no_club"))["player_id"].to_list())
    return set(av["player_id"].to_list()) - here


NEWLY_OUT = 3  # games missed up to which a player who has played this season is "newly" out


def _merge_out(outs: list[dict], team: str, n_games: int) -> dict:
    """One "Out" item per club, names only, recent absences first (Sora, 2026-10-15: "The Hurricanes are newly without
    Taylor Hall and Jordan Staal, and have had to make do without Seth Jarvis and Alexander Nikishin all season."). Two
    items a game was the cap for the tag, so a club's third absence never showed; the league's reasons stay on the team
    page."""
    outs = sorted(outs, key=lambda o: -o["score"])
    if not n_games:
        clauses = [f"are without {_and([o['who'] for o in outs])}"]
    else:
        never = [o for o in outs if o["missed"] >= n_games]
        new = [o for o in outs if o not in never and o["missed"] <= NEWLY_OUT]
        mid = [o for o in outs if o not in never and o not in new]
        clauses = []
        if new:
            clauses.append(f"are newly without {_and([o['who'] for o in new])}")
        if mid:
            since = (f" since {Date.fromisoformat(mid[0]['last_played']).strftime('%b %-d')}"
                     if len(mid) == 1 and mid[0]["last_played"] else " for several games")
            clauses.append(f"have been without {_and([o['who'] for o in mid])}{since}")
        if never:
            clauses.append(f"{'have had to make do' if clauses else 'have been'} without {_and([o['who'] for o in never])} all season")
    text = team + " " + (clauses[0] if len(clauses) == 1 else ", ".join(clauses[:-1]) + ", and " + clauses[-1]) + "."
    start = next((o["start"] for o in outs if o.get("start")), None)
    if start:
        text += f" {start} is expected to start."
    return {"tag": "Out", "text": text, "score": outs[0]["score"], "pid": outs[0]["pid"], "pids": [o["pid"] for o in outs]}


def _season_start(season: int) -> str:
    return f"{season}-09-01"  # 2026 is 2026-27


@lru_cache(maxsize=16)
def _roles_on(season: int, day: str) -> dict:
    from pipeline.export import changes
    return changes._roles(season, day)


def _count(n: int, word: str) -> str:
    return f"an {word}" if n == 1 and word[0] in "aeiou" else f"a {word}" if n == 1 else f"{num(n)} {word}s"


FILL_SEC = 240  # 5-on-5 seconds beside the missing player's usual linemates to count as taking his spot
FILL_DRAWS = 6  # faceoffs the fill-in took before his faceoffs are told against the missing center's
CENTER_DRAWS = 8  # draws a game that make the missing player the club's faceoff man
DRAW_GAP = 0.10


def _filled_in(gid: int, day: str, team: str, tid: int, season: int, N: dict, name) -> dict | None:
    """Post-game (Sora, 2026-10-15: "Jankowski made up for Staal's absence with a goal, even though he wasn't as impressive
    on the faceoff dot"): a regular who is newly out, whoever played beside his usual linemates tonight, and what that
    player did in the missing player's spot. Only told when he had a point or his faceoffs stood apart from the missing
    center's. Facts about tonight, never a judgment of the absence."""
    from pipeline.metrics import patterns
    # newly out: he has played for the club this season and missed only the last few games
    notes = [n for n in _out_notes(season, day).get(team, []) if n.get("partners") and (n.get("games") or 0) <= NEWLY_OUT + 1
             and n.get("last_played") and n["last_played"] >= _season_start(season)]
    if not notes:
        return None
    roles = _roles_on(season, day).get(tid, {})
    units = pl.read_parquet(TABLES / str(season) / "game_units.parquet").filter(pl.col("game_id") == gid)
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet", columns=["game_id", "player_id", "team_id", "g", "a1", "a2", "fow", "fol"])
    tonight = pg.filter(pl.col("game_id") == gid)
    for n in notes:
        cover = [c for c in patterns._with(units, tid, [gid], tuple(n["partners"]), n["grp"]) if c[0] != n["pid"]]
        if not cover or cover[0][1] < FILL_SEC or not N.get(cover[0][0]) or not N.get(n["pid"]):
            continue
        fid = cover[0][0]
        if (roles.get(fid) or {}).get("toi_rank", 99) <= (roles.get(n["pid"]) or {}).get("toi_rank", 0):
            continue  # a bigger player moved over (Draisaitl, Werenski): not someone stepping up into his spot
        me = tonight.filter(pl.col("player_id") == fid)
        if me.is_empty():
            continue
        f = me.row(0, named=True)
        g, a = int(f["g"]), int(f["a1"] + f["a2"])
        did = ("had a goal and an assist" if g == 1 and a == 1 else
               ("scored twice" if g == 2 else "had a hat trick" if g == 3 else f"scored {num(g)} times" if g > 3 else "scored") + (f" and added {_count(a, 'assist')}" if a else "") if g
               else f"had {num(a)} assists" if a >= 2 else "")  # a lone assist is not the story
        draws = ""
        his = pg.filter((pl.col("player_id") == n["pid"]) & (pl.col("team_id") == tid))
        if his.height and f["fow"] + f["fol"] >= FILL_DRAWS:
            hw, hn = his["fow"].sum(), his["fow"].sum() + his["fol"].sum()
            if hn >= CENTER_DRAWS * his.height:
                w, k = int(f["fow"]), int(f["fow"] + f["fol"])
                if w / k <= hw / hn - DRAW_GAP:
                    draws = f"won only {w} of {k} faceoffs"
                elif w / k >= max(hw / hn + DRAW_GAP, 0.5):
                    draws = f"won {w} of {k} faceoffs"
        if not did and not draws:
            continue
        missing, filler = name(n["pid"]), name(fid)
        text = f"With {missing} out, {filler} took his spot"
        if did and draws.startswith("won only"):
            text += f" and {did}, though he {draws}"
        elif did:
            text += f" and {did}" + (f" and {draws}" if draws else "")
        else:
            text += f" and {draws}" if not draws.startswith("won only") else f", though he {draws}"
        return {"tag": "Filling in", "text": text + ".", "score": 52 if g else 46}
    return None


@lru_cache(maxsize=8)
def _out_notes(season: int, day: str) -> dict[str, list[dict]]:
    """The team page's "Out" notes (export/changes.py) as of `day`: regulars and stars, most important first."""
    from pipeline.export import changes
    try:
        notes = changes.notes(season, day) or {}
    except Exception as e:  # tables missing in a test or a fresh season: no Out items
        print(f"lineup notes unavailable: {e!r}")
        return {}
    return {t: [n for n in ns if n.get("change") == "out" and n.get("pid")] for t, ns in notes.items()}


def _starters_out(season: int, day: str, tid: int, out: dict[int, dict]) -> list[dict]:
    """Goalies who are out (reported, or missing a run of games) and started at least a fifth of the club's games (and
    three or more) this season or last, with that share. A goalie new to the club counts when he was that for his
    club last season (Korpisalo, the Rangers' backup after 28 starts for Boston; Sora, 2026-10-14), marked `new`."""
    from pipeline.metrics import goalies
    keep = []
    for pid, r in out.items():
        if r["pos"] != "G":
            continue
        # Off the NHL roster with nothing reported means in the minors, not "out" (Sora, 2026-10-16: Tolopilo was named
        # as one of the Canucks' two usual goalies because of last season's starts). IR and non-roster injury lists have
        # a report and stay.
        if r.get("status") in ("not_listed", "off_roster") and not r.get("report_status"):
            continue
        for s in (season, season - 1):
            if not (TABLES / str(s) / "events.parquet").exists():
                continue
            started = goalies.per_game(s).filter(pl.col("started") & (pl.col("date") < day))
            starts = started.filter(pl.col("team_id") == tid)
            mine = starts.filter(pl.col("goalie_id") == pid).height
            if starts.height and mine >= 3 and mine / starts.height >= 0.2:  # a regular backup is news too (Demko: 24%)
                keep.append({**r, "share": mine / starts.height})
                break
            if s == season - 1 and not mine:  # last season with another club: his share of that club's starts
                his = started.filter(pl.col("goalie_id") == pid).group_by("team_id").len()
                if his.height:
                    club, n = his.sort("len").row(-1)
                    total = started.filter(pl.col("team_id") == club).height
                    if n >= 3 and n / total >= 0.2:
                        keep.append({**r, "share": n / total, "new": True})
    return keep


RACE_LEFT = 12  # games left when a club near the playoff line gets a race note
RACE_GAP = 6  # points from the line
RACE_MOVES = 25  # games left when a move into or out of a playoff spot is news (in October it changes nightly)
CLINCH = {"x": "clinched a playoff spot", "y": "clinched the {division} Division", "z": "clinched first place in the {conference} Conference",
          "p": "clinched the Presidents’ Trophy", "e": "were eliminated from playoff contention"}
SPOT = {("wc", 1): "the first wild card", ("wc", 2): "the second wild card"}


def _points(n: int) -> str:
    return "a point" if n == 1 else f"{num(n)} points"


def _race_now(r: dict, W: Words) -> str | None:
    """Where a club stands in a tight race late in the season ("The Capitals are three points behind Utah for the last
    wild card, with five games to play"); None when it isn't close or is settled."""
    if not r or r["left"] > RACE_LEFT or r["left"] <= 0 or r["clinch"] or r["gap"] is None or abs(r["gap"]) > RACE_GAP or not r["rival"]:
        return None
    if r["spot"][0] == "div" and r["spot"][1] < 3:
        return None  # a division's top two are told by the clinch, not the line
    the, rival, left = _cap(W.the(r["team"])), W.place(r["rival"]), f"with {num(r['left'])} {'game' if r['left'] == 1 else 'games'} to play"
    if r["spot"][0] == "out":
        if r["gap"] == 0:
            return f"{the} are level on points with {rival} for the last wild card, {left}."
        return f"{the} are {_points(-r['gap'])} behind {rival} for the last wild card, {left}."
    where = SPOT.get(r["spot"]) or f"{ORDINAL_N(r['spot'][1])} in the {r['division']}"
    hold = f"hold {where}" if r["spot"][0] == "wc" else f"are {where}"
    if r["gap"] == 0:
        return f"{the} {hold}, level on points with {rival}, the first team out, {left}."
    return f"{the} {hold}, {_points(r['gap'])} ahead of {rival}, the first team out, {left}."


def _race_after(gid: int, day: str, teams: tuple[str, str], W: Words, season: int) -> list[dict]:
    """What the night did to the race, for "What it means": a clinch or elimination, a move into or out of a playoff
    spot, or else where a club in a tight race late on now stands. Waits until every game that day is final."""
    from pipeline.ingest import standings_daily as sd
    after, prev = sd.table(season, day[:10]), sd.before(season, day[:10])
    out = []
    for t in teams:
        now, was = sd.race(after, t), sd.race(prev, t)
        if not now:
            continue
        if now["clinch"] and (not was or was["clinch"] != now["clinch"]):
            what = CLINCH.get(now["clinch"])
            if what:
                out.append({"tag": "Playoff race", "text": f"{_cap(W.the(t))} {what.format(**now)}.", "score": 80, "team": t})
            continue
        if was and now["clinch"] is None and now["left"] <= RACE_MOVES and (was["spot"][0] == "out") != (now["spot"][0] == "out"):
            rival = W.place(now["rival"]) if now["rival"] else None
            if now["spot"][0] != "out":
                where = SPOT.get(now["spot"]) or f"{ORDINAL_N(now['spot'][1])} place in the {now['division']}"
                tail = f", {_points(now['gap'])} ahead of {rival}" if rival and now["gap"] else f", level on points with {rival}" if rival else ""
                out.append({"tag": "Playoff race", "text": f"{_cap(W.the(t))} moved into {where}{tail}.", "score": 72, "team": t})
            else:
                tail = f", {_points(-now['gap'])} behind {rival}" if rival and now["gap"] else f", level on points with {rival}" if rival else ""
                out.append({"tag": "Playoff race", "text": f"{_cap(W.the(t))} dropped out of the playoff spots{tail}.", "score": 72, "team": t})
            continue
        text = _race_now(now, W)
        if text:
            out.append({"tag": "Playoff race", "text": text, "score": 58, "team": t})
    return out


def preview_facts(away: str, home: str, before: str, W: Words, season: int = CURRENT_SEASON, likely: dict | None = None,
                  gid: int | None = None, expected_in: set | None = None) -> dict:
    """Everything the preview story can say, from games before `before` (a date). `expected_in` holds the players
    NHL.com's game-day preview puts in tonight's lineup: none of them is told as out (the self-check of 2026-10-30
    found 30 of 33 players our Out items named who then dressed were in NHL.com's projected lineup)."""
    expected_in = expected_in or set()
    G, P, N = book()
    G, P = G.filter(pl.col("date") < before[:10]), P.filter(pl.col("date") < before[:10])
    cur = _current_team(P, season)
    out = {"meetings": [], "form": {}, "items": [], "heads": [], "out": set()}
    # Who the league has out today (eye test, 2026-10-13: hot-streak and old-team items for players who were hurt, and
    # Shesterkin, Bedard, Barkov missing from the list). Storylines skip them; regulars, stars and starting goalies
    # get an "Out" item instead.
    day = before[:10]
    reported = _availability(season, day)
    notes = _out_notes(season, day)
    tids = {}
    for r in G.filter(pl.col("season") == season).iter_rows(named=True):
        tids[r["home"]], tids[r["away"]] = r["home_id"], r["away_id"]
    # last meetings this season
    for r in results(G, home, season):
        if r["opp"] == away:
            out["meetings"].append(r)
    for t in (away, home):
        res = results(G, t, season)
        out["form"][t] = {"res": res, "streak": streak(res), "record": record(res)}
    items = out["items"]
    for t, o in ((away, home), (home, away)):
        skip = set(reported.get(t, {})) - expected_in
        out["out"] |= skip
        told, team_out = [], []
        for k, n in enumerate(x for x in notes.get(t, []) if x["pid"] not in expected_in):
            out["out"].add(n["pid"])
            team_out.append({"score": 76 - k, "pid": n["pid"], "who": N.get(n["pid"]) or n["player"], "missed": n.get("games") or 0,
                             "last_played": n.get("last_played")})
            told.append(n["pid"])
        n_games = len(results(G, t, season))
        keepers = _starters_out(season, day, tids.get(t, -1), reported.get(t, {}))
        if len(keepers) >= 2:  # both of a club's usual goalies out: a little-used goalie gets the net (Sora's notes)
            from pipeline.export import changes
            out["heads"].append((80, f"Who’s in net for {W.the(t)}?"))
            ids = [r["player_id"] for r in sorted(keepers, key=lambda r: -r["share"])][:3]
            names_ = [N.get(i) or changes._name(next(r for r in keepers if r["player_id"] == i)) for i in ids]
            items[:] = [x for x in items if x.get("pid") not in ids]
            new = any(r.get("new") for r in keepers if r["player_id"] in ids)  # a goalie new to the club isn't "usual" yet
            text = (f"{_cap(W.the(t))} are without their top two goalies, {names_[0]} and {names_[1]}." if len(ids) == 2 and new
                    else f"{_cap(W.the(t))} are without both of their usual goalies, {names_[0]} and {names_[1]}." if len(ids) == 2
                    else f"{_cap(W.the(t))} are without three of their goalies: {_and(names_)}.")
            nxt = next(((p, q) for p, q in ((likely or {}).get(t) or []) if p not in skip), None)
            if nxt and nxt[1] >= LIKELY and N.get(nxt[0]):
                text += f" {N[nxt[0]]} is expected to start."
            items.append({"tag": "In net", "text": text, "score": 86, "pid": ids[0], "pids": ids})
            keepers = []
        for r in keepers:
            if r["player_id"] in told or r["player_id"] in expected_in:
                continue
            from pipeline.export import changes
            nxt = next(((p, q) for p, q in ((likely or {}).get(t) or []) if p not in skip), None)
            team_out.append({"score": 70 + 20 * r["share"], "pid": r["player_id"], "who": N.get(r["player_id"]) or changes._name(r),
                             "missed": r["games_missed"] or 0, "last_played": r["last_played"],
                             "start": N[nxt[0]] if nxt and nxt[1] >= LIKELY and N.get(nxt[0]) else None})
        if team_out:
            item = _merge_out(team_out, _cap(W.the(t)), n_games)
            league = absences.sentence(absences.table(season, day).get(t), W.the)  # who's out right now, against the league (Sora, 2026-10-20)
            if league:
                item["text"] += " " + league
            items.append(item)
        fill = absences.filling_in(season, day, t)
        if fill:
            text = absences.filling_text(fill, W.the(t), N)
            if text:
                items.append({"tag": "Filling in", "text": text, "score": 72, "pid": fill["pid"]})
        roster = [p for p, team in cur.items() if team == t and p not in skip]
        mine = P.filter(pl.col("player_id").is_in(roster))
        this = mine.filter((pl.col("season") == season) & (pl.col("team") == t))
        # hot streaks; teammates on the same run share one item (Sora, 2026-10-12: "Granlund and LaCombe have ...")
        hot = {}
        for pid, g in this.sort("player_id").group_by("player_id", maintain_order=True):
            g = g.sort("date")
            pts = g["pts"].to_list()
            n, gn = run_of(pts), run_of(g["g"].to_list())
            if n >= 4 or gn >= GOAL_RUN:
                hot.setdefault(streak_text(gn, n, len(pts)), []).append((60 + n + gn / 2 + sum(pts) / 100, pid[0]))
        for run, who in hot.items():
            who.sort(key=lambda x: -x[0])
            names = [N[p] for _, p in who]
            text = f"{_and(names)} {'has' if len(names) == 1 else 'have'} {run}."
            items.append({"tag": "Hot streak", "text": text, "score": who[0][0], "pid": who[0][1], "pids": [p for _, p in who]})
        # slow start: scored often last season, nothing yet
        last = mine.filter(pl.col("season") == season - 1).group_by("player_id").agg(gp=pl.len(), g=pl.col("g").sum())
        last = last.filter((pl.col("gp") >= 40) & (pl.col("g") / pl.col("gp") >= 0.35))
        now = this.group_by("player_id").agg(gp=pl.len(), g=pl.col("g").sum())
        cold = last.join(now, on="player_id", suffix="_now").filter((pl.col("gp_now") >= 4) & (pl.col("g_now") == 0))
        if cold.height:
            pid = cold.sort(pl.col("g") / pl.col("gp"), -pl.col("player_id"), descending=True)["player_id"][0]
            items.append({"tag": "Slow start", "text": f"{N[pid]} is still looking for his first goal of the season.", "score": 50, "pid": pid})
        # records against tonight's opponent since 2023-24
        vs = mine.filter(pl.col("opp") == o).sort("date")
        cands = []
        for pid, g in vs.sort("player_id", "date").group_by("player_id", maintain_order=True):  # stable ties between runs
            pid = pid[0]
            if pid in skip:
                continue
            pts, goals, gp = g["pts"].to_list(), int(g["g"].sum()), g.height
            n, gn = run_of(pts), run_of(g["g"].to_list())
            elsewhere = g.filter(pl.col("team") != t)
            mostly = elsewhere["team"].mode()[0] if elsewhere.height * 2 > gp else None
            tail = f", most of them with {W.place(mostly)}" if mostly else ""
            if n >= 5 or gn >= GOAL_RUN:
                extra = f", and {int(sum(pts))} points in his last {num(gp) if gp <= 12 else gp} against them" if sum(pts) >= 1.5 * gp and gn < n else ""
                cands.append({"tag": "Owns them", "score": 70 + n + gn / 2, "pid": pid, "streak": n,
                              "text": f"{N[pid]} has {streak_text(gn, n, gp, f' against {W.place(o)}', atleast=(n == gp or gn == gp) and g['date'][0] <= LOGS_START)}{extra}."})
            elif gp >= 6 and goals / gp >= 0.6:
                cands.append({"tag": "Haunts them", "score": 68 + goals / gp * 10, "pid": pid,
                              "text": f"{N[pid]} has {num(goals)} goals in {num(gp) if gp <= 12 else gp} games against {W.place(o)} since 2023-24{tail}."})
        cands.sort(key=lambda c: -c["score"])
        items += cands[:1]
        if cands and cands[0].get("streak", 0) >= 7:
            out["heads"].append((80 + 1.5 * cands[0]["streak"], f"{_short(cands[0]['pid'], P, N, season)}’s favorite opponent"))
        # old teams: the club he played for before this one
        olds = []
        for pid, g in mine.sort("date").group_by("player_id", maintain_order=True):
            pid = pid[0]
            teams = g["team"].to_list()
            if teams[-1] != t:
                continue
            prev = next((x for x in reversed(teams) if x != t), None)
            if prev != o:
                continue
            for_them = g.filter(pl.col("team") == o)
            left = for_them["date"].max()
            met = g.filter((pl.col("date") > left) & (pl.col("opp") == o)).height
            olds.append((for_them.height, pid, met == 0))
        olds.sort(reverse=True)
        if olds:
            n_old, pid, first = olds[0]
            lastname = _short(pid, P, N, season)
            if first and n_old >= 40:
                text = f"{N[pid]} faces {W.place(o)} for the first time since leaving."
                where = W.place(o)
                out["heads"].append((85 + min(n_old, 300) / 30 + 2 * (home == o), f"{lastname} returns to {where}" if home == o else f"{lastname} meets his old team"))
            else:
                text = f"{N[pid]} faces his old team, {W.place(o)}."
            items.append({"tag": "Old team", "text": text, "score": (80 if first else 45) + min(n_old, 300) / 30, "pid": pid})
            others = [N[p] for _, p, _ in olds[1:4]]
            if others:
                items.append({"tag": "Old team", "text": f"{_and(others)} also {'played' if len(others) > 1 else 'played'} for {W.place(o)} before.",
                              "score": 30, "pid": None})
        # missing: a regular this season who sat out the latest game(s)
        res = out["form"][t]["res"]
        # only games the player table has (a game that failed to build would otherwise make everyone "missing":
        # COL at DAL 2026-10-13 listed ten Stars and ten Avalanche, Sora 2026-10-14)
        built = set(P.filter((pl.col("season") == season) & (pl.col("team") == t))["game_id"].to_list())
        team_games = [r["game_id"] for r in res if r["game_id"] in built]
        if len(team_games) >= 3:
            per = this.group_by("player_id").agg(gp=pl.len(), toi=pl.col("sec").mean(), pos=pl.col("pos").first(),
                                                 last=pl.col("game_id").max())
            gone = {}  # players who missed the same stretch share one item (Sora, 2026-10-12)
            for pos, top in (("F", 6), ("D", 4)):
                grp = per.filter(pl.col("pos").is_in(["C", "L", "R"]) if pos == "F" else pl.col("pos") == "D").sort("toi", "player_id", descending=[True, False]).head(top)
                for r in grp.iter_rows(named=True):
                    if r["player_id"] in skip:
                        continue  # told as "Out"
                    missed = len(team_games) - 1 - team_games.index(r["last"])
                    if missed >= 1 and r["gp"] >= 2:
                        gone.setdefault(missed, []).append((72 + (top - grp["player_id"].to_list().index(r["player_id"])), r["player_id"]))
            for missed, who in gone.items():
                if len(who) > MISSING_MAX:
                    continue  # that many regulars out at once is a data gap, not a story
                who.sort(key=lambda x: -x[0])
                when = "latest game" if missed == 1 else f"last {num(missed)} games"
                names = [N[p] for _, p in who]
                items.append({"tag": "Missing", "score": who[0][0], "pid": who[0][1], "pids": [p for _, p in who],
                              "text": f"{_and(names)} {'has' if len(names) == 1 else 'have'} missed {W.poss(t)} {when}."})
        if res:
            items += [x for x in _milestones(t, res[-1], P, N, before) if x.get("pid") not in skip]
            items += [x for x in _club_chases(t, res[-1], P, N, W, before, season) if x.get("pid") not in skip]
    item = _goalie_on_the_road(away, home, before, W, N, (likely or {}).get(away))
    # a likely starter on a winning run (Sora, 2026-10-30: Kochetkov's nine straight to open the season)
    from pipeline.export import goalie_streaks
    for t in (away, home):
        lk = (likely or {}).get(t) or []
        if not lk or lk[0][1] < LIKELY or lk[0][0] in out["out"]:
            continue
        run_ = goalie_streaks.preview_item(lk[0][0], t, season, day, N, W)
        if run_ and item and item.get("pid") == run_["pid"]:  # one item for one goalie: the run first, then the rink
            item = {**item, "text": f"{run_['text']} {item['text']}", "score": max(item["score"], run_["score"])}
        elif run_:
            items.append(run_)
    if item:
        items.append(item)
    # Sora's notes on what makes a game worth watching (2026-10-14): last spring's series, brothers, the bottom of the
    # table, and a club down to a little-used goalie.
    if not out["meetings"]:
        sr = storylines.playoff_series(away, home, season)
        if sr:
            close = sr["games"] >= 6 or sr["ot"] >= 2
            tail = f", {num(sr['ot'])} of them in overtime" if sr["ot"] >= 2 else ""
            items.append({"tag": "Rematch", "text": f"First meeting since {storylines.series_words(sr, W)}{tail}.", "score": 79 if close else 72})
            out["heads"].append((78 if close else 68, f"A rematch of last spring’s {sr['round']}"))
    last_dressed = P.filter((pl.col("season") == season) & pl.col("team").is_in([away, home]))
    if not last_dressed.is_empty():
        lastg = last_dressed.group_by("team").agg(pl.col("game_id").max())
        dressed = last_dressed.join(lastg, on=["team", "game_id"])
        dressed = dressed.filter(~pl.col("player_id").is_in(list(out["out"])))
        for (p1, _), (p2, _) in _brothers(dressed, away, home):
            if N.get(p1) and N.get(p2):
                same = N[p1].split(" ", 1)[-1] == N[p2].split(" ", 1)[-1]
                items.append({"tag": "Brothers", "text": f"Brothers {N[p1].split(' ', 1)[0]} and {N[p2]} on opposite sides." if same
                              else f"{N[p1]} and his brother {N[p2]} on opposite sides.", "score": 80, "pids": [p1, p2], "pid": p1})
                last1 = N[p1].split(" ", 1)[-1]
                out["heads"].append((82, f"{last1} against {last1}" if same else "Brother against brother"))
                break
    if storylines.basement(G, away, home, season, before):
        out["basement"] = "Both teams are near the bottom of the standings."
        out["heads"].append((66, "Somebody has to win"))
    tier = storylines.summit(G, away, home, season, before)  # Sora, 2026-10-16: the words belong to the standings
    if tier == "best":
        out["summit"] = "Two of the league’s best teams meet."
        out["heads"].append((77, _pick(["Best vs. best", "A heavyweight meeting", "Two of the best", "Clash of the titans"], gid, "summit")))
    elif tier == "good":
        out["summit"] = "Two good teams meet."
        out["heads"].append((70, _pick(["Two good teams", "A good test for both", "A measuring-stick game"], gid, "summit")))
    if tier:
        out["tier"] = tier
    if gid is not None:
        items += _new_coach(gid, (away, home), before, W, season)
    from pipeline.ingest import standings_daily as sd
    going_in = sd.before(season, before[:10])
    for t in (away, home):
        text = _race_now(sd.race(going_in, t), W)
        if text:
            items.append({"tag": "Playoff race", "text": text, "score": 77})
    from pipeline.export import behind
    for t in (away, home):  # a record well away from the play, and the part of the goal difference behind it
        item = behind.preview_item(t, season, before, W)
        if item:
            items.append(item)
    items += behind.luck_items(away, home, season, before, W, book()[2])
    return out


def _new_coach(gid: int, teams: tuple[str, str], day: str, W: Words, season: int, result: dict | None = None) -> list[dict]:
    """A club's first game this season under a coach who wasn't behind its bench before (eye test on 2025-26,
    2026-10-13: the Kings' first game after a coaching change went untold). Never says why the change happened."""
    from pipeline.ingest import coaches
    out = []
    for t in teams:
        c = coaches.for_game(season, gid, t, day)
        if not c or not c["first"]:
            continue
        took = f", in place of {c['after']}" if c.get("after") else ""
        if result is None:
            text = f"{c['coach']} is behind {W.poss(t)} bench for the first time this season{took}."
        else:
            text = f"{c['coach']} {'won' if result.get(t) else 'lost'} his first game behind {W.poss(t)} bench."
        out.append({"tag": "New coach", "text": text, "score": 78 if result is None else 64})
    return out


MISSING_MAX = 4  # most regulars one "Missing" item names; more than that means the lineup data is incomplete
MILESTONE_NEAR = 2  # goals, assists or points away from a round hundred
MILESTONE_BIG = 82  # score for 300 goals, 500 assists or points, 1,000 games: ahead of a club's Out notes


@lru_cache(maxsize=1)
def _careers() -> dict[int, list[tuple[str, int, int]]]:
    """Every skater with a complete NHL career on file: his regular-season games as (date, goals, points), oldest first."""
    from pipeline.ingest import careers
    out = {}
    for pid, c in careers.load_all().items():
        if (c.get("landing") or {}).get("position") == "G":
            continue
        out[pid] = sorted((x["gameDate"], int(x.get("goals") or 0), int(x.get("points") or 0)) for log in c["logs"].values() for x in log)
    return out


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _goals_of(gid: int, season: int, pid: int) -> list[int]:
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet", columns=["game_id", "type", "p1", "period_type"])
    return ev.filter((pl.col("game_id") == gid) & (pl.col("type") == "goal") & (pl.col("p1") == pid) & (pl.col("period_type") != "SO"))["p1"].to_list()


FINALLY_SEASONS, FINALLY_STARTS = 3, 5  # a goalie's first win "finally" came: this many seasons after his debut, or starts
SET_UP = {"tip-in": "tipping in", "deflected": "deflecting"}  # how a first goal went in, when the shot type says


def _found_firsts(gid: int, season: int, G, P) -> list[dict]:
    from pipeline.export import firsts
    try:
        return firsts.for_game(gid, season, G, P)
    except Exception as e:  # a nice-to-have: never lose the story over it
        print(f"firsts for {gid} unavailable: {e!r}")
        return []


def first_goal_detail(x: dict, goal: dict | None, helper: str | None) -> tuple[str, str]:
    """(before, after) for a first NHL goal: "an undrafted rookie in his eighth NHL game" and "tipping in a feed from
    MacKinnon" (outside coverage of Hughes's first, 2026-10-16, told all three; ours said only "his first NHL goal")."""
    bits = []
    if x.get("undrafted"):
        bits.append("undrafted")
    nth = x.get("nth") or 0
    if not x.get("debut") and nth >= 2:
        bits.append(f"in his {ORDINAL_N(nth)} NHL game")
    before = f", {' and '.join(bits)}," if bits else ""
    after = ""
    if helper:
        how = SET_UP.get((goal or {}).get("shot") or "")
        after = f", {how} a feed from {helper}" if how else f", set up by {helper}"
    return before, after


def goalie_first_text(x: dict, who: str, saves: int | None) -> str | None:
    """A goalie's first win, first shutout, first start, or first win in a long time."""
    k = x["kind"]
    made = f"{saves} saves" if saves else None
    if k == "first_win":
        n, yrs = x.get("start_n"), x.get("seasons") or 0
        fin = "finally " if yrs >= FINALLY_SEASONS or (n or 0) >= FINALLY_STARTS else ""
        if x.get("shutout") and made:
            head = f"{who} {fin}got his first NHL win with a {saves}-save shutout"
        else:
            head = f"{who} {fin}got his first NHL win" + (f", making {made}" if made else "")
        where = (" in relief" if not x.get("started") else
                 " in his first NHL start" if n == 1 else f" in his {ORDINAL_N(n)} start" if n else "")
        tail = f", {num(yrs)} seasons after his NHL debut" if yrs >= 2 else ""
        return f"{head}{where}{tail}."
    if k == "first_shutout":
        return f"{who} made {made} for his first NHL shutout." if made else f"{who} got his first NHL shutout."
    if k == "first_start":
        return f"{who} made his first NHL start{' and got the win' if x.get('won') else ''}."
    if k == "win_since":
        if x.get("since"):
            d = Date.fromisoformat(x["since"])
            return f"It was {who}’s first NHL win since {MONTHS[d.month - 1]} {d.day}, {d.year}."
        y = str(x["season"])
        return f"It was {who}’s first NHL win since the {y[:4]}-{y[6:]} season."
    return None


def _first_lines(gid: int, season: int, G, P, N, W, name, said: set, winner: str, loser: str, most: int = 2,
                 told: set | None = None, found: list[dict] | None = None, kinds: tuple | None = None, skip: set = frozenset(),
                 goals_told: bool = False, saves: dict[int, int] | None = None, in_net: list[str] | None = None,
                 goals: list[dict] | None = None) -> list[str]:
    """Up to `most` sentences on firsts, debuts and returns (export/firsts.py), most important first. A loser's player
    is named with his team. `kinds` picks which kinds to tell (a games-played milestone counts as "milestone"; goal,
    point and assist marks ride on the goal in the chronology instead); `goals_told` leaves off "and scored", since
    the chronology tells the goal. A goalie's first win, shutout or start goes to `in_net` when given (the
    story's goalie paragraph) instead of among the goals."""
    if found is None:
        found = _found_firsts(gid, season, G, P)
    out, returns = [], 0
    debuts = [x for x in found if x["kind"] == "debut" and not x.get("goalie") and x["pid"] in N and x["pid"] not in skip
              and (kinds is None or "debut" in kinds)]
    if len(debuts) >= 2:  # "The Rangers’ Noah Laba and the Penguins’ Ben Kindel both played their first NHL game."
        who_ = [f"{W.poss(x['team'])} {name(x['pid'])}" for x in debuts[:3]]
        out.append(f"{_cap(_and(who_))} {'both' if len(who_) == 2 else 'all'} played their first NHL game.")
        found = [x for x in found if x not in debuts[:3]]
    for x in found:
        if len(out) >= most:
            break
        pid = x["pid"]
        if pid not in N or pid in skip:
            continue
        if kinds is not None and (x["kind"] not in kinds or (x["kind"] == "milestone" and x["what"] != "game")):
            continue
        known = pid in said
        who = plain = name(pid)
        if not known and x["team"] == loser:
            who = f"{W.poss(loser)} {who}"
        k = x["kind"]
        if told is not None and (x.get("goals") or 0) and k in ("first_goal", "club_goal", "club_debut", "return"):
            told.add(pid)  # "What it means" doesn't then say "first goal of the season" again
        if k == "first_goal":
            debut = ", in his NHL debut" if x.get("debut") else ""
            two = "" if x["goals"] == 1 else f", then added {'another' if x['goals'] == 2 else num(x['goals'] - 1) + ' more'}"
            g1 = next((g for g in goals or [] if g["p1"] == pid), None)
            helper = name(g1["p2"]) if g1 and g1.get("p2") and N.get(g1["p2"]) else None
            pre, post = first_goal_detail(x, g1, helper)
            if two:  # "..., set up by X, then added another" reads as X's
                post = ""
            out.append(f"It was {_short(pid, P, N, season)}’s first NHL goal{debut}{post}." if known and not two
                       else f"{_cap(who)}{pre} scored his first NHL goal{debut}{post}{two}.")
        elif k in ("first_win", "first_shutout", "first_start", "win_since"):
            text = goalie_first_text(x, _cap(who) if k != "win_since" else plain, (saves or {}).get(pid))
            if text:
                (in_net if in_net is not None else out).append(text)
                if in_net is not None:
                    most -= 1
        elif k == "debut":
            if x.get("goalie"):
                won = (" with a shutout" if x.get("shutout") else " and got the win") if x["team"] == winner else ""
                out.append(f"{_cap(who)} made his NHL debut in net{won}.")
            else:
                pts = x.get("points") or 0
                tail = f" and picked up {'a point' if pts == 1 else num(pts) + ' points'}" if pts else ""
                out.append(f"{_cap(who)} played his first NHL game{tail}.")
        elif k == "return":
            if returns:
                continue
            returns += 1
            if x.get("since"):
                d = Date.fromisoformat(x["since"])
                what = "was back in an NHL net for the first time since" if x.get("goalie") else "played his first NHL game since"
                won = ", and got the win" if x.get("goalie") and x.get("won") else ""
                out.append(f"{_cap(who)} {what} {MONTHS[d.month - 1]} {d.day}, {d.year}{won}.")
            else:
                pts, gs = x.get("points") or 0, x.get("goals") or 0
                tail = (("" if goals_told else f" and scored{' twice' if gs == 2 else ' ' + num(gs) + ' times' if gs > 2 else ''}") if gs else
                        f" and picked up {'an assist' if pts == 1 else num(pts) + ' assists'}" if pts else "")
                if x.get("season_debut"):
                    out.append(f"{_cap(who)} played his first game of the season, after missing the first {num(x['missed'])}{',' if tail else ''}{tail}.")
                else:
                    out.append(f"{_cap(who)} was back in the lineup after missing {num(x['missed'])} games{',' if tail else ''}{tail}.")
        elif k == "milestone":
            what = {"game": "NHL game", "goal": "NHL goal", "assist": "NHL assist", "point": "NHL point"}[x["what"]]
            out.append(f"{_cap(who)} played his {x['mark']:,}th {what}." if x["what"] == "game"
                       else f"It was {_short(pid, P, N, season)}’s {x['mark']:,}th {what}." if known
                       else f"{_cap(who)} reached {x['mark']:,} career {x['what']}s.")
        elif k == "first_point":
            if out and out[-1].endswith(" picked up his first NHL point."):  # two in one game read as one sentence
                out[-1] = out[-1].replace(" picked up his first NHL point.", f" and {who} both picked up their first NHL point.")
            else:
                out.append(f"{_cap(who)} picked up his first NHL point.")
        elif k == "club_debut":
            who = plain  # the club is in the sentence
            club = W.nick.get(x["team"], x["team"])
            n = x.get("seasons") or 0
            if x.get("from") and n >= 2:
                was = f", after {num(n)} seasons with {W.the(x['from'])}"
            elif x.get("from"):
                was = f", after starting the season with {W.the(x['from'])}" if x.get("moved") else f", after playing for {W.the(x['from'])}"
            else:
                was = ""
            pts, gs = x.get("points") or 0, x.get("goals") or 0
            tail = (("" if goals_told else f" and scored{' twice' if gs == 2 else ' ' + num(gs) + ' times' if gs > 2 else ''}") if gs else
                    f" and picked up {'an assist' if pts == 1 else num(pts) + ' assists'}" if pts else "")
            out.append(f"{_cap(who)} made his {club} debut{was}{',' if was and tail else ''}{tail}.")
        elif k == "club_goal":
            many = sum(1 for g in _goals_of(gid, season, pid)) >= 2
            n_ = sum(1 for g in _goals_of(gid, season, pid))  # "They were his first goals" read wrong before the second was told
            out.append(f"{'Both' if n_ == 2 else 'All ' + num(n_)} of {_short(pid, P, N, season)}’s goals were his first for {W.place(x['team'])}." if known and many
                       else f"It was {_short(pid, P, N, season)}’s first goal for {W.place(x['team'])}." if known
                       else f"{_cap(who)} scored his first goal for {W.place(x['team'])}.")
    return out


def milestone(games: list[tuple[str, int, int]], before: str) -> tuple[float, str] | None:
    """(score, phrase) for the round number a skater is closest to, from his games before `before`, e.g. "is one goal
    from 400" or "is set to play his 800th NHL game"; None if nothing is close."""
    past = [x for x in games if x[0] < before[:10]]
    gp, g, p = len(past), sum(x[1] for x in past), sum(x[2] for x in past)
    found = []
    if gp + 1 >= 500 and (gp + 1) % 100 == 0:  # a big mark outranks Out notes (Sora's review, 2026-10-17: Aho's 700th point was cut)
        found.append(((MILESTONE_BIG if gp + 1 >= 1000 else 72) + (gp + 1) / 200, f"is set to play his {gp + 1:,}th NHL game"))
    for what, have, low, base, big in (("goal", g, 100, 72, 300), ("assist", p - g, 200, 66, 500), ("point", p, 300, 70, 500)):
        nxt = (have // 100 + 1) * 100
        away = nxt - have
        if nxt >= low and away <= MILESTONE_NEAR:
            unit = what if away == 1 else what + "s"
            found.append(((MILESTONE_BIG if nxt >= big else base) + nxt / 200 - away, f"is {num(away)} {unit} from {nxt:,}"))
    return max(found) if found else None


CHASE_NEAR = 2  # goals, assists or points from passing someone in a franchise's all-time top ten


def _club_chases(t: str, last: dict, P: pl.DataFrame, N: dict[int, str], W, before: str, season: int) -> list[dict]:
    """A skater who dressed in the team's last game and is close to passing someone inside his franchise's all-time top
    ten ("Kyle Connor is one goal from passing Ilya Kovalchuk for third on the Jets' all-time goals list"). Only for the
    team's next game, as with milestones. The same lists as the post-game "Club history" item."""
    from pipeline.ingest import names as names_, records
    if _games_between(t, last["date"], before):
        return []
    fid = records.franchise_of(f"{season}{season + 1}", t)
    lead = records.leaders(fid) if fid and records.seasons_of(fid) >= CLUB_SEASONS else []
    if not lead:
        return []
    logs, day = _club_logs(), before[:10]
    out = []
    for pid in P.filter((pl.col("game_id") == last["game_id"]) & (pl.col("team") == t))["player_id"].to_list():
        mine = _club_totals(pid, fid, day, logs) if N.get(pid) else None
        if not mine:
            continue
        for stat in ("points", "goals", "assists"):
            have = mine[stat][0]
            ahead = []
            for r in lead:
                if r["playerId"] == pid:
                    continue
                own = _club_totals(r["playerId"], fid, day, logs) if r.get("activePlayer") else None
                ahead.append((own[stat][0] if own else r[stat], r))
            above = sorted((x for x in ahead if x[0] >= have), key=lambda x: x[0])
            if not above:
                continue
            total, r = above[0]
            rank = sum(1 for x in ahead if x[0] > total) + 1  # his place once he passes
            away = total - have + 1
            if rank > CLUB_TOP or away > CHASE_NEAR:
                continue
            first, last_ = names_.spell(r["playerId"], r["firstName"], r["lastName"])
            rival = N.get(r["playerId"]) or f"{first} {last_}"
            unit = {"points": "point", "goals": "goal", "assists": "assist"}[stat] + ("" if away == 1 else "s")
            lst = f"{W.poss(t)} all-time scoring list" if stat == "points" else f"{W.poss(t)} all-time {stat} list"
            out.append({"tag": "Club history", "score": 70 - rank, "pid": pid,
                        "text": f"{N[pid]} is {num(away)} {unit} from passing {rival} for {ORDINAL_N(rank)} on {lst}."})
            break
    return sorted(out, key=lambda x: -x["score"])[:2]


def _games_between(t: str, after: str, before: str) -> int:
    """How many of a team's scheduled games fall after one date (local) and start before a time (UTC, as in `start`)."""
    p = SITE_DATA / "schedule.json"
    if not p.exists():
        return 0
    return sum(1 for g in json.loads(p.read_text())["games"]
               if t in (g["home"], g["away"]) and (g.get("date") or g["start"][:10]) > after and g["start"] < before)


def _milestones(t: str, last: dict, P: pl.DataFrame, N: dict[int, str], before: str) -> list[dict]:
    """One item per skater who dressed in the team's last game and is close to a milestone. Only for the team's next
    game: a game further ahead would count from a total another game may change."""
    if _games_between(t, last["date"], before):
        return []
    last_game = last["game_id"]
    out = []
    car = _careers()
    for pid in P.filter((pl.col("game_id") == last_game) & (pl.col("team") == t))["player_id"].to_list():
        got = milestone(car[pid], before) if pid in car and pid in N else None
        if got:
            out.append({"tag": "Milestone", "text": f"{N[pid]} {got[1]}.", "score": got[0], "pid": pid})
    return out


def _goalie_on_the_road(away: str, home: str, before: str, W: Words, N: dict[int, str], likely: list | None) -> dict | None:
    """The visitors' likely starting goalie (pipeline/metrics/starters.py, when it gives him LIKELY or better) and his
    record on tonight's ice, when it makes a story: never won there, a long winless run there, or a run of wins."""
    from pipeline.metrics import venues
    if not likely or likely[0][1] < LIKELY:
        return None
    pid = likely[0][0]
    if not N.get(pid):
        return None
    try:
        where = W.at(home)
        story = venues.storyline(pid, N[pid], home, where, before)
    except Exception as e:  # no saved boxscores or career files: no goalie storyline
        print(f"goalie records unavailable: {e!r}")
        return None
    return {"tag": "In net", "pid": pid, **story} if story else None


def _still_out(notes: list[str], team: str, N: dict[int, str], season: int) -> list[dict]:
    """The lines' absence notes ("S. Jarvis has not played this season. ...") as short items, with full names."""
    G, P, _ = book()
    out = []
    last = P.filter((pl.col("season") == season - 1) & (pl.col("team") == team)).select("player_id").unique()["player_id"].to_list()
    for n in notes:
        if " has not played this season" not in n:
            continue
        short = n.split(" has not played this season")[0]
        first, _, lastname = short.partition(". ")
        name = next((N[p] for p in last if N.get(p, "").endswith(" " + lastname) and N[p].startswith(first)), short)
        out.append({"tag": "Still out", "text": f"{name} hasn’t played yet this season.", "score": 55, "pid": None})
    return out


def _last_out(t: str, f: dict, W: Words, opp: str | None) -> str:
    """"the Ducks (5-6-1) lost 6-5 in overtime to Vancouver", a team's record and last result, for a preview with no
    streak to tell. Bare records ("The Predators are 4-4-5. The Hurricanes have won three straight.") said nothing about
    how either team came in (voice loop, 2026-10-30). The last game against tonight's opponent is left to the meetings
    sentence."""
    res = f["res"]
    r = res[-1]
    if r["opp"] == opp:
        return f"{W.the(t)} ({f['record']})"
    w = when_before(r["date"], getattr(W, "game_day", None))
    if r["win"]:
        return f"{W.the(t)} ({f['record']}) beat {W.place(r['opp'])} {score_text(r)} {w}"
    return f"{W.the(t)} ({f['record']}) lost {score_text(r)} to {W.place(r['opp'])} {w}"


def _form_sentence(t: str, f: dict, W: Words, meeting: bool, opp: str | None = None) -> str | None:
    kind, n = f["streak"]
    res = f["res"]
    if not res:
        return None
    lastg = res[-1]
    # the last game against tonight's opponent is the meetings sentence's (FLA "have won two in a row, the last a 5-1
    # rout of Winnipeg. The Cats won the first meeting this season, 5-1 in Winnipeg."; voice loop)
    rout = lastg["win"] and lastg["us"] - lastg["them"] >= 4 and lastg["opp"] != opp
    tail = f", the last {wording.article(score_text(lastg))} rout of {W.place(lastg['opp'])}" if rout else ""
    if kind == "winless" and n >= 3 and n == len(res):  # the whole season so far (Sora, 2026-10-18: the Flyers' 0-6-3)
        return f"{_cap(W.the(t))} are still looking for their first win of the season, {record(res)} through {num(n)} games."
    if kind == "won" and n >= 3 and n == len(res):
        return f"{_cap(W.the(t))} are perfect so far, {record(res)}."
    if kind == "won" and n >= 5:
        return _pick([f"{_cap(W.the(t))} are on a tear, {num(n)} straight wins{tail}.", f"{_cap(W.the(t))} have won {num(n)} straight{tail}, and they’re rolling."], t, n, "form")
    if kind == "won" and n >= 3:
        return f"{_cap(W.the(t))} have won {num(n)} straight{tail}."
    if kind == "winless" and n >= 3:
        return slump(t, res, n, W)
    if kind == "won" and n >= 2:
        return f"{_cap(W.the(t))} have won two in a row{tail}."
    return None


SCRATCH_RUN = 3  # straight games scratched before a healthy-scratch run is worth a line


def _scratch_runs(season: int, day: str, team: str) -> dict[int, int]:
    """Players the club scratched in each of its last N games before `day` (N >= 1): {player: N}."""
    p = TABLES / str(season) / "game_rosters.parquet"
    if not p.exists():
        return {}
    gr = pl.read_parquet(p, columns=["game_id", "date", "team", "player_id", "status"]).filter((pl.col("team") == team) & (pl.col("date") < day[:10]))
    games = gr.select("game_id", "date").unique().sort("date", "game_id")["game_id"].to_list()[::-1]
    scr = {g: set(gr.filter((pl.col("game_id") == g) & (pl.col("status") == "scratched"))["player_id"].to_list()) for g in games}
    out = {}
    for pid in scr.get(games[0], set()) if games else ():
        n = 0
        for g in games:
            if pid not in scr[g]:
                break
            n += 1
        out[pid] = n
    return out


def _listed_injured(season: int, day: str, team: str) -> set[int]:
    """Players NHL.com's earlier game-day previews this season listed as injured for the club (before `day`)."""
    folder = RAW / "nhl_previews" / str(season)
    out = set()
    for p in folder.glob("*.json.gz") if folder.exists() else ():
        try:
            got = json.loads(gzip.open(p).read())
        except (OSError, ValueError):
            continue
        if (got.get("fetched") or "")[:10] < day[:10]:
            out |= set(((got.get("lineups") or {}).get(team) or {}).get("injured") or [])
    return out


def expected_lineup(nhl: dict | None) -> set[int]:
    """Everyone NHL.com's game-day preview (`previews.nhl_lineup`) puts in either club's lineup tonight: its projected
    lines, pairs, extra defensemen and both goalies."""
    return {p for mine in ((nhl or {}).get("teams") or {}).values() for p in mine.get("ids") or []}


def _game_day(nhl: dict | None, away: str, home: str, day: str, W: "Words", N: dict[int, str], lines: dict, fp: dict | None,
              season: int) -> tuple[list[dict], set[int]]:
    """Who to watch items from NHL.com's game-day preview (`previews.nhl_lineup`): a projected 11 forwards and 7
    defensemen, the same lineup as last game, game-time decisions, line changes its coach made, a suspension, and a
    healthy scratch run. Facts only, in our words, and never a reason for an absence beyond a suspension. Also returns
    players the preview says are back, so an "Out" or "Missing" line about them alone is dropped."""
    if not nhl:
        return [], set()
    items, back = [], set()
    facts = nhl.get("facts") or []
    proj = nhl.get("teams") or {}
    said = lambda kind, t: [f for f in facts if f["kind"] == kind and f["team"] == t]
    for t in (away, home):
        mine = proj.get(t) or {}
        habit = ((fp or {}).get(t, {}).get("lineup") or {})
        eleven = said("eleven", t)
        if mine.get("eleven") or any(f["sure"] for f in eleven):
            tail = ("" if not habit.get("habit") else f", as in all {num(habit['games'])} of their games so far" if habit["eleven"] == habit["games"]
                    else f", as in {num(habit['eleven'])} of their {num(habit['games'])} games so far")
            items.append({"tag": "Lineup", "score": 40 if habit.get("habit") else 66,
                          "text": f"NHL.com projects {W.the(t)} to dress 11 forwards and seven defensemen{tail}."})
        elif eleven:
            items.append({"tag": "Lineup", "score": 58, "text": f"{_cap(W.the(t))} could go with 11 forwards and seven defensemen, NHL.com says."})
        elif said("same", t):
            items.append({"tag": "Lineup", "score": 30, "text": f"NHL.com expects {W.the(t)} to dress the same lineup as last game."})
        for f in said("gtd", t)[:2]:
            pid = f["players"][0]
            if N.get(pid):
                items.append({"tag": "Game-time call", "score": 72, "pid": pid, "text": f"{N[pid]} is a game-time decision, NHL.com says."})
        usual = [set(u.get("ids") or []) for u in (lines.get(t) or {}).get("units", []) if u["label"][0] == "L"]
        for f in said("lines", t):
            for pid in f["players"]:
                line = next((x for x in mine.get("lines", []) if pid in [y["id"] for y in x]), None)
                ids = {y["id"] for y in line} if line else set()
                if not line or len(line) < 3 or ids in usual or not N.get(pid):
                    continue
                mates = [N[y["id"]] for y in line if y["id"] != pid and N.get(y["id"])]
                items.append({"tag": "New lines", "score": 66, "pid": pid, "pids": list(ids),
                              "text": f"{_cap(W.the(t))} have changed their lines: NHL.com projects {N[pid]} with {_and(mates)}."})
                break
        for f in said("suspension", t):
            pid, n = f["players"][0], f.get("games")
            if not N.get(pid):
                continue
            length = f"{num(n)}-game " if n else ""
            if f["state"] == "returns":
                back.add(pid)
                items.append({"tag": "Suspension", "score": 74, "pid": pid, "text": f"{N[pid]} is back from his {length}suspension."})
            elif f["state"] == "serving" and f.get("last"):
                items.append({"tag": "Suspension", "score": 62, "pid": pid, "text": f"{N[pid]} serves the last game of his {length}suspension."})
            else:
                items.append({"tag": "Suspension", "score": 70, "pid": pid, "text": f"{N[pid]} serves a {length}suspension." if n else f"{N[pid]} is suspended."})
        runs = _scratch_runs(season, day, t)
        healthy = {f["players"][0] for f in said("healthy", t)}
        hurt = set(_availability(season, day).get(t, {})) | _listed_injured(season, day, t)  # missed games for a reason: not a scratch run
        dressed = {y["id"] for x in mine.get("lines", []) + mine.get("pairs", []) for y in x} | {y["id"] for y in mine.get("extra_d", [])}
        for pid, n in sorted(runs.items(), key=lambda kv: -kv[1]):
            if n < SCRATCH_RUN or not N.get(pid) or not mine or (pid in hurt and pid not in healthy):
                continue
            if pid in dressed:
                back.add(pid)
                items.append({"tag": "Back in", "score": 56, "pid": pid,
                              "text": f"NHL.com projects {N[pid]} back in the lineup after {num(n)} straight games as a healthy scratch." if pid in healthy
                              else f"NHL.com projects {N[pid]} back in the lineup after he was scratched for {num(n)} straight games."})
            elif pid in healthy:
                items.append({"tag": "Scratched", "score": 44, "pid": pid, "text": f"{N[pid]} has been a healthy scratch for {num(n)} straight games, and NHL.com expects another."})
            break
    return items, back


def _vs_projection(gid: int, r: dict, tonight: pl.DataFrame, W: "Words", N: dict[int, str], season: int) -> list[dict]:
    """Post-game: where tonight's lineup differed from NHL.com's projection before the game (`nhl_previews`): its shape
    (11 forwards and 7 defensemen or 12 and 6), or players it had in who didn't dress. Never why."""
    from pipeline.ingest import nhl_previews
    from pipeline.metrics import units
    start = (r.get("start") or "").replace("Z", "+00:00") or None
    got = nhl_previews.projection(season, gid, start)
    if not got:
        return []
    tid = {r["away"]: None, r["home"]: None}
    for x in tonight.select("team", "team_id").unique().iter_rows(named=True):
        tid[x["team"]] = x["team_id"]
    out = []
    for t in (r["away"], r["home"]):
        lu = (got.get("lineups") or {}).get(t) or {}
        projected = {p for x in lu.get("lines", []) + lu.get("pairs", []) for p in x} | set(lu.get("extra_d") or [])
        if len(projected) < 17 or lu.get("missed"):  # a lineup we could not read in full says nothing about who was left out
            continue
        dressed = set(tonight.filter((pl.col("team") == t) & (pl.col("pos") != "G"))["player_id"].to_list())
        if len(dressed) < 17:
            continue
        pf, pd = nhl_previews.shape(lu)
        af, ad = units.shapes(season).get((gid, tid[t]), (None, None))
        gone = [p for p in projected - dressed if N.get(p)]
        came = [p for p in dressed - projected if N.get(p)]
        if af and (pf, pd) != (af, ad) and {pf, af} == {11, 12}:
            out.append({"tag": "Lineup", "score": 40, "text": f"NHL.com had projected {W.the(t)} with {pf} forwards and {num(pd)} defensemen, but they dressed {af} and {num(ad)}."})
        elif len(gone) == 1 and len(came) == 1:
            out.append({"tag": "Lineup", "score": 38, "pid": gone[0], "text": f"{N[gone[0]]} was in NHL.com’s projected lineup, but {N[came[0]]} played instead."})
        elif 1 <= len(gone) <= 2:
            out.append({"tag": "Lineup", "score": 36, "pid": gone[0], "text": f"{_and([N[p] for p in gone])} {'was' if len(gone) == 1 else 'were'} in NHL.com’s projected lineup but didn’t dress."})
    return out


def preview_story(gid: int, away: str, home: str, start: str, places: dict, claims: list[dict],
                  outlook: dict | None, lines: dict, season: int = CURRENT_SEASON, likely: dict | None = None,
                  nhl: dict | None = None, fp: dict | None = None, projected: dict | None = None) -> dict:
    """The story for one preview, from data before the game and the calls already chosen for it. `start` is the game's
    local date: games are dated locally, so a late start's UTC date would count the game itself."""
    W = Words(places)
    W.game_day = start[:10]
    facts = preview_facts(away, home, start, W, season, likely, gid, expected_lineup(nhl))
    _, _, N = book()
    sents = []
    # the last meeting(s)
    m = facts["meetings"]
    if len(m) == 1:
        r = m[0]
        winner = home if r["win"] else away
        at = W.at(home) if r["home"] else W.at(away)
        if winner == away and r["home"]:
            sents.append(f"{_cap(W.the(away))} already played {at} once this season and left with {wording.article(score_text(r, win=True))} {when_before(r['date'], start)}.")
        else:
            sents.append(f"{_cap(W.the(winner))} won the teams’ first meeting this season, {score_text(r)} {at} {when_before(r['date'], start)}.")
        if r["home"]:  # a plain fact: it leads only when nothing livelier does (Sora's review of 2025-26, 2026-10-17)
            facts["heads"].append((45, f"Round two {W.at(home)}"))
        else:
            facts["heads"].append((40, "Round two"))
    elif len(m) > 1:
        wins = sum(r["win"] for r in m)  # `win` is the home team's
        if wins in (0, len(m)):
            w = home if wins else away
            sents.append(f"{_cap(W.the(w))} have won {'both' if len(m) == 2 else 'all ' + num(len(m))} meetings this season.")
        elif wins * 2 == len(m):
            sents.append(f"The teams have split {num(len(m))} meetings this season.")
        else:  # "split three meetings" for a 2-1 record (eye test on 2025-26, 2026-10-13)
            lead, n = (home, wins) if wins * 2 > len(m) else (away, len(m) - wins)
            sents.append(f"{_cap(W.the(lead))} have won {num(n)} of the {num(len(m))} meetings this season.")
    # form
    ups = {}  # each club's form sentence and whether it is going their way, for joining the two below
    form = {t: _form_sentence(t, facts["form"][t], W, bool(m), away if t == home else home) for t in (home, away)}
    sh, sa = facts["form"][home]["streak"], facts["form"][away]["streak"]
    order = sorted((home, away), key=lambda t: not defining_run(facts["form"][t]["res"]))  # the bigger run first
    if form[home] and form[away] and sh == sa and sh[0] == "won":  # "The X have won three straight. The Y have won three straight."
        sents.append(f"Both teams come in on {a_num(sh[1])}-game winning streak." if sh[1] >= 3 else "Both teams have won two in a row.")
    elif form[home] and form[away] and sh == sa and all(x["end"] == "REG" for t in (home, away) for x in facts["form"][t]["res"][-sh[1]:]):
        sents.append(f"Both teams come in on {a_num(sh[1])}-game losing streak.")
    elif form[home] and form[away]:  # two streaks: separate sentences, since "but" only fits a real contrast (Sora, 2026-10-12)
        sents += [form[t] for t in order]
        ups.update({t: (form[t], facts["form"][t]["streak"][0] == "won") for t in order})
    elif any(form.values()):
        for t in order:
            if form[t]:
                sents.append(form[t])
                ups[t] = (form[t], facts["form"][t]["streak"][0] == "won")
            elif facts["form"][t]["res"]:
                last_ = _last_out(t, facts["form"][t], W, away if t == home else home)
                sents.append(_cap(last_ + "." if not last_.endswith(")") else f"{last_.split(' (')[0]} are {facts['form'][t]['record']}."))
                ups[t] = (sents[-1], facts["form"][t]["res"][-1]["win"])
    else:
        fa, fh = facts["form"][away], facts["form"][home]
        if fa["res"] and fh["res"]:
            a_, h_ = _last_out(away, fa, W, home), _last_out(home, fh, W, away)
            wa, wh = (when_before(f_["res"][-1]["date"], start) for f_ in (fa, fh))
            join = ", and " if fa["res"][-1]["win"] == fh["res"][-1]["win"] else ", while "  # a win beside a loss is a contrast
            if wa == wh and a_.endswith(wa) and h_.endswith(wh):  # one "on Tuesday" a sentence
                sents.append(_cap(f"{a_}{join}{h_[: -len(wh)]}the same night."))
            elif not a_.endswith(")") or not h_.endswith(")"):
                sents.append(_cap(f"{a_}{join}{h_}."))
            else:
                sents.append(f"{_cap(W.the(away))} come in at {fa['record']}, {W.the(home)} at {fh['record']}.")
    if m and any(defining_run(facts["form"][t]["res"]) for t in (home, away)):
        # a season-defining run leads, ahead of the last meeting (Sora, 2026-10-18: "winless in nine" came second)
        sents = sents[1:] + sents[:1]  # the meetings sentence is always the first one
    # the two clubs’ form in one sentence, joined by how it compares (Sora, 2026-10-30): "while" for a hot team beside a cold
    # one, "and" when both are going the same way; a sentence that already has a comma stays on its own
    if len(ups) == 2 and all(x in sents for x, _ in ups.values()) and "," not in "".join(x for x, _ in ups.values()):
        ia, ih = sents.index(ups[away][0]), sents.index(ups[home][0])
        if abs(ia - ih) == 1:
            i = min(ia, ih)
            first, then = sents[i], sents[i + 1]
            t2 = home if i + 1 == ih else away
            same = ups[home][1] == ups[away][1]
            X2 = _cap(W.the(t2))
            if then.startswith(X2 + " have ") and i + 2 < len(sents) and sents[i + 2].startswith(X2 + " "):
                then = f"{W.place(t2)} has " + then[len(X2 + " have "):]  # "... Florida has won two in a row. The Panthers won ..."
            elif then.startswith("The "):
                then = "the " + then[4:]
            sents[i: i + 2] = [first[:-1] + (", and " if same else ", while ") + then]
    for i in range(len(sents) - 1):  # "The Kraken have won both meetings. The Kraken (8-4-1) beat ..." names them by place first
        for t in (home, away):
            X = _cap(W.the(t))
            if sents[i].startswith(X + " have won") and sents[i + 1].startswith(X + " "):
                sents[i] = f"{W.place(t)} has won" + sents[i][len(X + " have won"):]
    for t, f in facts["form"].items():
        k, n = f["streak"]
        if k == "winless" and n >= 4 and n == len(f["res"]):  # no win yet: the story of their season
            facts["heads"].append((76 + 2 * n, _pick([f"{_cap(W.the(t))} are still looking for their first win", f"Still no win for {W.the(t)}",
                                                      f"{_cap(W.the(t))} try again for a first win"], gid, t, "nowin")))
            continue
        if k == "won" and n >= 4 and n == len(f["res"]):
            facts["heads"].append((66 + 2 * n, _pick([f"{_cap(W.the(t))} are still perfect", f"Can anyone beat {W.the(t)}?"], gid, t, "perfect")))
            continue
        if k == "won" and n >= 4:
            facts["heads"].append((60 + n, _pick([f"{_cap(W.the(t))} come in hot", f"{_cap(W.the(t))} are rolling", f"Can anyone stop {W.the(t)}?"]
                                                 if n >= 6 else [f"{_cap(W.the(t))} come in hot", f"{_cap(W.the(t))} are rolling", f"{_cap(W.the(t))} are on a heater"], gid, t, "hot")))
        if k == "winless" and n >= 4:
            facts["heads"].append((58 + n + max(0, n - 5), _pick([f"{_cap(W.the(t))} need a win", f"{_cap(W.the(t))} are desperate for a win", f"Something has to change for {W.the(t)}"]
                                                 if n >= 6 else [f"{_cap(W.the(t))} need a win", f"{_cap(W.the(t))} are searching for answers"], gid, t, "cold")))
    if facts.get("basement"):
        sents.append(facts["basement"])
    if facts.get("summit"):
        sents.append(facts["summit"])
    # what to look for: the gist of the top calls
    look, kinds = [], set()
    tired_told = any("back-to-back" in v.get("body", "") for v in (outlook or {}).values())
    from pipeline.export import preview_more
    style_gists, covered, rest_gist = [], set(), None
    for c in claims:  # one per kind of call: two duels or two edges in a row read the same
        if c["kind"] == "rest":  # who played last night belongs with how they come in
            if not tired_told and c.get("gist"):
                rest_gist = c["gist"]
            continue
        if c["kind"] in preview_more.TRAIT_CALLS:  # a call about the two styles is what they mean against each other
            covered |= {x for x in (c.get("metric"), (c.get("check") or {}).get("metric")) if isinstance(x, str)}
            if c.get("gist") and c["kind"] not in kinds and len(style_gists) < 2:
                traits = {x for x in (c.get("metric"), (c.get("check") or {}).get("metric")) if isinstance(x, str)}
                style_gists.append((preview_more.meet_line(c["kind"], c.get("metric"), c.get("team"), c.get("opp"), W, c.get("gist") or "")
                                    or c["gist"], preview_more.partners(traits)))
                kinds.add(c["kind"])
            continue
        if c.get("gist") and c["kind"] not in kinds and len(look) < 2:
            look.append((c["kind"], _history_line(c) if c["kind"] == "history" else c["gist"]))
            kinds.add(c["kind"])
    if rest_gist:
        sents.append(rest_gist.replace("; ", ", and ", 1) if "last night" not in " ".join(sents) else rest_gist.split("; ", 1)[-1][:1].upper() + rest_gist.split("; ", 1)[-1][1:])
    blood = penalties.bad_blood(m[-1]["game_id"], season, N) if m else None
    if m:  # a star who fought last time is the bigger story (Sora's notes, 2026-10-14)
        sf = [x for x in storylines.star_fights(m[-1]["game_id"], season, m[-1]["date"]) if N.get(x["pid"])]
        if sf and blood:
            words = storylines.rare_fight_words(sf[0]["record"])
            if words:
                full = N[sf[0]["pid"]]
                who = full.split(" ", 1)[-1] if full in blood["text"] else full
                blood = {**blood, "text": f"{blood['text']} For {who}, it was {words}.", "score": max(blood["score"], 78)}
            facts["heads"].append((79, f"{N[sf[0]['pid']].split(' ', 1)[-1]} fought last time"))
        if m[-1]["date"] == _day_before(start):
            facts["heads"].append((75, "Same teams, second night running"))
    still = _still_out([n["text"] for n in lines.get(away, {}).get("notes", [])], away, N, season) \
        + _still_out([n["text"] for n in lines.get(home, {}).get("notes", [])], home, N, season)
    out_last = {N[p].split(" ", 1)[-1] for p in facts["out"] if N.get(p)}
    still = [x for x in still if not any(f" {nm} " in " " + x["text"] for nm in out_last)]  # already an "Out"
    day_items, back = _game_day(nhl, away, home, start, W, N, lines, fp, season)
    items = [i for i in facts["items"] if not (i["tag"] in ("Out", "Missing") and set(i.get("pids") or [i.get("pid")]) <= back)]
    items = sorted(items + day_items + ([blood] if blood else []) + still, key=lambda i: -i["score"])
    gone = _elsewhere(season, start, (away, home)) | {p for p in facts.get("out", ()) if p}
    # nothing about a player the page lists as out, except the Out note itself (Sora's review, 2026-10-17: a
    # milestone chase for a player who was out)
    items = [i for i in items if i["tag"] in ("Out", "Missing") or not gone & set(i.get("pids") or [i.get("pid")])]
    # the fuller preview's extra paragraphs (Sora, 2026-10-30)
    more = preview_more.paragraphs(gid, away, home, start[:10], W, N, fp, projected, gone, " ".join(sents), season,
                                   style_gists, covered)
    if more["standing"]:
        sents.append(more["standing"])
    from pipeline.export import behind
    luck = behind.preview_sentence(away, home, season, start[:10], W)  # records against the chances, both clubs
    if luck:  # its own paragraph, after where they stand (Sora, 2026-10-30)
        items = [i for i in items if i["tag"] != "Behind the record"]
    told_tags = set().union(*more["told"].values()) if more["told"] else set()
    items = [i for i in items if not (i["tag"] in told_tags and i.get("pid") in more["used"])]  # the scorers' paragraph tells it
    picked, per, seen = [], {}, set()
    for i in items:
        if per.get(i["tag"], 0) >= MAX_PER_TAG or (i.get("pid") and i["pid"] in seen) or len(picked) >= MAX_ITEMS:
            continue
        per[i["tag"]] = per.get(i["tag"], 0) + 1
        seen.update(i.get("pids") or [i.get("pid")])
        picked.append({"tag": i["tag"], "text": i["text"]})
    for t in (away, home):  # always at least two: each team's leading scorer
        if len(picked) >= MIN_ITEMS or more["scoring"]:
            break
        lead = _leader(t, start, season)
        if lead and lead[0] not in seen:
            picked.append({"tag": "Leads the way", "text": f"{N[lead[0]]} leads {W.the(t)} with {num(lead[1])} {'point' if lead[1] == 1 else 'points'}."})
    heads = sorted(facts["heads"], key=lambda h: -h[0])
    # a matchup call ("Berube's plan for the Oilers' top line") never makes the headline (Sora's review, 2026-10-17)
    call_head = next((c["head"] for c in claims if c.get("kind") not in ("matchup", "history") and c.get("head")), None)
    head = (heads[0][1] if heads and (heads[0][0] >= HEAD_FACT or not call_head) else call_head or f"{W.place(away)} at {W.place(home)}")
    lean = {t: v["body"] for t, v in (outlook or {}).items()}
    paras = [" ".join(sents)] if sents else []
    paras += [x for x in (luck, more["play"], more["scoring"], more["net"]) if x]
    if look:  # separate ideas, so each gets its own way in (Sora, 2026-10-30: two lines side by side read as related)
        look.sort(key=lambda x: {"matchup": 0, "history": 1}.get(x[0], 2))
        out_ = [look[0][1]]
        for kind, g in look[1:]:
            if kind == "history" and g.startswith("Don’t read much into"):
                out_.append("And in goal, d" + g[1:])
            elif kind == "history" and g.startswith("In goal, "):
                out_.append(g)
            else:
                out_.append(_pick(["Elsewhere, ", "One more thing: "], gid, kind, "look") + preview_more._lower(g))
        paras.append(" ".join(out_))
    paras = W.nicknames(paras, gid)
    # the players whose names carry a stat tag in the text, so the page can link them (Sora, 2026-10-30)
    people = {N[p]: p for p in set(more["used"]) | {x["id"] for x in (projected or {}).values() if x and x.get("id")} if p in N}
    return {"kicker": "Before the game", "head": head, "paras": paras, "lean": lean, "list_head": "Who to watch", "items": picked,
            **({"people": people} if people else {})}


def _leader(t: str, before: str, season: int) -> tuple[int, int] | None:
    _, P, _ = book()
    p = P.filter((pl.col("season") == season) & (pl.col("team") == t) & (pl.col("date") < before[:10]))
    if p.is_empty():
        return None
    r = p.group_by("player_id").agg(pl.col("pts").sum()).sort("pts", "player_id", descending=[True, False]).row(0)
    return (r[0], int(r[1])) if r[1] > 0 else None


# --- post-game -----------------------------------------------------------------------------------------------------

def _goals(gid: int, season: int, home_id: int) -> list[dict]:
    """The game's goals in order (shootout left out): period, seconds into it, team side, scorer and helpers, strength."""
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet").filter((pl.col("game_id") == gid) & pl.col("type").is_in(["goal", "penalty"]))
    pens = [(r["team_id"], r["sec"]) for r in ev.filter(pl.col("type") == "penalty").iter_rows(named=True)]
    out, hs, as_ = [], 0, 0
    gx = TABLES / str(season) / "goals.parquet"
    xg = ({r["event_id"]: r["xg"] for r in pl.read_parquet(gx, columns=["game_id", "event_id", "xg"]).filter(pl.col("game_id") == gid).iter_rows(named=True)}
          if gx.exists() else {})
    for r in ev.filter(pl.col("type") == "goal").sort("sort").iter_rows(named=True):  # `sec` counts from the opening faceoff; scores are kept here
        if r["period_type"] == "SO":
            continue
        code = str(r["situation_code"] or "1551")
        home = r["team_id"] == home_id
        a_sk, h_sk = int(code[1]), int(code[2])
        mine, theirs = (h_sk, a_sk) if home else (a_sk, h_sk)
        ps = code in ("1010", "0101")  # a penalty shot: one shooter against one goalie
        empty = not ps and ((code[3] == "0") if not home else (code[0] == "0"))  # the other side's goalie off
        own_off = not ps and ((code[0] == "0") if not home else (code[3] == "0"))  # the scorers' own goalie off
        # their sixth skater stood in for their own goalie, so he is no power-play advantage: Fantilli's 6-on-5 goal
        # during a delayed penalty (code 0651, CBJ at MIN 2026-10-18) read "on the power play" (outside notes)
        sk = mine - 1 if own_off and not empty else mine
        delayed = own_off and not empty and sk == theirs and any(tid != r["team_id"] and abs(sec - r["sec"]) <= 2 for tid, sec in pens)
        strength = ("ps" if ps else "en" if empty else "dp" if delayed else "pp" if sk > theirs else "sh" if sk < theirs
                    else "ev")
        hs, as_ = hs + home, as_ + (not home)
        out.append({"period": r["period"], "sec": r["sec"] - 1200 * (r["period"] - 1), "home": home, "p1": r["p1"], "p2": r["p2"], "p3": r["p3"],
                    "xg": xg.get(r["event_id"]),
                    "strength": strength, "hs": hs, "as": as_,
                    # the other side had pulled its goalie for an extra attacker (more skaters than the scorers), not
                    # just left the net during a delayed penalty
                    "extra": empty and theirs > mine,
                    "goalie": r["p1"] in (r["home_goalie"], r["away_goalie"]), "zone": r["zone"], "shot": r["shot_type"],
                    # the scorers' own goalie pulled for an extra attacker: third period or later, and no penalty
                    # against the other side at that moment (a delayed penalty also puts six skaters out; 2026-10-17,
                    # Sora's review: a second-period goal read "with the goalie pulled")
                    "own_extra": ((code[0] == "0") if not home else (code[3] == "0")) and mine > theirs and r["period"] >= 3
                    and not any(tid != r["team_id"] and abs(sec - r["sec"]) <= 2 for tid, sec in pens)})
    return out


def _shootout(gid: int, season: int) -> list[dict]:
    """Every shootout attempt in order, from the raw play-by-play (our tables leave the shootout out): team id, shooter,
    scored."""
    f = RAW / str(season) / "pbp" / f"{gid}.json.gz"
    if not f.exists():
        return []
    out = []
    for p in json.loads(gzip.open(f).read()).get("plays", []):
        if (p.get("periodDescriptor") or {}).get("periodType") != "SO":
            continue
        d, kind = p.get("details") or {}, p.get("typeDescKey")
        if kind in ("goal", "shot-on-goal", "missed-shot", "failed-shot-attempt"):
            pid = d.get("scoringPlayerId") if kind == "goal" else d.get("shootingPlayerId")
            if pid and d.get("eventOwnerTeamId"):
                out.append({"tid": d["eventOwnerTeamId"], "pid": pid, "scored": kind == "goal"})
    return out


def _so_winner(tries: list[dict], winner_id: int) -> tuple[int | None, str]:
    """See timeline.so_winner: the shooter whose shootout goal decided it."""
    from pipeline.export import timeline
    return timeline.so_winner(tries, winner_id)[:2]


def _ps_stopped(gid: int, season: int) -> list[dict]:
    """Penalty shots that did not go in: shooter, goalie, period, seconds into it."""
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet").filter(
        (pl.col("game_id") == gid) & pl.col("type").is_in(["shot-on-goal", "missed-shot"]) & pl.col("situation_code").cast(pl.Utf8).is_in(["1010", "0101"]))
    return [{"p1": r["p1"], "goalie": r["goalie_id"], "period": r["period"], "sec": r["sec"] - 1200 * (r["period"] - 1), "type": r["type"],
             "home": bool(r["is_home"])}
            for r in ev.sort("sort").iter_rows(named=True)]


DROUGHT = 960  # a team's longest regulation stretch without a shot on goal, told from 16:00 (about 1 team-game in 35, 2023-26;
# 15:00 came up in 1 game in 7 of 2026-27, too often to be news)


def _longest_gaps(ev: pl.DataFrame, home: bool) -> tuple[int, int, int]:
    """(gap, start, end) of one side's longest regulation stretch without a shot on goal, from puck drop to the end of
    the third."""
    ts = sorted([0, *ev.filter((pl.col("is_home") == home) & pl.col("type").is_in(["shot-on-goal", "goal"]))["sec"].to_list(), 3600])
    return max(((b - a, a, b) for a, b in zip(ts, ts[1:])), default=(0, 0, 0))


@lru_cache(maxsize=1)
def _drought_norms() -> list[int]:
    """Every team-game's longest stretch without a shot on goal over the full seasons, sorted."""
    out = []
    for s in FULL_SEASONS:
        d = TABLES / str(s)
        if not (d / "events.parquet").exists():
            continue
        g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id")
        ev = (pl.read_parquet(d / "events.parquet", columns=["game_id", "period", "sec", "type", "is_home"])
              .filter((pl.col("period") <= 3) & pl.col("type").is_in(["shot-on-goal", "goal"])).join(g, on="game_id"))
        for (_, h), part in ev.group_by(["game_id", "is_home"]):
            out.append(_longest_gaps(part, h)[0])
    return sorted(out)


def shot_droughts(gid: int, season: int, home: str, away: str) -> list[dict]:
    """A team's long stretch without a shot on goal (Sora, 2026-10-29: Boston went 20:39 without one at Carolina, and
    the story never said so). Regulation only, 15:00 or longer. Each: team, start and end (game seconds), gap, the
    other team's shots on goal in it, seconds of power play the team had in it, whether the game reached overtime,
    and `rare`: "never" when no team-game of the full seasons went that long, "season" when most teams never go that
    long in a season (82 games)."""
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return []
    ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "period", "period_type", "sec", "type", "is_home", "situation_code"]).filter(
        pl.col("game_id") == gid)
    ot = ev.filter(pl.col("period") > 3).height > 0
    reg = ev.filter(pl.col("period") <= 3).sort("sec")
    norms = _drought_norms()
    out = []
    for is_home, t in ((False, away), (True, home)):
        gap, a, b = _longest_gaps(reg, is_home)
        if gap < DROUGHT:
            continue
        other = reg.filter((pl.col("is_home") != is_home) & pl.col("type").is_in(["shot-on-goal", "goal"]) & (pl.col("sec") > a) & (pl.col("sec") < b)).height
        pp = 0  # seconds the team had more skaters, both goalies in, by the situation code of the play before
        rows_ = reg.filter((pl.col("sec") >= a) & (pl.col("sec") <= b)).select("sec", "situation_code").rows()
        for (s0, c), (s1, _) in zip(rows_, rows_[1:]):
            c = str(c or "").zfill(4)
            if len(c) == 4 and c[0] == "1" == c[3]:
                mine, theirs = (int(c[2]), int(c[1])) if is_home else (int(c[1]), int(c[2]))
                pp += (s1 - s0) if mine > theirs else 0
        p = (len(norms) - __import__("bisect").bisect_left(norms, gap)) / len(norms) if norms else 0
        out.append({"team": t, "start": a, "end": b, "gap": gap, "other": other, "pp": pp, "ot": ot,
                    "rare": None if not norms else "never" if gap > norms[-1] else "season" if (1 - p) ** 82 > 0.5 else None})
    return out


WIPED_WHY = {"off-side": "for offside", "goal-interference": "for goalie interference", "missed-stoppage": "for a missed stoppage"}
REVIEW_WHY = {"high-stick": "for a high stick", "hand-pass": "for a hand pass"}  # league reviews; most give no reason
SHOT_TYPES = ("shot-on-goal", "missed-shot")  # a blocked shot is owned by the blocking team
REVIEW_GAP = 10


def _overturned(gid: int, season: int) -> list[dict]:
    """Goals taken off on a coach's or the league's challenge (eye test, 2026-10-13: Montréal's overturned overtime
    winner at home to Detroit went untold). The NHL deletes the goal from the play-by-play and leaves a stoppage whose
    reason is "chlg-{hm|vis|league}-{why}". A failed challenge keeps the goal and restarts at center ice; a successful
    one restarts at a neutral-zone dot outside the blue line where the goal was scored, which says whose goal it was
    (checked on all 13 challenges of 2026-27 so far, and it names Montréal for the league's overtime review).
    Goals the league itself reviews (a kicked puck, a high stick, a hand pass; Sora's eye test, 2026-10-15: Colorado's
    kicked goal against Dallas went untold) leave a stoppage whose reason or secondary reason is "video-review" and no
    reason why. One counts as a goal that didn't count when play restarts at a neutral-zone dot with no penalty at that
    second and the side the dot names took the last shot attempt, within REVIEW_GAP seconds (about 30 a season; the
    rest restart in the end zone after a review that found no goal, or come with a penalty review)."""
    f = RAW / str(season) / "pbp" / f"{gid}.json.gz"
    if not f.exists():
        return []
    d = json.loads(gzip.open(f).read())
    plays, home_id = d.get("plays", []), (d.get("homeTeam") or {}).get("id")
    out = []
    for i, p in enumerate(plays):
        det = p.get("details") or {}
        why = det.get("reason") or ""
        if not why.startswith("chlg-") and (det.get("secondaryReason") or "").startswith("chlg-"):
            why = det["secondaryReason"]  # "referee-or-linesman" with the challenge as the second reason (OTT-NYI, 2026-10-15)
        review = "video-review" in (why, det.get("secondaryReason"))
        if p.get("typeDescKey") != "stoppage" or not (why.startswith("chlg-") or review):
            continue
        when = p.get("timeInPeriod") or "0:00"
        same = [x for x in plays if x.get("timeInPeriod") == when and (x.get("periodDescriptor") or {}).get("number") == p["periodDescriptor"]["number"]]
        if any(x.get("typeDescKey") == "goal" for x in same):
            continue  # the goal stood
        fo = next((x for x in plays[i + 1:i + 6] if x.get("typeDescKey") == "faceoff"), None)
        fx = ((fo or {}).get("details") or {}).get("xCoord")
        if not fx:
            continue  # center ice: the goal stood
        defends = p.get("homeTeamDefendingSide")
        if not why.startswith("chlg-"):
            if abs(fx) > 30 or defends not in ("left", "right") or any(x.get("typeDescKey") == "penalty" for x in same):
                continue  # an end-zone restart (no goal found) or a penalty review
            home_goal = (fx > 0) == (defends == "left")
            m, sec = when.split(":")
            shot = next((x for x in reversed(plays[max(0, i - 8):i]) if x.get("typeDescKey") in SHOT_TYPES), None)
            if not shot or (shot.get("periodDescriptor") or {}).get("number") != p["periodDescriptor"]["number"]:
                continue
            sm, ss = (shot.get("timeInPeriod") or "0:00").split(":")
            owner_home = (shot.get("details") or {}).get("eventOwnerTeamId") == home_id
            if owner_home != home_goal or int(m) * 60 + int(sec) - (int(sm) * 60 + int(ss)) > REVIEW_GAP:
                continue
            out.append({"home": home_goal, "why": REVIEW_WHY.get(why, "after a video review"), "period": p["periodDescriptor"]["number"],
                        "sec": int(m) * 60 + int(sec), "league": True})
            continue
        if "-hm-" in why or "-vis-" in why:  # a coach only challenges the other team's goal (eye test on 2025-26:
            home_goal = "-vis-" in why        # the restart dot named Carolina for Montréal's goal)
        elif defends in ("left", "right"):
            home_attacks_right = defends == "left"
            home_goal = (fx > 0) == home_attacks_right
        else:
            continue  # a league review with no side to read: say nothing rather than guess
        kind = why.split("-", 2)[2] if why.count("-") >= 2 else ""
        m, sec = when.split(":")
        out.append({"home": home_goal, "why": WIPED_WHY.get(kind, "on a challenge"), "period": p["periodDescriptor"]["number"],
                    "sec": int(m) * 60 + int(sec), "league": "-league-" in why})
    return out


def _wipe_after(mine: list[dict], goals: list[dict], home: bool, won: bool, margin: int, end: str, pk) -> str:
    """How the goals a team had taken off played out (Sora, 2026-10-15: talk about it like a fan would). In order: it
    cost them in a close loss (lost by no more than the goals taken off), they scored the game's next goal within five
    minutes ("got it back"), their power play matched it in a win, or they won anyway. A lopsided loss gets nothing:
    the goals taken off didn't decide it, and saying what would have happened is a guess."""
    n = len(mine)
    at = lambda x: (x["period"] - 1) * 1200 + x["sec"]
    first = min(at(x) for x in mine)
    if not won and (margin == 1 or end in ("OT", "SO")):
        one = "That one" if n == 1 else "Those"
        if end in ("OT", "SO"):
            # only a goal taken off late reads as costing them the win in regulation (the outside notes, Oct 20:
            # SJS-MTL's first-period goal "stung" after the overtime winner read as cause and effect)
            if max(x["period"] for x in mine) < 3:
                return ""
            went = "a shootout" if end == "SO" else "overtime"
            return f" {'That disallowed goal' if n == 1 else 'Those disallowed goals'} stung: the game went to {went}."
        return " " + pk([f"{one} stung in a one-goal loss.", "It mattered: they lost by one."])
    if not won and margin <= n:
        return f" They lost by {num(margin)}, as many goals as came off."
    nxt = next((g for g in goals if at(g) >= max(at(x) for x in mine)), None)
    if n == 1 and nxt and nxt["home"] == home and at(nxt) - at(mine[0]) <= 300:
        gap = at(nxt) - at(mine[0])
        later = f"{grammar.span(gap)} later" if gap < 60 else f"{gap // 60}:{gap % 60:02d} later"
        return " " + pk([f"They got it back {later}.", f"They didn’t wait long to get it back: {later}."])
    if won:
        pp = [g for g in goals if g["home"] == home and g["strength"] == "pp" and at(g) > first]
        if len(pp) >= n:
            return " " + pk(["The power play gave it back.", "They got it back on the power play."] if n == 1 else
                            ["The power play gave them back.", "They got them back on the power play."])
        return " " + pk(["It didn’t cost them in the end.", "They won anyway.", "No harm done: they won anyway.",
                         "It didn’t matter in the end." if n == 1 else "They didn’t matter in the end."])
    return ""


def _left(g: dict) -> str:
    s = 1200 - g["sec"]
    return ("1 second" if s == 1 else f"{s} seconds") if s < 60 else f"{s // 60}:{s % 60:02d}"


STREAK_END = 5  # a point streak this long that ended tonight is told (Stamkos's and Josi's five, 2026-10-13)


VS_STREAK_END = 7  # a point streak against one club told as ended only from here: at five or six it filled the list with
# a non-event in 4 of 13 games (voice loop, 2026-10-30); one that goes on is told from STREAK_END


def _streak_ends(gid: int, day: str, tonight, pg, G, P, N, W, winner: str, loser: str, home: str, away: str, season: int) -> list[dict]:
    """Streaks that ended or grew tonight: a player's point streak of STREAK_END+ games that ended, a point streak
    against tonight's opponent (the preview's "Owns them") that ended or went on, and the second game of a
    home-and-home ("the Stars split the two-game set")."""
    out = []
    for pr in tonight.iter_rows(named=True):
        pid = pr["player_id"]
        if not N.get(pid):
            continue
        before = pg.filter((pl.col("player_id") == pid) & (pl.col("team") == pr["team"]) & (pl.col("game_id") != gid)).sort("date")
        n = run_of(before["pts"].to_list())
        if n >= STREAK_END and not pr["pts"]:
            out.append({"tag": "Streak over", "text": f"{N[pid]}’s {num(n)}-game point streak ended.", "score": 44 + n})
        opp = away if pr["team"] == home else home
        vs = P.filter((pl.col("player_id") == pid) & (pl.col("opp") == opp) & (pl.col("date") < day)).sort("date")
        m = run_of(vs["pts"].to_list())
        if m >= STREAK_END and (pr["pts"] or m >= VS_STREAK_END):
            whole = m == vs.height and vs["date"][0] <= LOGS_START  # the run reaches back to where our logs begin
            # "after at least five games" puzzled Sora: say what we know, every meeting since our logs begin
            text = (f"{N[pid]} has a point in all {num(m + 1)} of his games against {W.place(opp)} since 2023-24." if pr["pts"] and whole else
                    f"{N[pid]} extended his point streak against {W.place(opp)} to {num(m + 1)} games." if pr["pts"] else
                    (f"{N[pid]} had a point in all {num(m)} of his games against {W.place(opp)} since 2023-24, and that run ended." if whole else
                     f"{N[pid]}’s {num(m)}-game point streak against {W.place(opp)} ended."))
            out.append({"tag": "Against them", "text": text, "score": 50 + m})
    prev = G.filter((pl.col("date") < day) & ((pl.col("home") == winner) | (pl.col("away") == winner))).tail(1)
    prev_l = G.filter((pl.col("date") < day) & ((pl.col("home") == loser) | (pl.col("away") == loser))).tail(1)
    if prev.height and prev_l.height and prev["game_id"][0] == prev_l["game_id"][0]:
        from datetime import date as D_
        gap = (D_.fromisoformat(day) - D_.fromisoformat(prev["date"][0])).days
        if gap <= 2:  # the second of two games in a row between the same teams
            x = prev.row(0, named=True)
            won_first = (x["home_score"] > x["away_score"]) == (x["home"] == winner)
            when = "a night after" if gap == 1 else "two days after"
            text = (f"{_cap(W.the(winner))} made it two straight over {W.the(loser)}." if won_first else
                    f"{_cap(W.the(winner))} split the two-game set, {when} losing to {W.the(loser)}.")
            out.append({"tag": "Rematch", "text": text, "score": 54})
    return out


EVEN_GAP = 8  # 5-on-5 shots on goal the winner had more of, for the 5-on-5 count to ride beside the shot total ...
EVEN_OTHER = 4  # ... when the loser had this many shots outside 5-on-5 ...
EVEN_SHARE = 0.05  # ... and the winner's share of 5-on-5 shots was this much bigger than of all shots


def _even_shots(gid: int, season: int, home: str, away: str) -> dict[str, int]:
    """Each team's shots on goal at 5-on-5 with both goalies in (penalty shots and shootouts out)."""
    d = TABLES / str(season)
    if not (d / "events.parquet").exists():
        return {}
    ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "type", "is_home", "situation_code", "period_type"]).filter(
        (pl.col("game_id") == gid) & pl.col("type").is_in(["shot-on-goal", "goal"]) & (pl.col("period_type") != "SO")
        & (pl.col("situation_code").cast(pl.Utf8) == "1551"))
    return {home: ev.filter(pl.col("is_home")).height, away: ev.filter(~pl.col("is_home")).height}


FOLLOW_TAGS = ("Haunts them", "Behind the record", "In net", "Bad blood", "Hot streak", "Milestone", "Club history")


def _saved_preview_items(gid: int) -> list[tuple[str, str]]:
    """The Who-to-watch items the game's preview printed: (tag, text)."""
    f = SITE_DATA / "previews" / f"{gid}.json"
    if not f.exists():
        return []
    try:
        items = (json.loads(f.read_text()).get("story") or {}).get("items") or []
    except ValueError:
        return []
    return [(x.get("tag", ""), x.get("text", "")) for x in items if x.get("tag") in FOLLOW_TAGS]


def _word_number(word: str) -> int | None:
    w = word.lower()
    return grammar.WORDS.index(w) if w in grammar.WORDS else int(w.replace(",", "")) if w.replace(",", "").isdigit() else None


def _preview_followups(gid: int, r: dict, season: int, P: pl.DataFrame, tonight: pl.DataFrame, N: dict[int, str], W: Words,
                       winner: str, loser: str, home: str, away: str, fought: bool) -> list[dict]:
    """What came of the storylines the preview told (Sora, 2026-10-29: the post-game story built everything from the
    game itself and never picked up the threads the teams and players brought into it). Only items the preview
    printed are followed, and only when tonight added to them: a player who haunts a team scored again, a club whose
    record ran ahead of its chances won without the chances again or had them catch up, a goalie's record on this ice,
    bad blood that carried over, a short point streak that ended, a milestone or club list that got closer. What other
    items already say (streaks of five, old teams, rematches, brothers, a first goal of the season, a milestone or club
    list reached) is left to them."""
    out = []
    day = r["date"]
    line = {row["player_id"]: row for row in tonight.iter_rows(named=True)}

    def named(text: str) -> list[int]:
        return sorted((p for p in line if N.get(p) and N[p] in text), key=lambda p: text.index(N[p]))
    for tag, text in _saved_preview_items(gid):
        if tag == "Haunts them":
            for pid in named(text)[:1]:
                me = line[pid]
                if not me["g"]:
                    continue
                vs = P.filter((pl.col("player_id") == pid) & (pl.col("opp") == me["opp"]) & (pl.col("date") <= day))
                g_, gp = int(vs["g"].sum()), vs.height
                again = "again" if me["g"] == 1 else "twice more" if me["g"] == 2 else f"{num(int(me['g']))} more times"
                out.append({"tag": "Haunts them", "score": 56,
                            "text": f"{N[pid]} scored {again} against {W.place(me['opp'])}, and has {num(g_)} goals in "
                                    f"{num(gp) if gp <= 12 else gp} games against them since 2023-24."})
        elif tag == "Behind the record":
            from pipeline.export import behind
            team = next((t for t in (away, home) if W.poss(t) in text or W.the(t) in text or _cap(W.the(t)) in text), None)
            nums = (r.get("numbers") or {})
            rec = behind.summary(season, day).get(team) if team else None
            if not rec or team not in nums:
                continue
            opp = home if team == away else away
            better = nums[team]["xg"] > nums[opp]["xg"]
            won = team == winner
            pp, play = behind.p3(rec["pp"]), behind.p3(rec["play"])
            if rec["gap"] > 0 and not better:
                text_ = (f"{_cap(W.the(team))} won without the better of the chances again. Their record ({pp}) has been well ahead of "
                         f"what their chances are worth ({play})." if won else
                         f"The chances caught up with {W.the(team)} this time. Their record ({pp}) had been well ahead of what "
                         f"their chances were worth ({play}).")
            elif rec["gap"] < 0 and better:
                text_ = (f"{_cap(W.the(team))} got the result their play has been pointing to. Their chances have been worth about "
                         f"{play}, better than their record ({pp})." if won else
                         f"{_cap(W.the(team))} had the better chances and lost again. Their chances have been worth about {play}, "
                         f"better than their record ({pp}).")
            else:
                continue
            out.append({"tag": "Behind the record", "text": text_, "score": 54})
        elif tag == "In net":
            from pipeline.metrics import venues
            g_ = next((x for x in r.get("goalies", []) if x.get("started") and N.get(x["id"]) and N[x["id"]] in text), None)
            if not g_ or g_["team"] == home:
                continue
            try:
                rec = venues.record(g_["id"], home, day)
            except Exception:
                rec = None
            if not rec or not rec["starts"]:
                continue
            who, at = N[g_["id"]], W.at(home)
            won = g_["team"] == winner
            if rec["w"] == 0:
                text_ = (f"{who} got his first career win {at}, in his {grammar.ordinal_word(rec['starts'] + 1)} start there." if won else
                         f"{who} is still looking for his first career win {at}.")
            elif "hasn’t won" in text:
                text_ = (f"{who} won {at}, ending a run of {num(rec['winless'])} starts there without a win." if won else
                         f"{who} is still looking for a win {at}, now {num(rec['winless'] + 1)} starts without one there.")
            elif won:
                text_ = f"{who} improved to {rec['w'] + 1}-{rec['l']}-{rec['o']} {at} in his career."
            else:
                continue
            out.append({"tag": "In net", "text": text_, "score": 52 if won else 44})
        elif tag == "Bad blood" and fought:
            out.append({"tag": "Bad blood", "text": "The bad blood from their last meeting carried over into this one.", "score": 48})
        elif tag == "Hot streak":
            goals = "scored in" in text
            ended = []
            for pid in named(text):
                me = line[pid]
                mine_ = P.filter((pl.col("player_id") == pid) & (pl.col("season") == season) & (pl.col("team") == me["team"])
                                 & (pl.col("date") < day)).sort("date")
                n = run_of(mine_["g" if goals else "pts"].to_list())
                # point streaks of STREAK_END or more are told by _streak_ends
                if n and not me["g" if goals else "pts"] and (goals or n < STREAK_END):
                    ended.append((pid, n))
            if len(ended) == 1:
                pid, n = ended[0]
                out.append({"tag": "Streak over", "score": 42,
                            "text": f"{grammar.possessive(N[pid])} {'goal' if goals else 'point'} streak ended at {num(n)} games."})
            elif ended:
                out.append({"tag": "Streak over", "score": 42,
                            "text": f"{_and([N[p] for p, _ in ended])} saw their {'goal' if goals else 'point'} streaks end."})
        elif tag in ("Milestone", "Club history"):
            m = re.search(r"is (\w+) (goals?|assists?|points?) from (passing .+|\d[\d,]*)\.$", text)
            pids = named(text)
            if not m or not pids or _word_number(m.group(1)) is None:
                continue
            pid = pids[0]
            me = line[pid]
            unit = m.group(2).rstrip("s")
            got = int({"goal": me["g"], "assist": me["pts"] - me["g"], "point": me["pts"]}[unit])
            left = _word_number(m.group(1)) - got
            if tag == "Milestone":  # counted again from his career, tonight included: the preview's day may be stale
                car = _careers().get(pid)
                target = _word_number(m.group(3))
                if not car or target is None:
                    continue
                past = [x for x in car if x[0] <= day[:10]]
                have = {"goal": sum(x[1] for x in past), "point": sum(x[2] for x in past)}
                have["assist"] = have["point"] - have["goal"]
                left = target - have[unit]
            if got and left > 0:
                out.append({"tag": tag, "score": 40,
                            "text": f"{N[pid]} is now {num(left)} {unit}{'' if left == 1 else 's'} from {m.group(3)}."})
    return out


CLUB_TOP = 10  # a move up a franchise's all-time scoring list is told inside its top ten (wire recaps tell these)
CLUB_TIE_TOP = 5  # drawing level is told only near the top: most passes come a game or two after a tie
CLUB_SEASONS = 10  # a younger franchise's top ten is a few seasons' work (Utah's tenth had 30 assists), so it is left out


@lru_cache(maxsize=1)
def _club_logs() -> dict[int, list[tuple[str, str, str, int, int]]]:
    """Every skater with a complete career on file: his regular-season games as (season, date, team, goals, points)."""
    from pipeline.ingest import careers
    out = {}
    for pid, c in careers.load_all().items():
        if (c.get("landing") or {}).get("position") == "G":
            continue
        out[pid] = [(s, x["gameDate"], x["teamAbbrev"], int(x.get("goals") or 0), int(x.get("points") or 0))
                    for s, log in c["logs"].items() for x in log]
    return out


def _club_totals(pid: int, fid: int, day: str, logs) -> dict[str, tuple[int, int]] | None:
    """(before tonight, after tonight) franchise totals of points, goals and assists, from our own game logs."""
    from pipeline.ingest import records
    games = logs.get(pid)
    if games is None:
        return None
    mine = [x for x in games if records.franchise_of(x[0], x[2]) == fid]
    out = {}
    for stat in ("points", "goals", "assists"):
        val = (lambda x: x[4]) if stat == "points" else (lambda x: x[3]) if stat == "goals" else (lambda x: x[4] - x[3])
        out[stat] = (sum(val(x) for x in mine if x[1] < day), sum(val(x) for x in mine if x[1] <= day))
    return out


def _club_lists(day: str, tonight, N, W, season: int) -> list[dict]:
    """A player who moved up his franchise's all-time list of points, goals or assists tonight, inside its top ten
    ("Aho passed Eric Staal for second on the Hurricanes' all-time points list"). Other names on the list come from the
    NHL's records site; anyone with a complete career on file is counted from our own logs as of tonight too."""
    from pipeline.ingest import names as names_, records
    logs = _club_logs()
    out = []
    for pr in tonight.filter(pl.col("pts") > 0).iter_rows(named=True):
        pid, t = pr["player_id"], pr["team"]
        fid = records.franchise_of(f"{season}{season + 1}", t)
        lead = records.leaders(fid) if fid and records.seasons_of(fid) >= CLUB_SEASONS else []
        mine = _club_totals(pid, fid, day, logs) if lead and N.get(pid) else None
        if not mine:
            continue
        for stat in ("points", "goals", "assists"):
            b, a = mine[stat]
            if a == b:
                continue
            others = []
            for r in lead:
                if r["playerId"] == pid:
                    continue
                own = _club_totals(r["playerId"], fid, day, logs) if r.get("activePlayer") else None
                others.append((r, own[stat] if own else (r[stat], r[stat])))
            rank = 1 + sum(1 for _, (_, ra) in others if ra > a)
            passed = [r for r, (rb, ra) in others if b <= rb and a > ra]
            tied = [r for r, (rb, ra) in others if b < rb and a == ra]
            if rank > CLUB_TOP or not (passed or tied and rank <= CLUB_TIE_TOP):
                continue

            def who(r):
                first, last = names_.spell(r["playerId"], r["firstName"], r["lastName"])
                return N.get(r["playerId"]) or f"{first} {last}"
            passed.sort(key=lambda r: -r[stat])
            lst = f"{W.poss(t)} all-time {stat} list" if stat != "points" else f"{W.poss(t)} all-time scoring list"
            if rank == 1 and passed and not tied:
                text = f"{N[pid]} passed {who(passed[0])} to become {W.poss(t)} all-time leader in {stat}, with {a:,}."
            elif passed:
                over = who(passed[0]) if len(passed) == 1 else f"{who(passed[-1])} and {who(passed[0])}" if len(passed) == 2 else f"{num(len(passed))} players"
                place = ORDINAL_N(rank) if not tied else f"a tie for {ORDINAL_N(rank)}"
                text = f"{N[pid]} passed {over} for {place} on {lst}, with {a:,}{' points' if stat == 'points' else ''}."
            else:
                text = f"{N[pid]} tied {who(tied[0])} for {ORDINAL_N(rank)} on {lst}, with {a:,}{' points' if stat == 'points' else ''}."
            out.append({"tag": "Club history", "text": text, "score": 76 - rank, "pid": pid, "rank": rank, "stat": stat, "team": t,
                        "tied": bool(tied) and not passed, "led": rank == 1 and bool(passed) and not tied,
                        "rival": (tied[0] if tied and not passed else passed[0] if passed else None) and who(tied[0] if tied and not passed else passed[0])})
            break  # one list per player: points first, then goals, then assists
    return sorted(out, key=lambda x: -x["score"])[:2]


BIG_SAVES, ROUGH_GOALS = 35, 5  # a goalie's night told in the story (eye test, 2026-10-14: Dobeš's five on 24 led
# Montréal's coverage, Luukkonen's 35 saves Buffalo's; neither was on our page)


# --- the turn of the game (outside coverage, Oct 16: COL 5-2 PIT and BOS 4-2 ANA) ----------------------------------
# Writers name the player who lit the comeback ("Lehkonen's spark"), say when the game turned in plain shot counts
# ("the Avalanche had just two shots when the Penguins were up two"), tell a power play that won the night ("3
# power-play goals", "killed all four"), name the late insurance goal, and close on each team's record.

SLOW_GAP = 6  # first-period shots a trailing winner was outshot by (and by 2 to 1) to read as a slow start ...
SLOW_TURN = 5  # ... and then outshot the other team by, the rest of the way
INSURANCE_SEC = 900  # third-period seconds after which a goal that made a one-goal lead two is told as insurance
LATE_RUSH = 3  # the winners' third-period goals (not into an empty net), from within one, that read "pour it on late"
SHAKY_SHOTS = 10  # a goalie who allowed 2+ goals within his first this-many shots, then stopped the rest ...
SETTLED_SAVES = 15  # ... of at least this many, "settled in after a shaky start"
PP_STORY = 3  # power-play goals that make the power play part of the story
KILL_STORY = 4  # penalties killed without a goal against that do


def deficit_at(goals: list[dict], side, loser: str) -> int | None:
    """Index of the goal that gave the losing team its biggest lead (the first time it got there)."""
    best, at = 0, None
    for i, g in enumerate(goals):
        d = (g["hs"] - g["as"]) * (1 if side({"home": True}) == loser else -1)
        if d > best:
            best, at = d, i
    return at


def spark(goals: list[dict], side, winner: str, loser: str) -> tuple[int, int] | None:
    """The comeback's spark: a winner who scored 2+ of the goals that erased the deficit, else whoever scored the first
    goal back. (pid, his goals in the comeback)."""
    at = deficit_at(goals, side, loser)
    if at is None:
        return None
    back = []
    for g in goals[at + 1:]:
        if side(g) == winner and g["strength"] != "en":
            back.append(g)
        if g["hs"] == g["as"]:
            break
    if not back:
        return None
    count = {}
    for g in back:
        count[g["p1"]] = count.get(g["p1"], 0) + 1
    two = max(count, key=count.get)
    return (two, count[two]) if count[two] >= 2 else (back[0]["p1"], 1)


def slow_start(periods: list[dict], winner: str, loser: str, W) -> str | None:
    """"The Avalanche had five shots in the first period and trailed 2-1, then outshot the Penguins 27-17 the rest of
    the way": a winner behind after the first and outshot 2 to 1 in it, who then had more of the shots."""
    if not periods or not periods[0].get("shots") or not periods[0].get("goals"):
        return None
    p1 = periods[0]
    sw, sl = p1["shots"].get(winner, 0), p1["shots"].get(loser, 0)
    gw, gl = p1["goals"].get(winner, 0), p1["goals"].get(loser, 0)
    rw = sum((p.get("shots") or {}).get(winner, 0) for p in periods[1:])
    rl = sum((p.get("shots") or {}).get(loser, 0) for p in periods[1:])
    if gw >= gl or sl - sw < SLOW_GAP or sw * 2 > sl or rw - rl < SLOW_TURN:
        return None
    shots = "one shot" if sw == 1 else f"{num(sw)} shots"
    return f"{_cap(W.the(winner))} had {shots} in the first period and trailed {gl}-{gw}, then outshot {W.the(loser)} {rw}-{rl} the rest of the way."


def insurance(goals: list[dict], side, winner: str) -> dict | None:
    """A winners' goal late in the third that made a one-goal lead two, when the lead never got back to one."""
    for i, g in enumerate(goals):
        if side(g) != winner or g["strength"] == "en" or g["period"] != 3 or g["sec"] < INSURANCE_SEC:
            continue
        lead = lambda x: (x["hs"] - x["as"]) * (1 if g["home"] else -1)
        if lead(g) == 2 and i and lead(goals[i - 1]) == 1 and all(lead(x) >= 2 for x in goals[i:]):
            return g
    return None


def late_rush(goals: list[dict], side, winner: str) -> bool:
    """The winners scored LATE_RUSH+ third-period goals (empty nets aside) from within one goal entering the third."""
    before = [g for g in goals if g["period"] < 3]
    lead = (before[-1]["hs"] - before[-1]["as"]) * (1 if side({"home": True}) == winner else -1) if before else 0
    third = [g for g in goals if g["period"] == 3 and side(g) == winner and g["strength"] != "en"]
    return lead <= 1 and len(third) >= LATE_RUSH


def special_teams(nums: dict, goals: list[dict], side, winner: str, loser: str, W, name, comeback: bool) -> tuple[list[str], list[str]]:
    """(lines to open the penalty paragraph, lines to close it). A power play of PP_STORY+ goals or a perfect kill of
    KILL_STORY+ is told; when the winners' power-play goals erased a deficit while the other side's power play got
    nothing, it is told as the night's turn ("special teams reversal", outside coverage of BOS at ANA)."""
    a, b = nums.get(winner) or {}, nums.get(loser) or {}
    if a.get("pp_goals") is None or b.get("pp_goals") is None:
        return [], []
    wp, wo, lp, lo = a["pp_goals"], a.get("pp_opps") or 0, b["pp_goals"], b.get("pp_opps") or 0
    Wt, Lt, Ln = W.the(winner), W.the(loser), W.nick.get(loser, loser)
    pick = lambda pool, *k: _pick(pool, "st", winner, loser, wp, wo, lp, lo, *k)
    at = deficit_at(goals, side, loser) if comeback else None
    if at is not None and lp == 0 and lo >= 3:
        pp = [g for g in goals[at + 1:] if side(g) == winner and g["strength"] == "pp"]
        if len(pp) >= 2:
            who = list(dict.fromkeys(g["p1"] for g in pp))
            names = _and([name(p) for p in who])
            did = (" all scored" if len(who) >= 3 else " both scored") if len(who) > 1 else f" scored {num(len(pp)) if len(pp) > 2 else 'twice'}"
            ahead = any((g["hs"] - g["as"]) * (1 if g["home"] else -1) > 0 for g in pp)
            hole = abs(goals[at]["hs"] - goals[at]["as"])
            turned = f"turned a {num(hole)}-goal hole into the lead" if ahead else f"erased a {num(hole)}-goal hole"
            start = pick(["Special teams flipped this one:", "The power plays decided it:"], "flip")
            return [f"{start} {names}{did} on the power play as {Wt} {turned}, and {Lt} went 0-for-{lo} on theirs."], []
    first, last_ = [], []
    perfect = lo >= KILL_STORY and lp == 0
    if wp >= PP_STORY:
        ratio = pick([f"scored {num(wp)} power-play goals on {num(wo)} chances", f"went {wp}-for-{wo} on the power play"], "pp") \
            if wo > wp else f"went {wp}-for-{wo} on the power play"
        mine = sum(1 for g in goals if side(g) == winner)
        if perfect and wp * 2 >= mine:
            first.append(f"Special teams did the work: {Wt} {ratio} and killed all {num(lo)} {Ln} power plays.")
            perfect = False
        else:
            first.append(f"{_cap(Wt)} {ratio}.")
    if lp >= PP_STORY:
        first.append(f"{_cap(Lt)} scored {num(lp)} power-play goals and still lost.")
    if perfect and wp == 0 and wo >= 2:  # both kills perfect: say so, not just the winners' (Sora's review, 2026-10-19)
        last_.append(f"Neither power play scored: {Wt} killed all {num(lo)} {Ln} power plays, and {Lt} killed {'both' if wo == 2 else 'all ' + num(wo)} of {grammar.possessive(W.place(winner))}.")
    elif perfect:
        last_.append(f"{_cap(Wt)} killed all {num(lo)} {Ln} power plays.")
    return first, last_


TWO_MAN_KILL = 60  # seconds of 5-on-3 (both goalies in) killed off that earn a line
POSTS = 3  # posts and crossbars by the losers that earn a line (2 in a one-goal game)


@lru_cache(maxsize=4)
def _sh_periods(season: int) -> list[tuple[str, str, int]]:
    """Every regular-season period since 2023-24 in which one team scored twice or more shorthanded (empty-net ones
    included, read from the situation code as `_goals` does): (date, team, game)."""
    out = []
    for s_ in range(2023, season + 1):
        d = TABLES / str(s_)
        try:
            ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "type", "period", "period_type", "is_home", "situation_code"]).filter(
                (pl.col("type") == "goal") & (pl.col("period_type") != "SO"))
            gm = pl.read_parquet(d / "games.parquet", columns=["game_id", "game_type", "date", "home", "away"]).filter(pl.col("game_type") == REGULAR)
        except Exception:
            continue
        n = {}
        for r in ev.iter_rows(named=True):
            code = str(r["situation_code"] or "1551")
            if code in ("1010", "0101"):
                continue
            a_sk, h_sk = int(code[1]), int(code[2])
            mine, theirs = (h_sk, a_sk) if r["is_home"] else (a_sk, h_sk)
            if (code[0] if r["is_home"] else code[3]) == "0":
                mine -= 1  # their own goalie off for an extra skater
            if mine < theirs:
                k = (r["game_id"], r["is_home"], r["period"])
                n[k] = n.get(k, 0) + 1
        info = {r["game_id"]: r for r in gm.iter_rows(named=True)}
        for (g_, h, p), c in n.items():
            if c >= 2 and g_ in info:
                out.append((info[g_]["date"], info[g_]["home"] if h else info[g_]["away"], g_))
    return sorted(out)


PP_DRY = 5  # games without a power-play goal before one is news


def pp_drought(gid: int, season: int, day: str, home: str, away: str, W) -> list[dict]:
    """"The Blue Jackets scored on the power play for the first time in seven games, after going 0-for-22." (outside
    notes, Oct 24: Fantilli's winner was Columbus' first power-play goal in 23 chances; Sennecke's was Anaheim's first
    in 15.) Counted from the league's own power-play lines, regular season, this season only."""
    from pipeline.ingest import game_stats
    pp = game_stats.lookup(season)
    G = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["game_id", "game_type", "date", "home", "away"])
    G = G.filter((pl.col("game_type") == REGULAR) & (pl.col("date") < day)).sort("date", descending=True)
    out = []
    for t in (away, home):
        if (pp.get((gid, t)) or (0, 0))[0] == 0:
            continue
        n = opps = 0
        since = None  # the last game they scored one
        for x in G.filter((pl.col("home") == t) | (pl.col("away") == t)).iter_rows(named=True):
            got = pp.get((x["game_id"], t))
            if got is None:
                n = 0  # a game with no saved line: say nothing rather than guess
                break
            if got[0]:
                since = x["date"]
                break
            n, opps = n + 1, opps + got[1]
        if n >= PP_DRY and opps:
            when_ = f"since {MONTHS[Date.fromisoformat(since[:10]).month - 1]} {Date.fromisoformat(since[:10]).day}" if since else "this season"
            out.append({"tag": "Power play", "score": 58,
                        "text": f"{_cap(W.the(t))} scored on the power play for the first time {when_}, after going 0-for-{opps} over {num(n)} games."})
    return out


def sh_pair(gid: int, season: int, day: str, goals: list[dict], home: str, away: str, W) -> list[dict]:
    """"The Senators scored twice shorthanded in the third, their first time doing that since Jan 30, 2025." (outside
    notes, Oct 21: NHL.com told how rare Ottawa's two shorthanded goals in one period were; we told the goals only.)"""
    out = []
    for t, is_home in ((away, False), (home, True)):
        for p in sorted({g["period"] for g in goals}):
            n = sum(1 for g in goals if g["period"] == p and g["home"] == is_home and g["strength"] == "sh")
            if n < 2 or p > 3:
                continue
            before = [d for d, tm, g_ in _sh_periods(season) if tm == t and (d, g_) < (day, gid)]
            if before:
                d = Date.fromisoformat(before[-1])
                when_ = f"their first time doing that since {MONTHS[d.month - 1]} {d.day}, {d.year}"
            else:
                when_ = "something they hadn’t done since at least the 2023-24 season"
            twice = "twice" if n == 2 else f"{num(n)} times"
            out.append({"tag": "Shorthanded", "score": 64,
                        "text": f"{_cap(W.the(t))} scored {twice} shorthanded in the {PERIODS[p]}, {when_}."})
    return out


def two_man_kills(gid: int, season: int, home_id: int, home: str, away: str, goals: list[dict], W) -> list[str]:
    """"The Blue Jackets killed off a two-man advantage that lasted just under two minutes early in the first." (outside
    notes, 2026-10-18: AP told Columbus's 1:52 kill and we did not.) From the stints, 5-on-3 stretches with both goalies
    in, merged across line changes; told only when no goal came in it."""
    try:
        st = pl.read_parquet(TABLES / str(season) / "stints.parquet").filter(pl.col("game_id") == gid).sort("start")
    except Exception:
        return []
    runs: list[list] = []  # [start, end, short side home?]
    for r in st.iter_rows(named=True):
        two = r["home_goalie"] is not None and r["away_goalie"] is not None and abs(r["n_home"] - r["n_away"]) == 2 \
            and min(r["n_home"], r["n_away"]) == 3
        if not two:
            continue
        short_home = r["n_home"] < r["n_away"]
        if runs and runs[-1][2] == short_home and r["start"] - runs[-1][1] <= 2:
            runs[-1][1] = r["end"]
        else:
            runs.append([r["start"], r["end"], short_home])
    out = []
    for a, b, short_home in runs:
        if b - a < TWO_MAN_KILL:
            continue
        if any(a <= 1200 * (g["period"] - 1) + g["sec"] <= b + 1 for g in goals):
            continue
        t = home if short_home else away
        p = a // 1200 + 1
        if p > 3:
            continue
        into = a - 1200 * (p - 1)
        part = "early in" if into < 400 else "midway through" if into < 900 else "late in"
        out.append(f"{_cap(W.the(t))} killed off a two-man advantage that lasted {grammar.span(b - a)} {part} the {grammar.ordinal_word(p)}.")
    return out[:1]


def posts_line(gid: int, season: int, ids: dict, winner: str, loser: str, margin: int, W) -> list[str]:
    """"The Hurricanes hit four posts and still lost." Outside notes, 2026-10-18 (Brind'Amour: "three or four
    goalposts"). Shots off the iron are misses to our chance model, so this is told as luck, never "should have scored"."""
    try:
        ev = pl.read_parquet(TABLES / str(season) / "events.parquet", columns=["game_id", "type", "team_id", "reason", "period_type"])
    except Exception:
        return []
    iron = ev.filter((pl.col("game_id") == gid) & (pl.col("type") == "missed-shot") & (pl.col("period_type") != "SO")
                     & pl.col("reason").is_in(["hit-left-post", "hit-right-post", "hit-crossbar"]))
    by = {ids.get(t): n for t, n in iron.group_by("team_id").len().iter_rows()}
    n = by.get(loser, 0)
    if not (n >= POSTS or (n >= 2 and margin == 1)):
        return []
    lid = next(t for t, a in ids.items() if a == loser)
    bar = iron.filter((pl.col("reason") == "hit-crossbar") & (pl.col("team_id") == lid)).height
    what = (f"{num(n)} posts" if not bar else f"the crossbar {'twice' if bar == 2 else num(bar) + ' times'}" if bar == n
            else f"{num(n - bar) if n - bar > 1 else 'a'} post{'s' if n - bar > 1 else ''} and {'the crossbar' if bar == 1 else 'the crossbar ' + ('twice' if bar == 2 else num(bar) + ' times')}")
    Lt = _cap(W.the(loser))
    pool = [f"{Lt} hit {what} and still lost.", f"{Lt} hit {what} and came up a goal short."] if margin == 1 else \
        [f"{Lt} hit {what} and still lost.", f"{Lt} hit {what}, and the bounces never came."]
    return [_pick(pool, "iron", gid)]


REDEEM_GSAX = 1.5  # tonight, goals saved above expected...
BAD_LAST = (5, -2.0)  # ...after a last start with 5+ goals against or 2+ below expected


@lru_cache(maxsize=2)
def _starts(season: int) -> pl.DataFrame:
    from pipeline.metrics import goalies
    g = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["game_id", "home_id", "away_id", "home", "away"])
    return goalies.per_game(season).filter(pl.col("started")).join(g, on="game_id").sort("game_id")


def bounced_back(gid: int, season: int, pid: int, W) -> str | None:
    """", who gave up seven goals in Colorado in his last start," for a goalie whose last start this season went badly
    (5+ against, or 2+ goals below expected) and who was good tonight (the caller checks tonight). Outside notes,
    2026-10-18: Cooley allowed seven in Colorado, then stopped 34 of 36 in Minnesota."""
    try:
        st = _starts(season)
    except Exception:
        return None
    before = st.filter((pl.col("goalie_id") == pid) & (pl.col("game_id") < gid))
    if not before.height:
        return None
    r = before.row(-1, named=True)
    ga, gsax = int(r["ga"]), float(r["xga"] - r["ga"])
    if ga < BAD_LAST[0] and gsax > BAD_LAST[1]:
        return None
    home = r["team_id"] == r["home_id"]
    other = r["away"] if home else r["home"]
    where = f"against {W.place(other)}" if home else W.at(other)
    return f", who gave up {num(ga)} goals {where} in his last start,"


def settled_in(gid: int, season: int, pid: int) -> tuple[int, int, int] | None:
    """(goals, shots by the last of them, saves after) for a goalie who allowed 2+ goals early and then stopped the rest."""
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet", columns=["game_id", "sort", "type", "goalie_id", "period_type"])
    shots = ev.filter((pl.col("game_id") == gid) & (pl.col("goalie_id") == pid) & pl.col("type").is_in(["shot-on-goal", "goal"])
                      & (pl.col("period_type") != "SO")).sort("sort")["type"].to_list()
    hits = [i for i, t in enumerate(shots) if t == "goal"]
    if len(hits) < 2 or hits[-1] >= SHAKY_SHOTS:
        return None
    after = len(shots) - hits[-1] - 1
    return (len(hits), hits[-1] + 1, after) if after >= SETTLED_SAVES else None


PLAYOFF_PICTURE = RACE_MOVES  # games left for the median club when the standings line starts saying what it means for the playoffs
UP_WORDS = ("taking", "climbing")  # standings phrases that move a club up ...
DOWN_WORDS = ("slipping", "sinking", "giving", "losing")  # ... or down


def _table_marks(rows: tuple) -> dict[str, dict]:
    """Each club's place in one day's standings: league rank by points and by points percentage (1 = best, ties share
    a rank, `*_n` how many share it), the same counted from the bottom, and its division place (the NHL's own order,
    tiebreakers included) out of how many."""
    from fractions import Fraction
    pct = {r["teamAbbrev"]: Fraction(r["points"], 2 * r["gamesPlayed"]) if r["gamesPlayed"] else Fraction(0) for r in rows}
    pts = {r["teamAbbrev"]: r["points"] for r in rows}
    size: dict[str, int] = {}
    for r in rows:
        size[r["divisionName"]] = size.get(r["divisionName"], 0) + 1
    out = {}
    for r in rows:
        t = r["teamAbbrev"]
        out[t] = {"pts": 1 + sum(v > pts[t] for v in pts.values()), "pts_n": sum(v == pts[t] for v in pts.values()),
                  "pct": 1 + sum(v > pct[t] for v in pct.values()), "pct_n": sum(v == pct[t] for v in pct.values()),
                  "pts_low": 1 + sum(v < pts[t] for v in pts.values()), "pct_low": 1 + sum(v < pct[t] for v in pct.values()),
                  "div": r["divisionSequence"], "div_n": size[r["divisionName"]], "division": r["divisionName"],
                  "gp": r["gamesPlayed"]}
    return out


def _league_top(m: dict) -> tuple[bool, bool]:
    return m["pts"] == 1, m["pct"] == 1


def _standing(t: str, opp: str, now: dict, was: dict | None, W) -> tuple[str, str] | None:
    """Where a club landed in the standings after the night, as (a phrase to ride on its record, a sentence of its own):
    ("taking sole possession of first in the NHL in both points and points percentage", "took sole possession of ...").
    League first place (points or points percentage) and last place come first, then the division: first place won or
    lost, the bottom reached or left, a move up or down, or simply where they sit (Sora, 2026-10-29)."""
    m, b = now[t], (was or {}).get(t)
    top_pts, top_pct = _league_top(m)
    was_pts, was_pct = _league_top(b) if b else (False, False)
    sole = lambda k: m[f"{k}_n"] == 1
    if top_pts or top_pct:
        if top_pts and top_pct and sole("pts") and sole("pct"):
            what = "first in the NHL in both points and points percentage"
            if b and was_pts and was_pct and b["pts_n"] == 1 and b["pct_n"] == 1:
                return f"still {what}", f"are still {what}"
            return f"taking sole possession of {what}", f"took sole possession of {what}"
        k = "pts" if top_pts else "pct"
        what = "points" if k == "pts" else "points percentage"
        if not sole(k):
            return f"tied for first in the NHL in {what}", f"are tied for first in the NHL in {what}"
        if b and (was_pts if k == "pts" else was_pct):
            return f"still first in the NHL in {what}", f"are still first in the NHL in {what}"
        return f"climbing into first in the NHL in {what}", f"climbed into first in the NHL in {what}"
    if b and (was_pts or was_pct):
        lost = "points percentage" if was_pct and not was_pts else "points" if was_pts and not was_pct else None
        o_pts, o_pct = _league_top(now[opp]) if opp in now else (False, False)
        if lost and ((lost == "points" and o_pts) or (lost == "points percentage" and o_pct)):
            best = "the most points" if lost == "points" else "the best points percentage"
            return f"giving up {best} in the NHL to {W.the(opp)}", f"gave up {best} in the NHL to {W.the(opp)}"
        return "slipping out of first in the NHL", "slipped out of first in the NHL"
    last = m["pts_low"] == 1 and m["pct_low"] == 1
    was_last = bool(b) and b["pts_low"] == 1 and b["pct_low"] == 1
    if last:
        return ("still last in the NHL", "are still last in the NHL") if was_last else ("sinking to last in the NHL", "sank to last in the NHL")
    if was_last:
        return "climbing out of last place in the NHL", "climbed out of last place in the NHL"
    d, n, div = m["div"], m["div_n"], m["division"]
    bd = b["div"] if b and b["division"] == div else None
    if d == 1:
        if bd == 1:
            return f"still first in the {div}", f"are still first in the {div}"
        return f"climbing into first in the {div}", f"climbed into first in the {div}"
    if bd == 1:
        if now.get(opp, {}).get("div") == 1 and now[opp]["division"] == div:
            return f"losing first place in the {div} to {W.the(opp)}", f"lost first place in the {div} to {W.the(opp)}"
        return f"slipping out of first in the {div}", f"slipped out of first in the {div}"
    if d == n:
        if bd == n:
            return f"still last in the {div}", f"are still last in the {div}"
        return f"sinking to the bottom of the {div}", f"sank to the bottom of the {div}"
    if bd == n:
        return f"climbing off the bottom of the {div}, now {ORDINAL_N(d)}", f"climbed off the bottom of the {div} to {ORDINAL_N(d)}"
    if bd and d < bd:
        return f"climbing to {ORDINAL_N(d)} in the {div}", f"climbed to {ORDINAL_N(d)} in the {div}"
    if bd and d > bd:
        return f"slipping to {ORDINAL_N(d)} in the {div}", f"slipped to {ORDINAL_N(d)} in the {div}"
    return f"{ORDINAL_N(d)} in the {div}", f"are {ORDINAL_N(d)} in the {div}"


def _playoff_part(t: str, rows: tuple) -> str | None:
    """In the playoff picture (the median club has `PLAYOFF_PICTURE` games or fewer left): where the night left a club
    against the playoff line, unless it has clinched or been eliminated. "holding the first wild card in the East",
    "two points out of a playoff spot"."""
    from pipeline.ingest import standings_daily as sd
    r = sd.race(rows, t)
    if not r or r["clinch"]:
        return None
    conf = {"Eastern": "East", "Western": "West"}.get(r["conference"] or "", r["conference"])
    if r["spot"][0] == "div":
        return "in playoff position"
    if r["spot"][0] == "wc":
        return f"holding {SPOT[r['spot']]} in the {conf}"
    if r["gap"] is None:
        return None
    return "level on points with the last playoff spot" if r["gap"] == 0 else f"{_points(-r['gap'])} out of a playoff spot"


def _swap(winner: str, loser: str, now: dict, was: dict | None, rows: tuple, prev: tuple | None, picture: bool,
          got: dict, raced: set, W) -> tuple[str, str | None] | None:
    """When the winner took a place straight from the club it beat (Sora, 2026-10-29: two lines saying the same swap
    read as repetitive), what it took, as "took first place in the Metropolitan from", plus an optional sentence of
    its own. In order: first in the NHL in points or points percentage, first in the division, a wild card (in the
    playoff picture, unless a Playoff race item tells it), then any other division place (outside the picture)."""
    if not was or winner not in was or loser not in was or loser not in now:
        return None
    w, l, bw, bl = now[winner], now[loser], was[winner], was[loser]
    taken = [k for k in ("pts", "pct") if bl[k] == 1 and w[k] == 1 and w[f"{k}_n"] == 1 and l[k] != 1]
    if taken:
        both = w["pts"] == 1 and w["pct"] == 1 and w["pts_n"] == 1 and w["pct_n"] == 1
        if len(taken) == 2:
            return "took first place in the NHL in both points and points percentage from", None
        what = "the most points" if taken[0] == "pts" else "the best points percentage"
        extra = f"That leaves {W.the(winner)} first in the NHL in both points and points percentage." if both else None
        return f"took {what} in the NHL from", extra
    if any(" NHL" in (got.get(t) or ("",))[0] for t in (winner, loser)):
        return None  # league news comes first
    div = w["division"]
    same = l["division"] == div and bw["division"] == div and bl["division"] == div
    if same and w["div"] == 1 and bl["div"] == 1:
        return f"took first place in the {div} from", None
    if picture and not ({winner, loser} & set(raced)) and prev:
        from pipeline.ingest import standings_daily as sd
        rw, rl, pl_ = sd.race(rows, winner), sd.race(rows, loser), sd.race(prev, loser)
        if rw and rl and pl_ and rw["conference"] == rl["conference"] and pl_["spot"][0] == "wc" \
                and rw["spot"] == pl_["spot"] and rl["spot"] != pl_["spot"] and not rw["clinch"]:
            conf = {"Eastern": "East", "Western": "West"}.get(rw["conference"] or "", rw["conference"])
            return f"took {SPOT[rw['spot']]} in the {conf} from", None
        return None
    if same and w["div"] == bl["div"] and bw["div"] > bl["div"] and l["div"] > w["div"]:
        return f"took {ORDINAL_N(w['div'])} place in the {div} from", None
    return None


def standings_after(season: int, day: str, winner: str, loser: str, W, raced: set = frozenset()) -> dict[str, tuple[str, str]]:
    """Each club's standings phrase once every game on `day` is final (the saved standings for that day: until then
    the post-game page shows only the record, and the next run after the last game adds it). The phrase is fixed to
    that night's table, so the page keeps telling where the night left them. Clubs in `raced` already have a Playoff
    race item, so the playoff part is left to it."""
    from pipeline.ingest import standings_daily as sd
    rows = sd.table(season, day[:10])
    if not rows:
        return {}
    now = _table_marks(rows)
    prev = sd.before(season, day[:10])
    was = _table_marks(prev) if prev else None
    gps = sorted(r["gamesPlayed"] for r in rows)
    picture = sd.GAMES - gps[len(gps) // 2] <= PLAYOFF_PICTURE
    out = {}
    for t, opp in ((winner, loser), (loser, winner)):
        if t not in now:
            continue
        got = _standing(t, opp, now, was, W)
        if not got:
            continue
        extra = _playoff_part(t, rows) if picture and t not in raced else None
        league = " NHL" in got[0]
        if extra == "in playoff position" and (league or "first in the" in got[0]):
            extra = None  # the league's or a division's top club: being in is a given
        if extra and extra.startswith("holding") and not got[0].startswith(UP_WORDS + DOWN_WORDS + ("still",)):
            got = (extra, "hold" + extra[len("holding"):])  # "fifth in the Central" says less than the wild card it holds
        elif extra:
            got = (f"{got[0]}, {extra}", f"{got[1]}, {extra}")
        out[t] = got
    swap = _swap(winner, loser, now, was, rows, prev, picture, out, raced, W)
    if swap:
        out["_swap"] = swap
    return out


def record_line(G: pl.DataFrame, day: str, winner: str, loser: str, W, season: int, gid, skip: set,
                standing: dict[str, tuple[str, str]] | None = None) -> str | None:
    """"The Bruins improved to 6-2-0, and the Ducks fell to 4-3-0." Teams in `skip` already had their record told.
    With `standing` (standings_after), each record carries where the club landed: "The Hurricanes improved to 10-2-1,
    taking sole possession of first in the NHL in both points and points percentage, and the Bruins fell to 9-3-1,
    giving up the best points percentage in the NHL to the Hurricanes." A club whose record was told elsewhere gets
    its place as a sentence of its own."""
    played = G.filter(pl.col("date") <= day)
    standing = standing or {}
    swap = standing.get("_swap")
    if swap and winner not in skip and loser not in skip:
        # one sentence for a place taken straight from the club they beat: "The Hurricanes improved to 10-2-2 and took
        # first place in the Metropolitan from the Devils, who fell to 9-3-1."
        head, extra = swap
        ls = standing.get(loser)
        sank = f" and {ls[1]}" if ls and ls[1].startswith("sank") else ""
        if sank and f"the {(div := sank.rsplit(' the ', 1)[-1])}" in head:
            sank = sank[: -len(div)] + "division"  # "took seventh place in the Metropolitan ... sank to the bottom of the division"
        line = (f"{_cap(W.the(winner))} {_pick(['improved to', 'moved to'], gid, 'rec', winner)} {record(results(played, winner, season))} "
                f"and {head} {W.the(loser)}, who {_pick(['fell to', 'dropped to'], gid, 'rec', loser)} {record(results(played, loser, season))}{sank}.")
        return f"{line} {extra}" if extra else line
    parts, alone = [], []
    for t, pool in ((winner, ["improved to", "moved to"]), (loser, ["fell to", "dropped to"])):
        if t not in skip:
            st = standing.get(t)
            # a winner that still slipped, or a loser that still climbed, reads with "but": "fell to 8-5-0 but moved up to second"
            against = st and st[0].startswith(UP_WORDS if t == loser else DOWN_WORDS)
            tail = (f" but {st[1]}" if against else f", {st[0]}") if st else ""
            parts.append(f"{W.the(t)} {_pick(pool, gid, 'rec', t)} {record(results(played, t, season))}{tail}")
        elif t in standing:
            alone.append(f"{_cap(W.the(t))} {standing[t][1]}.")
    if not parts:
        return " ".join(alone) or None
    if len(parts) == 2 and standing.get(winner):
        # one sentence, the winner's move told as a verb: "The Lightning moved to 8-3-1 and climbed to third in the
        # Atlantic, while the Jets dropped to 4-6-3, seventh in the Central." Two sentences built the same way closed
        # every story (voice loop, round two: rewritten as one in 12 of 13 games)
        st = standing[winner]
        moved = not st[1].startswith("are ")
        first = parts[0].rsplit(f", {st[0]}", 1)[0] + f" and {st[1]}" if moved and parts[0].endswith(f", {st[0]}") else parts[0]
        line = f"{_cap(first)}, while {parts[1]}."
    else:
        line = f"{_cap(' and '.join(parts) if len(parts) == 1 else parts[0] + ', and ' + parts[1])}."
    return " ".join([line, *alone])


QUIET_SHOTS = 18  # a winning goalie's shots faced at or under which his saves read "needed only"
KEPT_GSAX = 1.0  # goals saved above expected for a losing goalie who "kept them in it"


def _goalie_lines(r: dict, winner: str, loser: str, W, name, said: set, head: str, pk=None, season: int | None = None) -> list[str]:
    """Both starters' nights in one sentence, in every story (Sora's review of 2025-26 against NHL.com and AP,
    2026-10-17: the goalies were the most-missed fact, 530 games). The winner's goalie "made 30 saves"; a shutout
    "stopped all 31 shots"; the loser's "stopped 27 of 30", or "made 38 saves in a losing effort" (35+ and 1.5+ goals
    saved above expected), or "allowed five goals on 24 shots" (5+ and 1+ below expected). A goalie the first
    paragraph already named (pulled, or "the difference") is left out, so nothing is told twice. A winner's goalie who
    allowed 2+ goals within his first 10 shots and then stopped 15+ "settled in after a shaky start" (Blackwood, 2026-10-16)."""
    gl = {x["team"]: x for x in r.get("goalies", []) if x.get("started")}
    pk = pk or (lambda pool, *k: pool[0])
    parts = []
    wg, lg = gl.get(winner), gl.get(loser)
    ls = r["score"][loser]

    def nm(g, good=False):
        back = bounced_back(r["game_id"], season, g["id"], W) if good and season else None
        return (name(g["id"]) or g["name"]) + (back or "")
    if wg and wg.get("id") not in said and (wg.get("shots") or 0) > 0:
        saves = wg.get("saves") or 0
        early = settled_in(r["game_id"], season, wg["id"]) if season and _secs(wg.get("toi")) >= 3000 else None
        if ls == 0 and (wg.get("ga") or 0) == 0:
            n_ = nm(wg, True)
            parts.append(pk([f"{n_} stopped all {saves} shots for {W.the(winner)}", f"{n_} turned aside all {saves} shots for {W.the(winner)}",
                             f"{n_} was perfect for {W.the(winner)}, {saves} saves on {saves} shots"], "so-saves"))
        elif early:
            n_, at_, after_ = early
            parts.append(pk([f"{nm(wg)} settled in after a shaky start for {W.the(winner)}: he gave up {num(n_)} goals on the first {num(at_)} shots, then stopped the last {after_}",
                             f"{nm(wg)} shook off a shaky start for {W.the(winner)}, allowing {num(n_)} goals on the first {num(at_)} shots and none on the last {after_}"], "settled"))
        elif wg.get("shots", 0) <= QUIET_SHOTS and (wg.get("ga") or 0) <= 1:  # 14 shots: "made 14 saves" said nothing (voice loop)
            parts.append(f"{nm(wg)} needed only {saves} saves for {W.the(winner)}")
        else:
            parts.append(f"{nm(wg, (wg.get('gsax') or 0) >= REDEEM_GSAX or saves >= 30)} made {saves} saves for {W.the(winner)}")
    if lg and lg.get("id") not in said and (lg.get("shots") or 0) > 0:
        saves, ga, gsax, shots = lg.get("saves") or 0, lg.get("ga") or 0, lg.get("gsax") or 0, lg.get("shots") or 0
        for_ = f" for {W.the(loser)}" if not parts else ""
        if saves >= BIG_SAVES and gsax >= 1.5:
            parts.append(f"{nm(lg, True)} made {saves} saves in a losing effort{for_}")
        elif ga >= ROUGH_GOALS and gsax <= -1:
            parts.append(f"{nm(lg)} allowed {num(ga)} goals on {shots} shots{for_}")
        elif gsax >= KEPT_GSAX:
            # the loser's goalie who held his team in it got the same flat "stopped 28 of 30" as a quiet night (Swayman,
            # 1.9 goals saved above expected in a 2-1 loss; voice loop, 2026-10-30)
            parts.append(f"{nm(lg, True)} kept {W.the(loser)} in it, stopping {saves} of {shots}")
        else:
            # "and Nedeljkovic stopped 24 of 27 at the other end": the two goalies read as one exchange (voice loop, round two)
            parts.append(f"{nm(lg, gsax >= REDEEM_GSAX)} stopped {saves} of {shots}{for_ or ' at the other end'}")
    if not parts:
        return []
    if len(parts) == 2:
        return [f"{_cap(parts[0])}, and {parts[1]}."]
    return [f"{_cap(parts[0])}."]


def _assists(night: pl.DataFrame, pid: int) -> int:
    r = night.filter(pl.col("player_id") == pid)
    return int(r["a1"].sum() + r["a2"].sum()) if r.height else 0


BIG_NIGHT = 3  # points: a night outside recaps lead with (eye test, 2026-10-13: McDavid's four, Scheifele's three)


def _big_nights(night: pl.DataFrame, winner: str, loser: str, W, name, said: set, last, skip: set, most: int = 2, verb: str = "had") -> list[str]:
    """Up to two 3+ point nights, the winner's first, then the most points: "Connor McDavid had four points: a goal and
    three assists." A player already told scoring twice gets "also had two assists"."""
    rows = [r for r in night.iter_rows(named=True) if r["pts"] >= BIG_NIGHT and r["pts"] > r["g"] and r["player_id"] not in skip]
    rows.sort(key=lambda r: (r["team"] != winner, -r["pts"], -r["g"]))
    # One sentence for the night's big point totals (Sora's review, 2026-10-19): a lone "Novak also had an assist" is
    # not news, but "Novak added an assist for a three-point night" is, and two totals read as one sentence.
    lines = []
    keep = rows[:most]
    if len(rows) > most and all(r["pts"] == rows[0]["pts"] for r in rows[:most + 1]):  # a tie for the last place is not cut (Blake's three assists, 2026-10-20)
        keep = [r for r in rows if r["pts"] == rows[0]["pts"]][:4]
    for r in keep:
        pid, g, a, pts = r["player_id"], int(r["g"]), int(r["pts"] - r["g"]), int(r["pts"])
        known = pid in said
        assists = "an assist" if a == 1 else f"{num(a)} assists"
        if known and g >= 2:
            lines.append({"known": True, "who": last(pid), "what": f"{'two goals' if g == 2 else num(g) + ' goals'} and {assists}",
                          "clause": f"{last(pid)} added {assists} for a {num(pts)}-point night", "pts": pts})
            continue
        who = last(pid) if known else name(pid)
        if not who:
            continue
        if not known and r["team"] == loser:
            who = f"{W.poss(loser)} {who}"
        what = assists if not g else f"{'a goal' if g == 1 else num(g) + ' goals'} and {assists}"
        total = f"{num(pts)} points on {what}" if pts >= 4 and g else what
        lines.append({"known": False, "who": who, "what": what, "clause": f"{who} {verb} {total}", "had": f"{who} had {total}", "pts": pts})
    lines.sort(key=lambda x: x["known"])
    if not lines:
        return []
    if len(lines) == 1:
        return [_cap(lines[0]["clause"]) + "."]
    if len(lines) > 2:
        words = {3: "three", 4: "four"}
        parts = [f"{l['who']} with {l['what']}" for l in lines]
        same = len({l["pts"] for l in lines}) == 1  # "three three-point nights" stumbled (round 4)
        what = f"{num(lines[0]['pts'])}-point nights" if same else "three or more points"
        return [f"{_cap(words[len(lines)])} players had {what}: {', '.join(parts[:-1])}, and {parts[-1]}."]
    x, y = lines
    if not x["known"] and not y["known"] and x["what"] == y["what"]:  # "Bratt and Hughes each had a goal and two assists"
        return [f"{_cap(x['who'])} and {y['who']} each had {x['what']}."]
    if x["pts"] == y["pts"] and x["pts"] < 4:
        return [f"Two players had {num(x['pts'])}-point nights: {x['who']} with {x['what']}, and {y['who']} with {y['what']}."]
    second = y["clause"].replace(" added ", " had ", 1) if y["known"] and x["known"] else y["clause"] if y["known"] else y["had"]
    return [f"{_cap(x['clause'])}, and {second}."]


PUSH_SEC = 1200 - 240  # a loser's goal in the last four minutes that made it a one-goal game is told
def _when(g: dict) -> str:
    p, s = g["period"], g["sec"]
    t = ("1 second" if s == 1 else f"{s} seconds") if s < 60 else f"{s // 60}:{s % 60:02d}"
    if p > 3:
        return f"{t} into overtime" if s < 180 else "in overtime"
    if p == 1 and s < 120:
        return f"{t} in"
    part = "early in" if s < 400 else "midway through" if s < 900 else "late in"
    return f"{part} the {PERIODS[p]}"


def _day_before(d: str) -> str:
    return Date.fromordinal(Date.fromisoformat(d[:10]).toordinal() - 1).isoformat()


def _brothers(dressed: pl.DataFrame, away: str, home: str) -> list[tuple[tuple[int, str], tuple[int, str]]]:
    """Brothers on opposite sides of one game (Wikidata siblings), as ((id, team), (id, team)) pairs."""
    sib = storylines.siblings()
    team = dict(zip(dressed["player_id"].to_list(), dressed["team"].to_list()))
    out = []
    for pid, t in team.items():
        for b in sib.get(pid, ()):
            if pid < b and team.get(b) and team[b] != t and {t, team[b]} == {away, home}:
                out.append(((pid, t), (b, team[b])))
    return out


def _later_first(x: dict, night: pl.DataFrame, fighters: set) -> bool:
    """A first or a games-played mark told in What it means instead of the opening paragraph (Sora's review,
    2026-10-19: a first NHL game, a season debut or a 900th game leads the story only when the player did something:
    a point or a fight, or, for a first game, a first-round pick's debut). Goalie firsts stay with the goaltending."""
    k, pid = x["kind"], x["pid"]
    if x.get("goalie") or pid in fighters:
        return False
    if k == "debut":
        return not (x.get("points") or x.get("first_round"))
    if k == "return":
        return not (x.get("points") or x.get("goals")) and not x.get("since")
    if k == "club_debut":
        return not (x.get("points") or x.get("goals"))
    if k == "milestone" and x.get("what") == "game":
        return not x.get("goals")
    return False


def _first_items(later: list[dict], night: pl.DataFrame, N: dict, W: "Words", loser: str) -> list[dict]:
    """What it means items for the firsts kept out of the opening paragraph."""
    items = []
    debuts = [x for x in later if x["kind"] == "debut" and N.get(x["pid"])]
    if debuts:
        who = _and([f"{W.poss(x['team'])} {N[x['pid']]}" for x in debuts[:3]])
        text = f"{_cap(who)} played his first NHL game." if len(debuts) == 1 else \
            f"{_cap(who)} {'both' if len(debuts) == 2 else 'all'} played their first NHL game."
        items.append({"tag": "Debut", "text": text, "score": 50})
    for x in later:
        pid, k = x["pid"], x["kind"]
        if not N.get(pid) or k == "debut":
            continue
        who = f"{W.poss(x['team'])} {N[pid]}"
        if k == "milestone":
            r_ = night.filter(pl.col("player_id") == pid)
            g, pts = (int(r_["g"].sum()), int(r_["pts"].sum())) if r_.height else (0, 0)
            a = pts - g
            bits = (["a goal" if g == 1 else f"{num(g)} goals"] if g else []) + (["an assist" if a == 1 else f"{num(a)} assists"] if a else [])
            got = f" and had {_and(bits)}" if bits else ""
            items.append({"tag": "Milestone", "text": f"{_cap(who)} played his {grammar.ordinal(x['mark'])} NHL game{got}.", "score": 54})
        elif k == "return":
            text = (f"{_cap(who)} played his first game of the season, after missing the first {num(x['missed'])}." if x.get("season_debut") else
                    f"{_cap(who)} was back in the lineup after missing {num(x['missed'])} games.")
            items.append({"tag": "Back", "text": text, "score": 44})
        elif k == "club_debut":
            club = W.nick.get(x["team"], x["team"])
            n = x.get("seasons") or 0
            was = f", after {num(n)} seasons with {W.the(x['from'])}" if x.get("from") and n >= 2 else \
                f", after starting the season with {W.the(x['from'])}" if x.get("from") and x.get("moved") else ""
            items.append({"tag": "New club", "text": f"{N[pid]} made his {club} debut{was}.", "score": 40})
    return items


def _star_fight(say, fighters: list[int], gid: int, season: int, day: str, N: dict, name):
    """The fight sentence, followed by how rarely a star fights (Sora: MacKinnon and Hintz, 2026-10-13)."""
    def told(**kw) -> str:
        text = say(**kw)
        for x in storylines.star_fights(gid, season, day)[:2]:
            words = storylines.rare_fight_words(x["record"])
            if x["pid"] in fighters and words and N.get(x["pid"]):
                text += f" For {name(x['pid'])}, it was {words}."
        return text
    return told


def set_up_wins(goals: list[dict], home_won: bool) -> bool:
    """The winners' goals were mostly set up by a pass and rated low by our chance model: a win "without the chances"
    then reads as well-made plays the model can't see, not as luck (goal-tracking study, Sora approved 2026-10-25).
    Empty-net goals left out."""
    wg = [g for g in goals if g["home"] == home_won and g["strength"] != "en"]
    n = sum(1 for g in wg if g.get("p2") and g.get("xg") is not None and g["xg"] < surprise_rules.SET_UP_XG)
    return n >= 2 and 2 * n >= len(wg)


def recap_story(r: dict, places: dict, season: int = CURRENT_SEASON) -> dict | None:
    """The story for a finished game, from its post-game JSON (r), its Swing scores and the season's tables."""
    W = Words(places)
    G, P, N = book()
    gid, away, home = r["game_id"], r["away"], r["home"]
    row = G.filter(pl.col("game_id") == gid)
    if row.is_empty():
        return None
    home_id = row["home_id"][0]
    goals = _goals(gid, season, home_id)
    winner = home if r["score"][home] > r["score"][away] else away
    loser = away if winner == home else home
    ws, ls = r["score"][winner], r["score"][loser]
    end = r.get("end", "REG")
    side = lambda g: home if g["home"] else away
    last = lambda pid: _short(pid, P, N, season)
    said: set[int] = set()

    def name(pid: int) -> str:
        if pid in said:
            return last(pid)
        said.add(pid)
        return N.get(pid, "")
    # scorers
    by = {}
    for g in goals:
        by.setdefault(g["p1"], []).append(g)
    hat = [(pid, gs) for pid, gs in by.items() if len(gs) >= 3]
    twice = [pid for pid, gs in by.items() if len(gs) == 2 and side(gs[0]) == winner]
    two_l = [pid for pid, gs in by.items() if len(gs) == 2 and side(gs[0]) == loser]
    night = P.filter(pl.col("game_id") == gid)
    big_told: set[int] = set()
    # lead path
    lead_max = {home: 0, away: 0}
    for g in goals:
        d = g["hs"] - g["as"]
        lead_max[home] = max(lead_max[home], d)
        lead_max[away] = max(lead_max[away], -d)
    comeback = lead_max[loser] >= 2
    # A big lead that nearly got away (Sora, 2026-10-14: Edmonton led Vancouver 8-2 and won 9-7 after the Canucks
    # scored four straight): the winner led by 3+, then the loser cut it by 3+ goals to within one or two, never level.
    rally = None
    margins = [(g["hs"] - g["as"]) * (1 if winner == home else -1) for g in goals]
    if lead_max[winner] >= 3:
        top = margins.index(lead_max[winner])
        low = min(margins[top:])
        if 1 <= low <= 2 and lead_max[winner] - low >= 3:
            at = top + margins[top:].index(low)
            peak, cut = goals[top], goals[at]
            rally = {"peak": (max(peak["hs"], peak["as"]), min(peak["hs"], peak["as"])), "cut": (max(cut["hs"], cut["as"]), min(cut["hs"], cut["as"])),
                     "run": at - top, "lgoals": sum(side(g) == loser for g in goals[top + 1:at + 1])}
    gl = {x["team"]: x for x in r.get("goalies", []) if x.get("started")}
    from pipeline.export import timeline as _tl
    so_pid, so_how, so_all = _tl.so_winner(_shootout(gid, season), row["home_id"][0] if winner == home else row["away_id"][0]) if end == "SO" else (None, "", [])
    keeper_goals = [g for g in goals if g["goalie"]]  # a goalie scoring: a handful a decade, always the lead
    rb = r.get("replays") or {}
    share = (rb.get("share") or {}).get(winner)
    # headline (Sora, 2026-10-14: chattier and hotter, still true to the numbers). Every hot word is earned by a figure:
    # "blow out" needs a six-goal margin, "stands on his head" a shutout on 35+ shots, "highway robbery" a winner who
    # had a quarter of the replays or less. Pools are picked by the game id, so a page reads the same every run.
    heads = []
    pk = lambda pool, *k: _pick(pool, gid, "head", *k)
    W_, L_ = _cap(W.the(winner)), W.the(loser)
    found = _found_firsts(gid, season, G, P)
    res_w = results(G.filter(pl.col("date") <= r["date"]), winner, season)
    res_l = results(G.filter(pl.col("date") <= r["date"]), loser, season)
    rare = _rare_scorers(P, night, r["date"], N)
    heads += _fact_heads(found, night, res_w, res_l, winner, loser, W, W_, L_, last, N, rare)
    # the night's human hook leads, as NHL.com's headlines did on all 11 games of Oct 20 (outside notes): a club
    # record set or tied, a scorer back against his old club, a winner who blew a three-goal lead
    beat_ = f"{W.the(winner)} beat {L_}"
    tonight_ = P.filter((pl.col("season") == season) & (pl.col("date") <= r["date"])).filter(pl.col("game_id") == gid)
    for x in _club_lists(r["date"], tonight_, N, W, season):
        if x.get("rank") == 1 and (x.get("tied") or x.get("led")):
            what = {"points": "points", "goals": "goals", "assists": "assists"}.get(x["stat"], x["stat"])
            as_ = f" as {beat_}" if x["team"] == winner else ""
            rv = (x.get("rival") or "").split(" ")[-1]
            heads.append((96, f"{last(x['pid'])} ties {rv} for {W.poss(x['team'])} {what} record" if x.get("tied") and rv else
                          f"{last(x['pid'])} sets {W.poss(x['team'])} {what} record{as_}"))
    olds = old_club_returns(P, gid, r["date"], home)
    for x in olds[:1]:
        if x["g"] and N.get(x["pid"]):
            heads.append((87, f"{last(x['pid'])} scores in his return to {W.place(x['old'])}" if x["at_old"] else
                          f"{last(x['pid'])} scores against his old team"))
    blew = lead_max[winner] >= 3 and any(g["hs"] == g["as"] for g in goals[[(g["hs"] - g["as"]) * (1 if winner == home else -1) for g in goals].index(lead_max[winner]):])
    if blew:
        d_ = num(lead_max[winner])
        how_ = {"SO": "in a shootout", "OT": "in overtime"}.get(end, "anyway")
        heads.append((86, pk([f"{W_} blow a {d_}-goal lead, then win it {how_}" if how_ != "anyway" else f"{W_} blow a {d_}-goal lead and win anyway",
                              f"{W_} let a {d_}-goal lead slip, then win it {how_}" if how_ != "anyway" else f"{W_} let a {d_}-goal lead slip and win anyway"], "blew")))
    run_heads, run_lead, run_told = _run_story(res_w, res_l, winner, loser, W, pk)
    heads += run_heads
    if keeper_goals:
        heads.append((98, pk([f"Goalie {last(keeper_goals[0]['p1'])} scores", f"{last(keeper_goals[0]['p1'])} scores. Yes, the goalie"], "g")))
    hat_w = [pid for pid, gs in hat if side(gs[0]) == winner]
    if len(hat) >= 2:  # Verhaeghe and Bennett in one game (Sora's review, round 2: "TWO HAT TRICKS FOR FLORIDA!")
        a_, b_ = (last(p_) for p_, _ in sorted(hat, key=lambda x: goals.index(x[1][0]))[:2])
        heads.append((101, pk([f"Two hat tricks for {W.the(winner)}", f"Hat tricks for {a_} and {b_}"], "2hat") if len(hat_w) >= 2 else
                      f"Hat tricks for {a_} and {b_}"))
    if hat:
        pid, gs = max(hat, key=lambda x: len(x[1]))
        kinds = {g["strength"] for g in gs}
        hats = f", and the hats come down" if side(gs[0]) == home else ""
        if side(gs[0]) != winner:
            # A hat trick in a loss is a clause, not the headline (NHL.com and AP led with the Sabres' comeback and Power's
            # overtime winner, not Marchenko's three; Sora, 2026-10-16). Any result headline above beats it.
            heads.append((70, pk([f"{last(pid)}’s {'natural ' if storylines.natural_hat(goals) == pid else ''}hat trick isn’t enough",
                                  f"{last(pid)} gets a hat trick, but {W.the(loser)} lose" if len(gs) == 3 else
                                  f"{last(pid)} scores {num(len(gs))}, but {W.the(loser)} lose"], "hatl")))
        elif len(gs) >= 4:
            heads.append((100, pk([f"{last(pid)} scores {num(len(gs))}", f"{last(pid)} goes off for {num(len(gs))}"], "4")))
        elif {"ev", "pp", "sh"} <= kinds:
            heads.append((100, f"{last(pid)}’s hat trick, one at every strength"))
        elif storylines.natural_hat(goals) == pid:  # three in a row, nobody else scoring (Sora's notes, 2026-10-14)
            heads.append((96, f"A natural hat trick for {last(pid)}"))
        else:
            heads.append((95, pk([f"A hat trick for {last(pid)}{hats}", f"{last(pid)} bags a hat trick", f"{last(pid)} lights the lamp for a hat trick"], "hat")))
    wg = gl.get(winner)
    if ls == 0 and wg:
        G_ = last(wg["id"]) or wg["name"].split(" ", 1)[-1]
        heads.append((92, f"{G_} stops all {wg['saves']} to blank {L_}" if (wg.get("saves") or 0) >= 35 else
                      pk([f"{G_} shuts out {L_}", f"{G_} slams the door on {L_}", f"{G_} blanks {L_}", f"Nothing gets past {G_}"], "so")))
    elif wg and (wg.get("gsax") or 0) >= 2.5:  # a goalie who carried the win: 2.5+ goals saved above expected
        G_ = last(wg["id"]) or wg["name"].split(" ", 1)[-1]
        heads.append((89, pk([f"{G_} stands on his head", f"{G_} stands on his head against {L_}"], "soh")))
    if rally:
        Ln_ = W.nick.get(loser, loser)
        heads.append((86, pk([f"{W_} hold off a {Ln_} charge", f"{W_} survive a {Ln_} rally", f"{W_} hang on against {L_}", f"{W_} survive a scare"], "rally")))
    if share is not None and share <= 0.35 and ws - ls <= 2:
        thief = wg["name"].split(" ", 1)[-1] if wg and (wg.get("gsax") or 0) >= 1 else None
        where = f" {W.at(home)}" if winner == away else ""
        if set_up_wins(goals, winner == home):  # their goals were well-made plays: no "robbery" or "got away with one"
            heads.append((90, pk([f"{thief} steals one{where}", f"{thief} robs {L_}{where}"], "steal") if thief else
                          pk([f"{W_} make their chances count{where}", f"{W_} cash in{where}"], "cash")))
        else:
            heads.append((90, f"Highway robbery{where}" if share <= 0.25 else
                          pk([f"{thief} steals one{where}", f"{thief} robs {L_}{where}"], "steal") if thief else
                          pk([f"{W_} steal one{where}", f"{W_} get away with one{where}"], "steal")))
    nums_ = r.get("numbers") or {}
    slow = slow_start(r.get("periods") or [], winner, loser, W)
    fw = next((x for x in found if x["kind"] == "first_win" and x["team"] == winner), None)
    if fw and N.get(fw["pid"]):  # a goalie's first NHL win (DiPietro's, eight seasons after his debut, 2026-10-16)
        G_ = last(fw["pid"])
        wait = (fw.get("seasons") or 0) >= FINALLY_SEASONS or (fw.get("start_n") or 0) >= FINALLY_STARTS
        wpp = (nums_.get(winner) or {}).get("pp_goals") or 0
        pool = ([f"{_cap(num(wpp))} power-play goals get {G_} his first NHL win"] if wpp >= PP_STORY else
                [f"{G_} finally gets one", f"{G_} finally gets his first NHL win"] if wait else
                [f"{G_} gets his first NHL win", f"A first NHL win for {G_}"])
        heads.append((89 if wait or wpp >= PP_STORY else 84, pk(pool, "fw")))
    if comeback:
        # the player who lit it carries the headline, as writers do ("Lehkonen's spark", 2026-10-16), over the team
        d = num(lead_max[loser])
        sp = spark(goals, side, winner, loser)
        if sp and N.get(sp[0]):
            who_, n_, nk = last(sp[0]), sp[1], W.nick.get(winner, winner)
            pool = ([f"{who_}’s {num(n_)} goals spark {W.the(winner)}", f"{who_} scores {num(n_)} as {W.the(winner)} rally from {d} down"] if n_ >= 2 else
                    [f"{who_}’s first NHL goal sparks a {nk} comeback"] if any(x["kind"] == "first_goal" and x["pid"] == sp[0] for x in found) else
                    [f"{who_} sparks a {nk} comeback", f"{who_} sparks {W.the(winner)} back from {d} down"])
        elif lead_max[loser] >= 3:
            pool = [f"{W_} storm back from {d} down", f"{W_} erase a {d}-goal hole", f"{W_} come back from {d} down"]
        else:
            pool = [f"{W_} come back from {d} down", f"{W_} rally from {d} down", f"{W_} erase a {d}-goal deficit"]
            pool += [f"{W_} shake off a slow start"] if slow else []
        heads.append((88, pk(pool, "cb")))
    elif slow:  # trailing after the first and outshot 2 to 1 in it, then the better team
        heads.append((84, pk([f"{W_} shake off a slow start", f"{W_} shake off a slow start against {L_}"], "slow")))
    if end == "REG" and ws - ls >= 2 and late_rush(goals, side, winner):
        heads.append((74, pk([f"{W_} pour it on late", f"{W_} pour it on late against {L_}"], "rush")))
    if end in ("OT", "SO"):
        ot = next((g for g in goals if g["period"] > 3), None)
        if ot and end == "OT":
            heads.append((80, pk([f"{last(ot['p1'])} wins it in overtime", f"{last(ot['p1'])} ends it in overtime",
                                  f"{last(ot['p1'])} plays hero in overtime"], "ot")))
        else:
            heads.append((80, pk([f"{last(so_pid)} wins it in a shootout", f"{last(so_pid)} seals it in the shootout"], "so2")
                          if so_pid else f"{W_} win it in a shootout"))
    heads += _clutch_heads(goals, side, winner, end, W, W_, L_, last, N, pk)
    heads += _night_heads(night, winner, W, L_, last, N, found)
    if ws - ls >= 6:
        heads.append((76, pk([f"{W_} blow out {L_}", f"{W_} bury {L_}", f"{W_} hang {ws} on {L_}", f"{W_} turn it into a laugher"], "rout")))
    elif ws - ls >= 4:
        heads.append((75, pk([f"{W_} roll past {L_}", f"{W_} pile it on {L_}", f"{W_} run away from {L_}", f"{W_} cruise past {L_}"], "rout")))
    # "get past" and "edge" say it was close, "handle" that it wasn't (Sora's review, 2026-10-19: "The Hurricanes get past
    # the Blackhawks" for a 5-2 win, "The Blackhawks handle the Blues" when St. Louis had more of the play)
    en_last = bool(goals) and goals[-1]["strength"] == "en" and side(goals[-1]) == winner
    real = ws - ls - (1 if en_last else 0)  # the margin before an empty-net goal
    if real <= 1:
        base = [f"{W_} beat {L_}", f"{W_} get past {L_}", f"{W_} edge {L_}", f"{W_} squeak past {L_}"]
    elif real == 2:
        base = [f"{W_} beat {L_}", f"{W_} take down {L_}"]
    else:
        base = [f"{W_} beat {L_}", f"{W_} take down {L_}"] + ([f"{W_} handle {L_}"] if share is None or share >= 0.5 else [])
    heads.append((10, pk(base, "base")))
    star = _star_line(night, winner, last, N)
    if star:  # "The Flames beat the Red Wings" was the stiffest headline of the night when Farabee had three points (Oct 24)
        # the verb keeps what the plain headline said about the margin ("edge" for one goal, "handle" for a romp)
        verbs = ["beat", "edge", "get past"] if real <= 1 else ["beat", "take down"] if real == 2 else \
            ["beat", "take down"] + (["handle"] if share is None or share >= 0.5 else [])
        heads.append((30, f"{star} as {W.the(winner)} {pk(verbs, 'base2')} {L_}"))
    from pipeline.export import goalie_streaks
    heads += goalie_streaks.story(gid, season, N, lambda pid: N.get(pid, ""), W, winner, loser, pk)["heads"]
    head = max(heads, key=lambda h: h[0])[1]
    # paragraph one: the game in the order it happened (export/timeline.py, 2026-10-17). A fan leads with the big fact
    # (a hat trick, a debut, a return), then tells the goals from the first to the last, each by what it did to the
    # score, with firsts and milestones riding on the goal they came on. Nothing is told twice.
    s1 = []
    gone = _overturned(gid, season)
    from pipeline.export import timeline
    told_first = set()  # players whose first goal (NHL, for a club, of the season) the story already told
    big_told: set[int] = set()
    hat_ids = {p_ for p_, _ in hat}
    hat_lines: list = []  # told after the goals (Sora's review, 2026-10-19: the chronology already says "completed his hat trick")
    for pid, gs in sorted(hat, key=lambda x: goals.index(x[1][0])):
        # The first mention of three goals always says hat trick (or natural hat trick); counts come after (Sora,
        # 2026-10-16: "Kirill Marchenko scored three"). Every hat trick of the night gets its line (two for Florida).
        kinds = [{"ev": "at even strength", "pp": "on the power play", "sh": "shorthanded", "en": "into an empty net",
                  "ps": "on a penalty shot", "dp": "on a delayed penalty"}[g["strength"]] for g in gs]
        natural = pid in {goals[i]["p1"] for i in range(len(goals) - 2) if goals[i]["p1"] == goals[i + 1]["p1"] == goals[i + 2]["p1"]}
        n_ = len(gs)

        def _hat(pid=pid, gs=gs, kinds=kinds, natural=natural, n_=n_) -> list[str]:
            who = grammar.possessive(name(pid))
            out_ = []
            surname = last(pid)
            said_hat = any(surname in x and "hat trick" in x for x in s1)
            said_nat = any(surname in x and "natural hat trick" in x for x in s1)
            if len(set(kinds)) == len(kinds):
                what = "hat trick" if n_ == 3 else f"{num(n_)} goals"
                out_.append(f"{who} {'goals' if said_hat else what} came {_and(['one ' + k for k in kinds])}.")
            elif said_nat:
                pass  # the chronology said "completed a natural hat trick"
            elif natural and n_ == 3 and len({g["period"] for g in gs}) == 1 and gs[0]["period"] <= 3:
                out_.append(f"It was a natural hat trick, three straight goals in the {PERIODS[gs[0]['period']]} period.")
            elif natural and n_ == 3:
                out_.append("It was a natural hat trick, three goals in a row with nobody else scoring in between.")
            elif natural:
                out_.append(f"{who} {num(n_)} goals included a natural hat trick.")
            elif not said_hat:  # the third goal was told inside a run of goals, so "hat trick" wasn't said yet
                out_.append(f"{_cap(name(pid))} finished with a hat trick." if n_ == 3 else f"{_cap(name(pid))} finished with {num(n_)} goals.")
            run_ = _hat_run(P, pid, gid)
            if run_ >= 2:  # Brady Tkachuk's second straight natural hat trick (NHL.com and AP both led with it, 2026-10-15)
                out_.append(f"It was his {ORDINALS.get(run_, str(run_) + 'th')} straight hat trick.")
            helper = storylines.set_up_all(goals, pid)
            if helper and N.get(helper):  # one teammate on every goal; a brother is the story (the Tkachuks, 2026-10-13)
                kin = "His brother " if helper in storylines.siblings().get(pid, set()) else ""
                ha = _assists(night, helper)
                extra = f", and had {num(ha)} assists in all" if ha > len(gs) else ""  # Matthew Tkachuk's five (2026-10-13)
                out_.append(f"{kin}{name(helper)} assisted on all {num(len(gs))}{extra}.")
                big_told.add(helper)
            return out_
        hat_lines.append(_hat)
    for pid in _gordie_howe(P, gid, season):  # a goal, an assist and a fight (Ruff joked about Quinn's, 2026-10-15)
        if N.get(pid) and hat and pid not in hat_ids:
            # "Two kinds of hat tricks in Toronto: the Maple Leafs' Kirill Marchenko scored three goals and the Sabres'
            # Jack Quinn got the Gordie Howe." (Sora's review, round 2)
            h_ = hat[0][0]
            ht, gt = side(hat[0][1][0]), next((t for t in (away, home) if P.filter((pl.col("game_id") == gid) & (pl.col("player_id") == pid) & (pl.col("team") == t)).height), None)
            s1.append(f"Two kinds of hat tricks {W.at(home) if hasattr(W, 'at') else ''}: {W.poss(ht)} {name(h_)} scored three goals, and "
                      f"{W.poss(gt) if gt else ''} {name(pid)} got the Gordie Howe, a goal, an assist and a fight.".replace("  ", " "))
        elif N.get(pid):
            s1.append(f"{name(pid)} had a Gordie Howe hat trick: a goal, an assist and a fight.")
    for x in rare[:1]:  # a rare scorer's two goals open the story (Sora, 2026-10-21)
        # not when they were his first NHL goals: the goals themselves say so ("Dmitri Simashev, who scores in about
        # 1 game in 40, scored twice" opened UTA's comeback over Tampa Bay, Oct 24)
        if x["pid"] not in hat_ids and not any(f["kind"] == "first_goal" and f["pid"] == x["pid"] for f in found):
            how = f"scores in about 1 game in {round(1 / x['odds'])}"
            s1.append(f"{_cap(name(x['pid']))}, who {how}, scored {'twice' if x['goals'] == 2 else num(x['goals']) + ' times'}.")
    # debuts, returns and games played lead too; goals and points attach to the goal they came on
    net_firsts: list[str] = []  # a goalie's first win or shutout, told with the goaltending
    saves_ = {x["id"]: x.get("saves") for x in r.get("goalies", [])}
    fights = penalties.fight_events(gid, season, name)
    fighters = {p_ for _, ps_, _ in fights for p_ in ps_}
    later_firsts = [x for x in found if _later_first(x, night, fighters)]
    # A debut, club debut or return whose only mark is an assist waits until after the goals, beside the big nights
    # (Sora, 2026-10-21: Välimäki's first game for Carolina after 600+ days is news, but not the game's lead)
    after_goals = [x for x in found if x not in later_firsts and x["kind"] in ("debut", "club_debut", "return")
                   and x.get("points") and not x.get("goals") and not x.get("first_round")]
    s1 += _first_lines(gid, season, G, P, N, W, name, said, winner, loser, told=told_first,
                       found=[x for x in found if x not in later_firsts and x not in after_goals
                              # a games-played milestone of a goal scorer rides on his goal ("in his 700th NHL game")
                              and not (x["kind"] == "milestone" and x.get("what") == "game" and any(g["p1"] == x["pid"] for g in goals))
                              and not (timeline.rides_on_goal(x) and any(g["p1"] == x["pid"] for g in goals))],
                       kinds=("debut", "return", "club_debut", "first_point", "milestone", "first_win", "first_shutout", "first_start", "win_since"),
                       goals_told=True, saves=saves_, in_net=net_firsts)
    # goalie changes: "started" is whoever was in net at puck drop; both teams can change goalies. A change reads as
    # "pulled" only after 3+ goals, or 2+ at an intermission (NHL.com's own bar, 2026-10-17). A change in the middle
    # of play is more often an injury (Sora, 2026-10-12: an injured goalie comes out at once), so it is told without
    # "pulled", and never with a reason.
    in_net = r.get("goalies", [])
    changes = []
    for t in (away, home):
        first_g = next((x for x in in_net if x["team"] == t and x.get("started")), None)
        relief = max((x for x in in_net if x["team"] == t and not x.get("started") and _secs(x.get("toi")) > 0),
                     key=lambda x: _secs(x.get("toi")), default=None)
        if first_g and relief:
            came = relief.get("came_in") or {}
            ga = first_g.get("ga", 0)
            pulled = not came.get("back") and came.get("how") != "mid" and (ga >= 3 or (ga >= 2 and came.get("how") == "intermission"))
            changes.append((t, first_g, relief, pulled))
    exits = [x for x in (r.get("left") or []) if x.get("regular", True) and x["id"] in N][:2]
    season_goals = {pid: int(g_) for pid, g_ in P.filter((pl.col("season") == season) & (pl.col("date") < r["date"]))
                    .group_by("player_id").agg(pl.col("g").sum()).iter_rows()}
    gm = timeline.Game(goals=goals, winner=winner, loser=loser, home=home, away=away, end=end, W=W, name=name, last=last, said=said,
                       N=N, pk=pk, wiped=gone, reviews=storylines.reviews(gid, season), ps_stopped=[x for x in _ps_stopped(gid, season) if x["p1"] in N],
                       changes=changes, exits=exits, so=(so_pid, so_how, so_all), rally=rally, hat_ids=hat_ids,
                       fights=[(sec, _star_fight(say, ps_, gid, season, r["date"], N, name)) for sec, ps_, say in fights],
                       droughts=shot_droughts(gid, season, home, away))
    gm.attach, attached = timeline.attachments(gm, found, season_goals)
    # a goal streak rides on his goal ("..., his fifth straight game with a goal") instead of a Streak item below
    # (Sora's review, round 2: Jankowski's streak read as a separate fact after the goal was told)
    streak_inline: set[int] = set()
    old_told = None
    for x in olds[:1]:  # "Marchenko ... scored, in his first game back in Columbus"
        j = next((i for i, g in enumerate(goals) if g["p1"] == x["pid"]), None)
        if j is not None and not (gm.attach.get(j) or {}).get("tail"):
            gm.attach.setdefault(j, {})["tail"] = (f"in his first game back {W.at(x['old'])}" if x["at_old"] else
                                                   f"in his first game against {W.place(x['old'])} since leaving")
            old_told = x["pid"]
    for pid in dict.fromkeys(g["p1"] for g in goals if g["strength"] != "en"):
        if pid in hat_ids or pid not in N:
            continue
        mine_ = P.filter((pl.col("player_id") == pid) & (pl.col("season") == season) & (pl.col("date") <= r["date"]))
        team_ = mine_.filter(pl.col("game_id") == gid)["team"].to_list()
        gn = run_of(mine_.filter(pl.col("team") == team_[0]).sort("date")["g"].to_list()) if team_ else 0
        j = max(i for i, g in enumerate(goals) if g["p1"] == pid)
        if gn >= INLINE_GOAL_RUN and not (gm.attach.get(j) or {}).get("tail"):
            gm.attach.setdefault(j, {})["tail"] = f"his {grammar.ordinal_word(gn)} straight game with a goal"
            streak_inline.add(pid)
    told_first |= attached
    if not goals and end == "SO":
        s1.append("No goal counted through overtime." if gone else "Nobody scored through overtime.")
    s1 += timeline.paragraph(gm)
    # a first goal the chronology could not place (no goal of his on the sheet, say) is told the old way
    s1 += _first_lines(gid, season, G, P, N, W, name, said, winner, loser, told=told_first, found=found,
                       kinds=("first_goal", "club_goal"), skip=attached, goals=goals)
    # how the goals taken off played out, looking back (a one-goal loss, as many goals as came off, won anyway)
    for t in (away, home):
        mine = [x for x in gone if (home if x["home"] else away) == t]
        if mine and t != winner:  # the winners' goals taken off are answered in the chronology ("but it didn’t cost them")
            after = _wipe_after(mine, goals, t == home, t == winner, ws - ls, end, lambda pool: _pick(pool, gid, "wipe", t))
            if after and "got it back" not in after and "gave it back" not in after and "gave them back" not in after and "didn’t wait" not in after:
                s1.append(after.strip())
    if end == "SO":
        line = timeline.shootout_line(gm)
        if s1 and s1[-1].startswith(("Those disallowed goals stung", "That disallowed goal stung")):
            # one sentence: "Those disallowed goals stung: the game went to a shootout, and the Stars won it when Roope
            # Hintz scored in the fourth round." (Sora's review, round 2)
            s1[-1] = s1[-1][:-1] + timeline.shootout_tail(gm)
        else:
            s1.append(line)
    for f_ in hat_lines:
        s1 += f_()
    if after_goals:
        s1 += _first_lines(gid, season, G, P, N, W, name, said, winner, loser, told=told_first, found=after_goals,
                           kinds=("debut", "return", "club_debut"), goals_told=True, saves=saves_, in_net=net_firsts)
    for b in _big_nights(night, winner, loser, W, name, said, last, big_told | hat_ids, verb="finished with"):
        # "McDavid scored again ... McDavid added three assists" -> "He added three assists" (voice loop, round two)
        who = b.split(" ", 1)[0]
        if s1 and s1[-1].startswith(who + " ") and b.startswith(who + " added ") and " and " not in b:
            b = "He" + b[len(who):]
        s1.append(b)
    # A hot opening line for the nights fans talk about the next morning, each earned by the goals themselves
    # (Sora, 2026-10-14): a three-goal comeback, a tie with the goalie pulled that went on to win, a six-goal rout.
    s1[0:0] = [x for x in run_lead if not x.startswith(_cap(W.the(loser)))]
    pulled_tie = any(g["own_extra"] and g["hs"] == g["as"] and g["period"] == 3 and side(g) == winner for g in goals)
    if comeback and lead_max[loser] >= 3:
        s1.insert(0, pk([f"Never count {W.the(winner)} out.", "What a comeback."], "hot"))
    # no bare "What a finish." for a goalie-pulled tie: the tying goal now says "with the goalie pulled" itself (outside
    # notes, Oct 22: the line opened with nothing)
    elif rally and rally["peak"][0] - rally["peak"][1] >= 4:
        s1.insert(0, pk([f"{_cap(W.the(loser))} almost pulled off something wild.", f"This one got way too interesting for {W.the(winner)}."], "hot"))
    elif ws - ls >= 6:
        s1.insert(0, f"An ugly night for {W.the(loser)}.")  # no guessing at feelings ("will want to forget this one", Oct 22)
    # paragraph two: was it fair, and who stood out
    s2 = []
    if share is not None:
        def tenths(p):
            return {10: "every time", 9: "nine in ten", 8: "about four in five", 7: "about seven in ten", 6: "about six in ten",
                    5: "about half the time", 4: "about four in ten", 3: "about three in ten", 2: "about one in five",
                    1: "about one in ten", 0: "almost never"}[round(p * 10)]
        nums = r.get("numbers") or {}
        sw, sl = (nums.get(winner) or {}).get("shots"), (nums.get(loser) or {}).get("shots")
        outshot = f", outshooting {W.the(loser)} {sw}-{sl}" if sw and sl and sw - sl >= 5 else ""
        even = _even_shots(gid, season, home, away) if sw is not None and sl is not None else {}
        e_w, e_l = even.get(winner, 0), even.get(loser, 0)
        if (e_w - e_l >= EVEN_GAP and sl - e_l >= EVEN_OTHER and sw + sl
                and e_w / (e_w + e_l) >= sw / (sw + sl) + EVEN_SHARE):
            # the loser's shots came on the power play (Sora, 2026-10-29: Boston at Carolina was 30-15, and 25-9 at 5-on-5)
            outshot = f"{outshot} ({e_w}-{e_l} at 5-on-5)" if outshot else f", outshooting {W.the(loser)} {e_w}-{e_l} at 5-on-5"
        # The chances line must not contradict the shot count beside it (VAN at FLA, 2026-10-15: "nothing much separated
        # these two" when Florida outshot them 39-17; CGY at VGK: "the Golden Knights drove the play" when Calgary had
        # more of the shots). `outshot_by` is set when the loser had 5+ more shots, `lopsided` when either side had
        # 10+ more and 40% more.
        outshot_by = sw is not None and sl is not None and sl - sw >= 5
        lopsided = bool(sw and sl) and abs(sw - sl) >= 10 and max(sw, sl) >= 1.4 * min(sw, sl)
        more, fewer = (winner, loser) if (sw or 0) > (sl or 0) else (loser, winner)
        hi, lo = max(sw or 0, sl or 0), min(sw or 0, sl or 0)
        extra = {"OT": "overtime", "SO": "a shootout"}.get(end)
        # Bands on the calibrated replay share (eye test, 2026-10-13: 60-70% used to read "close to even", so Columbus
        # outshooting Pittsburgh 45-22 was "could have gone either way"). 45-55% is close, as in the win-chance reveal.
        # Hotter lines need hotter numbers (Sora, 2026-10-14): "dominated" only from 75% of the replays, "got away
        # with one" only when the loser won 70%+ of them.
        Wn, Ln = W.the(winner), W.the(loser)
        if share >= 0.6 and extra:
            s2.append(pk([f"{_cap(Wn)} carried the play{outshot}, but needed {extra} to win it.",
                          f"{_cap(Wn)} owned the chances{outshot}, and still had to sweat it out in {extra}."], "fair-x"))
            if share >= 0.7:
                s2.append(f"Replay this game’s chances a hundred times and {Wn} win in regulation {tenths(share)}.")
        elif share >= 0.75:
            s2.append(pk([f"No fluke here. Replay this game’s chances a hundred times and {Wn} win {tenths(share)}.",
                          f"{_cap(Wn)} dominated, and the replays agree: run this game’s chances back a hundred times and {Wn} win {tenths(share)}.",
                          f"The score was fair. Replay this game’s chances a hundred times and {Wn} win {tenths(share)}."], "fair"))
        elif share >= 0.7:
            s2.append(pk([f"The score was fair. Replay this game’s chances a hundred times and {Wn} win {tenths(share)}.",
                          f"{_cap(Wn)} earned this one. Replay the chances a hundred times and they win {tenths(share)}."], "fair"))
        elif share >= 0.55 and outshot_by:
            s2.append(pk([f"{_cap(Ln)} had more of the shots, {sl}-{sw}, but {Wn} had the better ones.",
                          f"{_cap(Ln)} outshot {Wn} {sl}-{sw}, but {W.poss(winner)} chances were the more dangerous ones."], "fair-shots"))
        elif share >= 0.6:
            s2.append(pk([f"{_cap(Wn)} carried the play{outshot}, and got the result to match.",
                          f"{_cap(Wn)} drove the play{outshot}, and the scoreboard caught up."], "fair6"))
        elif share >= 0.55:
            s2.append(pk([f"{_cap(Wn)} had the better of the chances{outshot}.", f"{_cap(Wn)} had a bit more of the good stuff{outshot}."], "fair55"))
        elif share <= 0.4:
            g = gl.get(winner)
            if sw and sl and sw - sl >= 5:  # the winner outshot them: say whose chances were better, not "the play"
                lead = f"{_cap(Wn)} outshot {Ln} {sw}-{sl}, but {W.poss(loser)} chances were the better ones."
            elif ws - ls >= 3:
                lead = f"The score flatters {Wn}: {Ln} had the better of the chances."
            elif share <= 0.3 and set_up_wins(goals, winner == home):
                lead = f"{_cap(Ln)} had the better of the chances, but {Wn} made theirs count."
            elif share <= 0.3:
                lead = pk([f"{_cap(Wn)} got away with one. {_cap(Ln)} had the better of the chances.",
                           f"{_cap(Ln)} will be kicking themselves: they had the better of the chances."], "steal")
            else:
                lead = f"{_cap(Ln)} had the better of the chances."
            again_ = "those chances" if lead.endswith("chances.") else "the chances"
            s2.append(f"{lead} Replay {again_} a hundred times and {Ln} win {tenths(1 - share)}.")
            if g and (g.get("gsax") or 0) >= 1:
                gn = name(g['id']) if g.get('id') in N else g['name']
                s2.append(pk([f"{gn} made the difference in net, stopping {g['saves']} of {g['shots']}.",
                              f"{gn} was the difference, stopping {g['saves']} of {g['shots']}."], "thief"))
        elif lopsided:  # close on the chances, far apart on the shots
            s2.append(pk([f"{_cap(W.the(more))} outshot {W.the(fewer)} {hi}-{lo}, but {W.poss(fewer)} looks were better, so the chances came out about even.",
                          f"{_cap(W.the(fewer))} were outshot {hi}-{lo}, but their looks were better, and the chances came out about even."], "even-shots"))
        elif share <= 0.45:
            s2.append(pk([f"{_cap(Ln)} had a little more of the play, but {Wn} made their chances count.",
                          f"{_cap(Ln)} had a little more of the play, but {Wn} had the finish."], "fair45"))
        elif ws - ls >= 3:
            s2.append(pk(["The chances were closer than the score.", "Don’t let the score fool you: the chances were a lot closer than that."], "close3"))
        else:
            s2.append(pk(["The chances were close to even, so this one could have gone either way.",
                          "Nothing much separated these two on the chances. This one could have gone either way."], "even"))
    if slow:  # the plain shot counts behind "shake off a slow start"
        s2.append(slow)
    # posts and crossbars belong with who had the better of the play, not at the head of the penalty paragraph (voice loop)
    iron = posts_line(gid, season, {home_id: home, row["away_id"][0]: away}, winner, loser, ws - ls, W)
    hit = re.match(r".+? hit (.+?)(?: and still lost| and came up a goal short|, and the bounces never came)\.$", iron[0]) if iron else None
    if hit and s2 and s2[-1].startswith(f"{_cap(Ln)} had ") and ", but " in s2[-1]:
        # "The Sharks had a little more of the play and hit a post and the crossbar, but the Red Wings had the finish."
        s2[-1] = s2[-1].replace(", but ", f" and hit {hit.group(1)}, but ", 1)
        iron = []
    s2 += iron
    s2 += net_firsts
    s2 += _goalie_lines(r, winner, loser, W, name, said, head, pk, season)
    # goalie streaks (Sora, 2026-10-30: Kochetkov's nine straight wins to open the season went untold)
    runs_ = goalie_streaks.story(gid, season, N, name, W, winner, loser, pk)
    s2 += runs_["sents"]
    swing = _swing(gid)
    if swing:
        top = swing[0]
        if top["id"] not in said and top.get("pos") != "G":
            pts = sum(top["id"] in (g["p1"], g["p2"], g["p3"]) for g in goals)
            # one "even" for everything that makes it unlikely ("even in a loss, even with Hagel's hat trick" read badly)
            parts = ["in a loss"] if top.get("team") == loser else ["without a point"] if not pts else []
            tn = name(top["id"])
            # The night's headline player ranked lower: say so rather than leave the sentence beside a hat trick or an
            # overtime winner unexplained (Sora, 2026-10-16: Ekblad over Tkachuk's natural hat trick).
            star = max(hat, key=lambda x: len(x[1]))[0] if hat else next((g["p1"] for g in goals if g["period"] > 3), None) if end == "OT" else None
            hats = [h[0] for h in hat if h[0] != top["id"] and N.get(h[0])]
            if len(hat) >= 2 and len(hats) >= 2:  # "with Bennett’s and Verhaeghe’s hat tricks in the game"
                parts.append(f"with {_and([name(h) + '’s' for h in hats])} hat tricks in the game")
            elif star and star != top["id"] and N.get(star):
                parts.append(f"with {name(star)}’s {'hat trick' if hat else 'overtime winner'} in the game")
            tail = f", even {' and '.join(parts)}" if parts else ""
            s2.append(pk([f"By our Swing measure, {tn} had the best night of anyone{tail}.",
                          f"Nobody on the ice had a bigger night than {tn}{tail}, by our Swing measure."], "swing"))
    for sp in r.get("surprises", []):
        if sp.get("kind") == "lineup" and "different line" in sp.get("head", ""):
            body = sp.get("body", "")
            team = next((t for t in (away, home) if W.place(t) in sp["head"]), None)
            dressed = P.filter((pl.col("game_id") == gid) & (pl.col("team") == (team or "")))["player_id"].to_list()
            trio = []
            for short in [x for part in body.split(" played ")[0].split(", ") for x in part.split(" and ")]:
                first, _, lastname = short.partition(". ")
                pid = next((p for p in dressed if N.get(p, "").endswith(" " + lastname) and N[p].startswith(first)), None)
                trio.append(name(pid) if pid else short)
            line_goals = sum(1 for g in goals if side(g) == team and {g["p1"], g["p2"], g["p3"]} & set(
                p for p in dressed if any(N.get(p, "").endswith(" " + x.split(" ")[-1]) for x in trio)))
            if team:
                tail = f", and it had a hand in {'both' if line_goals == 2 else num(line_goals)} of their goals" if line_goals >= 2 else ""
                s2.append(f"{_cap(W.the(team))} tried a line they hadn’t used before: {_and(trio)}{tail}.")
            break
    # paragraph three: the penalty sheet, only when something stood out (a fight, a brawl, an ejection, a strange count)
    st_first, st_last = special_teams(nums_, goals, side, winner, loser, W, name, comeback)
    s3 = penalties.recap_notes(gid, season, {home_id: home, row["away_id"][0]: away}, W, name, fights_told=True)
    s3 = st_first + s3 + st_last + two_man_kills(gid, season, home_id, home, away, goals, W)
    # what it means
    items = []
    items += sh_pair(gid, season, r["date"], goals, home, away, W)
    items += pp_drought(gid, season, r["date"], home, away, W)
    for t in (winner, loser):
        res = results(G.filter(pl.col("date") <= r["date"]), t, season)
        k, n = streak(res)
        prev_k, prev_n = streak(res[:-1])
        if t == winner and k == "won" and n >= 3:
            items.append({"tag": "Streak", "text": f"{_cap(W.the(t))} are rolling: {num(n)} straight wins." if n >= 5 else
                          f"{_cap(num(n))} straight wins for {W.the(t)}.", "score": 60 + n})
        if t == loser and prev_k == "won" and prev_n >= 3:
            items.append({"tag": "Snapped", "text": f"{_cap(W.poss(t))} {num(prev_n)}-game winning streak is over." if prev_n < 5 else
                          f"{_cap(W.poss(t))} {num(prev_n)}-game heater is over.", "score": 62 + prev_n})
        if t == loser and k == "winless" and n >= 3 and not (len(res) >= 4 and n == len(res)):  # else told as the start
            items.append({"tag": "Slump", "text": slump(t, res, n, W), "score": 55 + n})
        if t == winner and prev_k == "winless" and prev_n == 2 and len(res) > 3:  # NHL.com's "had lost two straight"
            items.append({"tag": "Snapped", "text": f"{_cap(W.the(t))} bounced back after two straight losses.", "score": 42})
        if t == winner and prev_k == "winless" and prev_n >= 3:
            first_win = prev_n == len(res) - 1
            items.append({"tag": "Snapped", "text": f"{_cap(W.the(t))} won for the first time this season, in their {ORDINAL_N(len(res))} game."
                          if first_win else f"{_cap(W.the(t))} won for the first time in {num(prev_n + 1)} games.", "score": 58 + prev_n})
        # how the season has started (eye test, 2026-10-13: wire recaps lead with these; ours never did)
        regl = [x for x in res if not x["win"] and x["end"] == "REG"]
        if len(res) >= 4 and not regl and not (k == "won" and n == len(res)):
            items.append({"tag": "Start", "text": f"{_cap(W.the(t))} are {record(res)}, still without a regulation loss.", "score": 56})
        elif len(res) >= 4 and k == "won" and n == len(res):
            items.append({"tag": "Start", "text": f"{_cap(W.the(t))} are {record(res)}, perfect so far.", "score": 66 + n})
        elif len(res) >= 4 and not any(x["win"] for x in res):
            items.append({"tag": "Start", "text": f"{_cap(W.the(t))} are {record(res)}, still looking for their first win.", "score": 64 + len(res)})
    for t in (winner, loser):
        items += _team_form(G, r, t, t == winner, W, season)
    items += _break_item(G, r, (away, home), W)
    items += _first_items(later_firsts, night, N, W, loser)
    items += _new_coach(gid, (winner, loser), r["date"], W, season, {winner: True, loser: False})
    items += _race_after(gid, r["date"], (winner, loser), W, season)
    items += _vs_projection(gid, r, P.filter(pl.col("game_id") == gid), W, N, season)
    home_games = [x for x in results(G.filter(pl.col("date") <= r["date"]), home, season) if x["home"]]
    if len(home_games) == 1 and home_games[0]["game_id"] == gid:
        items.append({"tag": "Home opener", "text": f"{_cap(W.the(home))} {'won' if winner == home else 'lost'} their home opener.", "score": 57})
    meet = [x for x in results(G.filter(pl.col("date") <= r["date"]), winner, season) if x["opp"] == loser]
    if len(meet) >= 2:
        w_ = sum(x["win"] for x in meet)
        l_ = len(meet) - w_
        text = (f"{_cap(W.the(winner))} have won all {num(len(meet))} meetings this season." if not l_ and len(meet) > 2 else
                f"{_cap(W.the(winner))} have won both meetings this season." if not l_ else
                f"The season series is even at {w_}-{l_}." if w_ == l_ else
                f"{_cap(W.the(winner))} lead the season series {w_}-{l_}." if w_ > l_ else
                f"{_cap(W.the(loser))} still lead the season series {l_}-{w_}.")
        items.append({"tag": "Season series", "text": text, "score": 35})
    for x in olds[:1]:
        if x["pid"] != old_told and N.get(x["pid"]):
            got = (f", and picked up {'an assist' if x['pts'] == 1 else num(x['pts']) + ' points'}" if x["pts"] else "")
            where_ = f"back {W.at(x['old'])}" if x["at_old"] else f"against {W.place(x['old'])} since leaving"
            items.append({"tag": "Old team", "text": f"{N[x['pid']]} played his first game {where_}{got}.", "score": 60})
    if len(meet) == 1:  # the first meeting since last spring's series
        sr = storylines.playoff_series(away, home, season)
        if sr:
            items.append({"tag": "Rematch", "text": f"{_cap(storylines.series_words(sr, W))}.", "score": 47})
    pg = P.filter((pl.col("season") == season) & (pl.col("date") <= r["date"]))
    tonight = pg.filter(pl.col("game_id") == gid)
    for a_, b_ in _brothers(tonight, away, home):
        if winner in (a_[1], b_[1]):
            w_, l_ = (a_, b_) if a_[1] == winner else (b_, a_)
            items.append({"tag": "Brothers", "text": f"{N[w_[0]]} got the better of his brother {N[l_[0]].split(' ', 1)[0]}.", "score": 58})
    for pr in tonight.filter(pl.col("pts") > 0).iter_rows(named=True):
        mine_ = pg.filter((pl.col("player_id") == pr["player_id"]) & (pl.col("team") == pr["team"])).sort("date")
        hist = mine_["pts"].to_list()
        n, gn = run_of(hist), run_of(mine_["g"].to_list())
        if (n >= 5 or gn >= GOAL_RUN) and not (pr["player_id"] in streak_inline and n <= gn):
            items.append({"tag": "Streak", "text": f"{N[pr['player_id']]} has {streak_text(gn, n, len(hist))}.", "score": 50 + n + gn / 2})
        if pr["g"] > 0 and pr["player_id"] not in told_first and pg.filter((pl.col("player_id") == pr["player_id"]) & (pl.col("game_id") != gid))["g"].sum() > 0:
            ended = _drought_over(P, pr["player_id"], gid, r["date"], season)
            if ended:
                items.append({"tag": "Drought over", "text": f"{N[pr['player_id']]} scored for the first time in {num(ended)} games.", "score": 46})
        if (pr["g"] > 0 and len(hist) >= 5 and pr["player_id"] not in told_first
                and pg.filter((pl.col("player_id") == pr["player_id"]) & (pl.col("game_id") != gid))["g"].sum() == 0):
            first = "first goal" if pr["g"] == 1 else f"first {num(int(pr['g']))} goals"  # Crosby scored two (2026-10-13)
            items.append({"tag": "Finally", "text": f"{N[pr['player_id']]} scored his {first} of the season{_drought(P, pr['player_id'], gid, r['date'])}.", "score": 48})
    for t in (winner, loser):
        fill = _filled_in(gid, r["date"], t, home_id if t == home else row["away_id"][0], season, N, name)
        if fill:
            items.append(fill)
    missing = absences.table(season, r["date"])  # who each club was missing tonight, against the league (Sora, 2026-10-20)
    for t in (winner, loser):
        text = absences.postgame_line(missing.get(t), W.the(t), t == winner, W.the)
        if text:
            items.append({"tag": "Shorthanded", "text": text, "score": 52 if t == winner else 44})
    items += _streak_ends(gid, r["date"], tonight, pg, G, P, N, W, winner, loser, home, away, season)
    items += runs_["items"]
    items += _preview_followups(gid, r, season, P, tonight, N, W, winner, loser, home, away, bool(fights))
    items += _league_marks(G, r["date"], gid, season, W, winner, loser)
    league = []
    try:
        from pipeline.export import league_lists
        league = league_lists.moves(gid, r["date"], tonight.filter(pl.col("pts") > 0)["player_id"].to_list(), N, season, _club_logs())
    except Exception as e:  # a nice-to-have: never lose the story over it
        print(f"league lists for {gid} unavailable: {e!r}")
    items += league
    items += [x for x in _club_lists(r["date"], tonight, N, W, season) if x["pid"] not in {y["pid"] for y in league}]
    if any(x["tag"] == "Rematch" and "split the two-game set" in x["text"] for x in items):  # same news, said once
        items = [x for x in items if "bounced back after" not in x["text"]]
    nxt = _next_games(r["date"], (away, home))
    for sp in r.get("surprises", []):
        if sp.get("kind") == "lineup" and "different line" in sp.get("head", ""):
            team = next((t for t in (away, home) if W.place(t) in sp["head"]), None)
            if team and team in nxt:
                n = nxt[team]
                where = W.at(n['home']) if n["home"] != team else f"at home against {W.place(n['away'])}"
                items.append({"tag": "Keep an eye on", "text": f"Whether {W.the(team)} keep that line together {where} on {weekday(n['date'])}.", "score": 45})
    for t, n in nxt.items():
        opp = n["home"] if n["away"] == t else n["away"]
        for item in _old_team_next(t, opp, n["date"], season, W):
            items.append({**item, "score": 40})
    held = [c for c in r.get("calls", []) if c.get("verdict") in ("held", "partly", "missed")]
    if held:
        h = sum(c["verdict"] == "held" for c in held)
        n = len(held)
        text = (("The preview’s call held." if h else "The preview’s call didn’t hold.") if n == 1 else
                ("Both of the preview’s calls held." if n == 2 else f"All {num(n)} of the preview’s calls held.") if h == n else
                f"{_cap(num(h))} of the preview’s {num(n)} calls held." if h else ("Neither of the preview’s calls held." if n == 2 else f"None of the preview’s {num(n)} calls held."))
        items.append({"tag": "The preview", "text": text, "score": 20})
    items = _merge_openers(items, home, winner, loser, W)
    items.sort(key=lambda i: -i["score"])
    picked, per = [], {}
    runs_told: set[str] = set(run_told)  # one run (streak, slump, snapped, start, last ten) per team; the lead's counts
    for i in items:
        if per.get(i["tag"], 0) >= (1 if i["tag"] in ("Finally", "Drought over") else MAX_PER_TAG) or len(picked) >= MAX_ITEMS:
            continue
        if i["tag"] in RUN_TAGS:
            low = i["text"].lower()
            # the club the item is about, by "the Hurricanes" first, its place only if neither club is named that way
            team = next((t for t in (winner, loser) if W.the(t).lower() in low or W.poss(t).lower() in low), None) \
                or next((t for t in (winner, loser) if W.place(t).lower() in low), None)
            if team in runs_told:
                continue
            runs_told.add(team)
        per[i["tag"]] = per.get(i["tag"], 0) + 1
        picked.append({"tag": i["tag"], "text": i["text"]})
    # each team's record after the game closes the story, as wire recaps do, unless an item already says it
    told_rec: set = set()  # both records always close the story, even beside a Start item (Sora's review, round 2)
    raced = {i.get("team") for i in items if i["tag"] == "Playoff race" and any(p_["text"] == i["text"] for p_ in picked)}
    rec = record_line(G, r["date"], winner, loser, W, season, gid, told_rec | (run_told - {winner}),
                      standings_after(season, r["date"], winner, loser, W, raced))
    # a home opener with nothing to join reads best on the record line: "the Canucks fell to 1-1-0 in their home opener"
    ho = next((i for i in picked if i["tag"] == "Home opener"), None)
    if ho and rec:
        m_ = re.search(re.escape(W.the(home)) + r" (?:improved|moved|fell|dropped|slipped|are|went)[^,.]*? to \d+-\d+-\d+", rec, re.I)
        if m_:
            rec = rec[:m_.end()] + (" with a win in their home opener" if winner == home else " in their home opener") + rec[m_.end():]
            picked.remove(ho)
    tail = ([rec] if rec else []) + [x for x in run_lead if x.startswith(_cap(W.the(loser)))]
    if tail:
        last_para = s3 if s3 else s2 if s2 else s1
        last_para += tail
    if len(picked) < MIN_ITEMS and not rec:
        res = results(G.filter(pl.col("date") <= r["date"]), winner, season)
        picked.append({"tag": "Record", "text": f"{_cap(W.the(winner))} are now {record(res)}."})
    return {"kicker": "What happened", "head": head, "paras": W.nicknames([p for p in (" ".join(s1), " ".join(s2), " ".join(s3)) if p], r.get("game_id")),
            "list_head": "What it means", "items": picked}


def _merge_openers(items: list[dict], home: str, winner: str, loser: str, W: "Words") -> list[dict]:
    """A home opener told on its own read stiffly (Sora's review, 2026-10-19), so it joins the item it goes with: the
    season's first game ("It was the first game of the season for both teams, and the Hurricanes lost their home
    opener"), a winning streak ("The Blackhawks won their home opener, their third straight win") or a run against
    tonight's opponent."""
    ho = next((i for i in items if i["tag"] == "Home opener"), None)
    if not ho:
        return items
    won = winner == home
    T = _cap(W.the(home))
    did = "won" if won else "lost"
    so = next((i for i in items if i["tag"] == "Season opener"), None)
    if so:
        if "both teams" in so["text"]:
            so["text"] = f"{so['text'][:-1]}, and {W.the(home)} {did} their home opener."
        elif W.poss(home) in so["text"]:  # "The Red Wings lost their home opener, which was also their first game of the season."
            so["text"] = f"{T} opened their season with a {'win' if did == 'won' else 'loss'} at home."
        else:
            return items
        return [i for i in items if i is not ho]
    first_w = next((i for i in items if i["tag"] == "Snapped" and won and "for the first time this season" in i["text"]), None)
    if first_w:  # "The Blackhawks won their home opener for their first win of the season, in their fourth game."
        first_w["text"] = f"{T} won their home opener for their first win of the season{first_w['text'].split('this season', 1)[1]}"
        return [i for i in items if i is not ho]
    streak_ = next((i for i in items if i["tag"] == "Streak" and won and i["text"].endswith(("straight wins.", f"straight wins for {W.the(home)}.", "straight wins!"))
                    and W.the(home).lower() in i["text"].lower()), None)
    if streak_:
        n = next((k for k, w in enumerate(grammar.WORDS) if w and (f" {w} straight" in streak_["text"].lower() or streak_["text"].lower().startswith(w + " "))), None)
        if n:
            streak_["text"] = f"{T} won their home opener, their {grammar.ordinal_word(n)} straight win."
            return [i for i in items if i is not ho]
    h2h = next((i for i in items if i.get("h2h")), None)
    if h2h:
        t, opp, h, back = h2h["h2h"]
        h2h["text"] = (f"{T} won their home opener and have beaten {W.the(opp)} {num(h)} straight times{back}." if t == home else
                       f"{T} lost their home opener, and {W.the(t)} have now beaten them {num(h)} straight times{back}.")
        return [i for i in items if i is not ho]
    return items


RUN_TAGS = {"Streak", "Snapped", "Slump", "Start", "Last ten", "Home run", "Road run"}
POINT_STREAK = 5  # games with a point (a win or an overtime/shootout loss) worth telling
LAST_TEN = (8, 2)  # won this many or fewer of the last ten
HOME_RUN, ROAD_RUN = 5, 4  # straight wins at home / on the road worth telling
H2H_RUN = 4  # straight wins against one opponent, across seasons since 2023-24
BREAK_DAYS = 7  # days without a game before "first game since the break"


def _run_len(res: list[dict], ok) -> int:
    n = 0
    for x in reversed(res):
        if not ok(x):
            break
        n += 1
    return n


def _team_form(G: pl.DataFrame, r: dict, t: str, won: bool, W: Words, season: int) -> list[dict]:
    """A team's form beyond straight wins and winless runs (Sora's review of 2025-26 against NHL.com and AP, 2026-10-17:
    team runs were the most-missed fact in What it means, 702 games): a point streak with its record ("points in nine
    straight, 7-0-2") and whether it is the league's longest right now, a point streak that ended, the last ten games,
    straight wins or losses at home or on the road, and a run against tonight's opponent going back to 2023-24."""
    Gd = G.filter(pl.col("date") <= r["date"])
    res = results(Gd, t, season)
    if not res:
        return []
    pts = lambda x: x["win"] or x["end"] != "REG"
    T, Tp = _cap(W.the(t)), _cap(W.poss(t))
    items = []
    n = _run_len(res, pts)
    wins = _run_len(res, lambda x: x["win"])
    if n >= POINT_STREAK and wins < n:  # a run of plain wins is told as a winning streak
        others = [_run_len(results(Gd, o, season), pts) for o in set(Gd.filter(pl.col("season") == season)["home"].to_list()) if o != t]
        tail = ", the longest run in the league right now" if others and n > max(others) else ""
        items.append({"tag": "Streak", "text": f"{T} have points in {num(n)} straight games ({record(res[-n:])}){tail}.", "score": 58 + n})
    prev_n = _run_len(res[:-1], pts)
    prev_w = _run_len(res[:-1], lambda x: x["win"])
    if not pts(res[-1]) and prev_n >= POINT_STREAK and prev_w < 3:  # a winning streak that ended is told as "Snapped" already
        items.append({"tag": "Snapped", "text": f"{Tp} {num(prev_n)}-game point streak is over.", "score": 60 + prev_n})
    if len(res) >= 10:
        w10 = sum(x["win"] for x in res[-10:])
        if w10 >= LAST_TEN[0] and wins < 5:
            items.append({"tag": "Last ten", "text": f"{T} have won {num(w10)} of their last ten.", "score": 50})
        elif w10 <= LAST_TEN[1] and _run_len(res, lambda x: not x["win"]) < 4:
            how = "just one" if w10 == 1 else "two" if w10 == 2 else "none"
            items.append({"tag": "Last ten", "text": f"{T} have won {how} of their last ten.", "score": 50})
    here = res[-1]["home"]
    same = [x for x in res if x["home"] == here]
    where = "at home" if here else "on the road"
    k = _run_len(same, lambda x: x["win"])
    if won and k >= (HOME_RUN if here else ROAD_RUN) and k < wins:  # k == wins is the plain winning streak
        items.append({"tag": "Home run" if here else "Road run", "text": f"That is {num(k)} straight wins {where} for {W.the(t)}.", "score": 47 + k})
    if won and not here and len(same) >= 3 and not any(x["win"] for x in same[:-1]):
        # "the Flyers’ first road win of the season" (outside notes, Oct 21: NHL.com led its notes with it)
        items.append({"tag": "Road run", "text": f"It was {W.poss(t)} first road win of the season, in their {ORDINAL_N(len(same))} road game.", "score": 56})
    kl = _run_len(same, lambda x: not x["win"])
    if not won and kl >= (HOME_RUN if here else ROAD_RUN) and kl > _run_len(res, lambda x: not x["win"]):
        verb = "lost" if all(x["end"] == "REG" for x in same[-kl:]) else "gone winless in"
        items.append({"tag": "Home run" if here else "Road run", "text": f"{T} have {verb} {num(kl)} straight {where}.", "score": 47 + kl})
    opp = res[-1]["opp"]
    if not won:  # one head-to-head run, told from the winner's side
        return items
    both = Gd.filter(((pl.col("home") == t) & (pl.col("away") == opp)) | ((pl.col("home") == opp) & (pl.col("away") == t))).sort("date", "game_id")
    meet = [{"win": (x["home_score"] > x["away_score"]) == (x["home"] == t), "season": x["season"]} for x in both.iter_rows(named=True)]
    h = _run_len(meet, lambda x: x["win"] == won)
    if h >= H2H_RUN and h > sum(1 for x in meet if x["season"] == season):  # reaches back past this season
        first = meet[-h]["season"]
        back = ", going back to last season" if first == season - 1 else f", going back to the {first}-{str(first + 1)[2:]} season"
        text = (f"{T} have beaten {W.the(opp)} {num(h)} straight times{back}." if won else
                f"{T} have lost {num(h)} straight to {W.the(opp)}{back}.")
        items.append({"tag": "Head to head", "text": text, "score": 52, "h2h": (t, opp, h, back)})
    return items


def _break_item(G: pl.DataFrame, r: dict, teams: tuple[str, str], W: Words) -> list[dict]:
    """The first game after a long gap in the schedule (the Olympic break in February 2026, the All-Star or holiday
    break): "This was the first game for both teams since the Olympic break." Only when both sides had the gap."""
    day = Date.fromisoformat(r["date"][:10])
    gaps = []
    for t in teams:
        prev = G.filter((pl.col("date") < r["date"]) & ((pl.col("home") == t) | (pl.col("away") == t)))["date"]
        if not prev.len():
            return []
        gaps.append((day - Date.fromisoformat(prev.max()[:10])).days)
    # the first game of the season reads as that, not "a break of 166 days" (Sora's review, 2026-10-19)
    openers = [t for t in teams if not G.filter((pl.col("date") < r["date"]) & (pl.col("season") == G.filter(pl.col("game_id") == r["game_id"])["season"][0])
                                                 & ((pl.col("home") == t) | (pl.col("away") == t))).height]
    if len(openers) == 2:
        return [{"tag": "Season opener", "text": "It was the first game of the season for both teams.", "score": 59}]
    if openers:
        return [{"tag": "Season opener", "text": f"It was {W.poss(openers[0])} first game of the season.", "score": 59}]
    if min(gaps) < BREAK_DAYS:
        return []
    what = "the Olympic break" if day.month == 2 and day.year == 2026 else f"a break of {num(min(gaps))} days" if min(gaps) == max(gaps) else "a break"
    return [{"tag": "Back at it", "text": f"This was the first game for both teams since {what}.", "score": 45}]


DROUGHT_OVER, DROUGHT_SCORER = 12, 15  # games without a goal, for a player who scored this many last season


def _drought_over(P: pl.DataFrame, pid: int, gid: int, day: str, season: int) -> int:
    """How many games a regular goal scorer (DROUGHT_SCORER goals last season) had played without a goal before
    scoring tonight, when that is DROUGHT_OVER or more this season; else 0."""
    last_season = P.filter((pl.col("player_id") == pid) & (pl.col("season") == season - 1))["g"].sum()
    if last_season < DROUGHT_SCORER:
        return 0
    before = P.filter((pl.col("player_id") == pid) & (pl.col("season") == season) & (pl.col("date") < day) & (pl.col("game_id") != gid)).sort("date", "game_id")
    scored = before.filter(pl.col("g") > 0)
    if not scored.height:
        return 0
    played = before.filter(pl.col("date") > scored["date"][-1]).height
    return played if played >= DROUGHT_OVER else 0


def _run_story(res_w: list[dict], res_l: list[dict], winner: str, loser: str, W: Words, pk) -> tuple[list, list[str], set[str]]:
    """Season-defining runs that tonight ended or stretched, told first (Sora, 2026-10-18: the Flyers' first win, after
    a 0-6-3 start, was missing from the headline and the story). Returns headlines, sentences (the winner's open
    paragraph one, the loser's close the story in place of its record) and the teams whose run they told (their run
    items and record are not told again)."""
    heads, lead, told = [], [], set()
    W_, L_ = _cap(W.the(winner)), _cap(W.the(loser))
    prev_w = res_w[:-1]
    k, n = streak(prev_w)
    if k == "winless" and n >= 4 and n == len(prev_w):  # a first win of the season
        heads.append((min(99, 80 + 2 * n), pk([f"{W_} finally win one", f"{W_} get their first win at last", f"At last, a first win for {W.the(winner)}"], "firstwin")))
        lead.append(f"{W_} won for the first time this season, at the {ORDINAL_N(len(res_w))} try, after starting {record(prev_w)}.")
        told.add(winner)
    elif k == "winless" and n >= 6:
        run = prev_w[-n:]
        losing = all(x["end"] == "REG" for x in run)
        heads.append((min(96, 72 + 2 * n), pk([f"{W_} snap a {num(n)}-game losing streak", f"{W_} finally end the skid"] if losing else
                                               [f"{W_} end a {num(n)}-game winless run", f"{W_} finally end the skid"], "skid")))
        lead.append(f"{W_} snapped a {num(n)}-game losing streak." if losing else
                    f"{W_} won for the first time in {num(n + 1)} games, after going {record(run)}.")
        told.add(winner)
    prev_l = res_l[:-1]
    k, n = streak(prev_l)
    lk, ln = streak(res_l)
    if k == "won" and n >= 5 and n == len(prev_l):  # a perfect start is over
        heads.append((80 + n, pk([f"{W_} hand {W.the(loser)} their first loss", f"{L_}’ perfect start is over"], "perfect")))
        lead.append(f"{L_} lost for the first time this season, after starting {record(prev_l)}.")
        told.add(loser)
    elif lk == "winless" and ln >= 5 and ln == len(res_l):  # still no win
        heads.append((70 + 2 * ln, pk([f"{W_} keep {W.the(loser)} winless", f"Still no win for {W.the(loser)}"], "nowin")))
        lead.append(f"{L_} are still looking for their first win of the season, now {record(res_l)}.")
        told.add(loser)
    elif lk == "winless" and ln >= 8:
        run = res_l[-ln:]
        losing = all(x["end"] == "REG" for x in run)
        heads.append((62 + 2 * ln, pk([f"{W_} hand {W.the(loser)} a {ORDINAL_N(ln)} straight loss"] if losing else [f"{W_} keep {W.the(loser)} winless in {num(ln)}"], "slide")))
        lead.append(f"{L_} have lost {num(ln)} straight." if losing else f"{L_} are winless in {num(ln)} ({record(run)}).")
        told.add(loser)
    return heads, lead, told



RARE_SCORER = 0.10  # goals a game (shrunk) at or under which a two-goal night leads the story: a player who scores about once in ten games


def _rare_scorers(P: pl.DataFrame, night: pl.DataFrame, day: str, N: dict[int, str]) -> list[dict]:
    """Players who scored 2+ goals tonight but rarely score (Sora, 2026-10-21: Deslauriers' two goals are the story of
    a game; two goals from a regular scorer are not). His goals a game before tonight, since 2023-24, are pulled toward
    his position's average with 20 games' weight, the same shrink the report cards use for "scores in 1 game in N"."""
    before = P.filter((pl.col("date") < day) & (pl.col("pos") != "G"))
    if not before.height:
        return []
    avg = {k: v for k, v in before.group_by(pl.col("pos") == "D").agg(pl.col("g").mean()).iter_rows()}
    out = []
    for r in night.iter_rows(named=True):
        pid = r["player_id"]
        if r["g"] < 2 or pid not in N:
            continue
        mine = before.filter(pl.col("player_id") == pid)
        if not mine.height:
            continue
        isd = bool(mine["pos"][-1] == "D")
        lam = (float(mine["g"].sum()) + 20 * avg.get(isd, 0.18)) / (mine.height + 20)
        if lam <= RARE_SCORER:
            out.append({"pid": pid, "team": r["team"], "goals": int(r["g"]), "lam": lam, "odds": 1 - math.exp(-lam)})
    return sorted(out, key=lambda x: -x["goals"])


OLD_CLUB_GAMES = 40  # games he played for the old club for his first game against it to be news


def old_club_returns(P: pl.DataFrame, gid: int, day: str, home: str) -> list[dict]:
    """Players facing the club they played for before this one for the first time since leaving (Marchenko in Columbus,
    Oct 20: NHL.com's lead, and we never said it), with his goals and points tonight. Never "traded": the NHL has no
    trade feed, and the move may have been a signing."""
    night = P.filter(pl.col("game_id") == gid)
    if not night.height:
        return []
    hist = P.filter((pl.col("date") < day) & pl.col("player_id").is_in(night["player_id"].to_list())).sort("date")
    out = []
    for r in night.iter_rows(named=True):
        t, o = r["team"], r["opp"]
        mine = hist.filter(pl.col("player_id") == r["player_id"])
        teams = mine["team"].to_list()
        prev = next((x for x in reversed(teams) if x != t), None)
        if prev != o or not teams:
            continue
        for_them = mine.filter(pl.col("team") == o)
        if for_them.height < OLD_CLUB_GAMES:
            continue
        if mine.filter((pl.col("date") > for_them["date"].max()) & (pl.col("opp") == o)).height:
            continue  # he has met them since
        out.append({"pid": r["player_id"], "team": t, "old": o, "n_old": for_them.height, "g": int(r["g"]), "pts": int(r["pts"]),
                    "at_old": o == home})
    return sorted(out, key=lambda x: (-x["g"], -x["pts"], -x["n_old"]))


def _pts_words(g: int, a: int) -> str:
    """"a goal and two assists", "two goals and an assist", "three assists", "two goals"."""
    gw = "a goal" if g == 1 else f"{num(g)} goals" if g else ""
    aw = "an assist" if a == 1 else f"{num(a)} assists" if a else ""
    return " and ".join(x for x in (gw, aw) if x)


def _star_line(night, winner: str, last, N) -> str | None:
    """The winners' best scoresheet when it is worth a headline (3+ points or two goals): "Farabee has a goal and two
    assists". A fallback for the plain "The X beat the Y"."""
    rows = [r for r in night.iter_rows(named=True) if r["team"] == winner and r["player_id"] in N and (r["pts"] >= 3 or r["g"] >= 2)]
    if not rows:
        return None
    r = max(rows, key=lambda r: (r["pts"], r["g"]))
    return f"{last(r['player_id'])} has {_pts_words(int(r['g']), int(r['pts'] - r['g']))}"


def _night_heads(night, winner: str, W, L_: str, last, N, found: list[dict] | None = None) -> list[tuple[int, str]]:
    """Five-point nights lead (outside notes, Oct 22: McDavid and Draisaitl each had five, our headline went to Ehlers'
    600th point). Two on one team are named together; a milestone on the same night rides along ("Nylander reaches 700
    points with a five-point night"). A loser's five points lead only over the plain headlines, not over a steal or a
    comeback."""
    marks = {x["pid"]: x["mark"] for x in found or [] if x["kind"] == "milestone" and x["what"] == "point" and x["mark"] >= 500}
    big = [r for r in night.iter_rows(named=True) if r["pts"] >= 5 and r["player_id"] in N]
    out = []
    by_team: dict = {}
    for r in big:
        by_team.setdefault(r["team"], []).append(r)
    for t, rs in by_team.items():
        rs.sort(key=lambda r: (-r["pts"], -r["g"]))
        tail = f" as {W.the(winner)} beat {L_}" if t == winner else f", but {W.the(t)} lose"
        if len(rs) >= 2:
            same = rs[0]["pts"] == rs[1]["pts"]
            pts = f"each have {num(int(rs[0]['pts']))} points" if same else f"have {num(int(rs[0]['pts']))} and {num(int(rs[1]['pts']))} points"
            out.append((97 if t == winner else 80, f"{last(rs[0]['player_id'])} and {last(rs[1]['player_id'])} {pts}{tail}"))
        elif rs[0]["player_id"] in marks:
            pid = rs[0]["player_id"]
            out.append((95 if t == winner else 80, f"{last(pid)} reaches {marks[pid]:,} points with a {num(int(rs[0]['pts']))}-point night{tail}"))
        else:
            out.append((94 if t == winner else 70, f"{_cap(num(int(rs[0]['pts'])))} points for {last(rs[0]['player_id'])}{tail}"))
    return out


def _clutch_heads(goals: list[dict], side, winner: str, end: str, W, W_: str, L_: str, last, N, pk) -> list[tuple[int, str]]:
    """A late winner leads (outside notes, Oct 22: Stecher put Toronto ahead for good with 28 seconds left; our headline
    went to the loser's four-point night), and so does one player tying it late and winning it in overtime (Hintz)."""
    out = []
    reg = [g for g in goals if g["period"] <= 3]
    if end == "REG" and reg:
        # the goal that put them ahead for good, in the last two minutes of the third
        for i, g in enumerate(reg):
            if side(g) == winner and g["period"] == 3 and g["sec"] >= 1080 and g["strength"] != "en" and g["p1"] in N:
                before = (g["hs"] - (1 if g["home"] else 0), g["as"] - (0 if g["home"] else 1))
                if before[0] == before[1] and all(side(h) == winner or h["strength"] == "en" for h in reg[i + 1:]):
                    left = grammar.time_left(1200 - g["sec"])
                    out.append((92 if g["sec"] >= 1140 else 88,
                                pk([f"{last(g['p1'])} wins it with {left} left", f"{last(g['p1'])} scores with {left} left to lift {W.the(winner)}"], "late")))
                break
    ot = next((g for g in goals if g["period"] > 3), None)
    if end == "OT" and ot and ot["p1"] in N:
        tie = next((g for g in reversed(reg) if g["hs"] == g["as"] and g["period"] == 3 and g["sec"] >= 900 and g["p1"] == ot["p1"]), None)
        if tie:
            out.append((92, f"{last(ot['p1'])} ties it late and wins it in overtime"))
    return out


def _fact_heads(found: list[dict], night, res_w: list[dict], res_l: list[dict], winner: str, loser: str, W: Words, W_: str, L_: str, last, N, rare: list[dict] | None = None) -> list[tuple[int, str]]:
    """Headlines that lead with the night's big fact, as NHL.com and AP do (Sora's review of 2025-26, 2026-10-17):
    a round-number milestone (300 goals, 500 points, 1,000 games), a goal in an NHL debut, a four-point night, a
    seventh straight win, a long point streak ended. Each still carries the result."""
    from pipeline.export import timeline
    heads = []
    beat = f"{W.the(winner)} beat {L_}"  # mid-headline, so "the" stays lower case
    for x in found:
        if x["pid"] not in N:
            continue
        who = last(x["pid"])
        team = x["team"]
        as_ = f" as {beat}" if team == winner else f", but {W.the(team)} lose"
        if x["kind"] == "milestone" and x["what"] == "goal" and x["mark"] >= 300:
            heads.append((93, f"{who} scores his {timeline.th(x['mark'])}{as_}"))
        elif x["kind"] == "milestone" and x["what"] == "point" and x["mark"] >= 500:
            heads.append((90, f"{who} reaches {x['mark']:,} points{as_}"))
        elif x["kind"] == "milestone" and x["what"] == "game" and x["mark"] >= 1000:
            heads.append((88, f"{who} plays his {timeline.th(x['mark'])} game{as_}"))
        elif x["kind"] == "first_goal" and (x.get("goals") or 0) >= 2:
            # NHL.com led with Knuble's first two NHL goals; ours led with the goalie (outside notes, Oct 21)
            many = "two" if x["goals"] == 2 else num(x["goals"])
            heads.append((91, f"{who} scores his first {many} NHL goals{as_}"))
        elif x["kind"] == "first_goal" and x.get("debut"):
            heads.append((82, f"{who} scores in his NHL debut{as_}"))
    for row in night.iter_rows(named=True):
        if row["pts"] >= 4 and row["g"] < 3 and row["player_id"] in N:
            who = last(row["player_id"])
            as_ = f" as {beat}" if row["team"] == winner else f", but {W.the(row['team'])} lose"
            heads.append((85, f"{_cap(num(int(row['pts'])))} points for {who}{as_}"))
    for x in rare or []:
        who = last(x["pid"])
        as_ = f" as {beat}" if x["team"] == winner else f", but {W.the(x['team'])} lose"
        # in a loss it is a clause, not the night's result (a shootout winner beat Zellweger's two, Oct 2026)
        heads.append((80 if x["team"] == winner else 70, f"{who} scores {'twice' if x['goals'] == 2 else num(x['goals']) + ' times'}{as_}"))
    k, n = streak(res_w)
    if k == "won" and n >= 7:
        heads.append((84, f"{W_} make it {num(n)} straight"))
    pts = lambda x: x["win"] or x["end"] != "REG"
    ended = _run_len(res_l[:-1], pts) if res_l and not pts(res_l[-1]) else 0
    if ended >= 10:
        heads.append((84, f"{W_} end {W.poss(loser)} {num(ended)}-game point streak"))
    return heads


def _league_marks(G: pl.DataFrame, day: str, gid: int, season: int, W: Words, winner: str, loser: str) -> list[dict]:
    """The league's last unbeaten-in-regulation or winless club going down or winning (NHL.com: the Oilers "were the last
    team in the NHL without a regulation loss"; the Flyers' 0-6-3 start; Sora, 2026-10-16). Counted from our own finished
    games, so it starts at the season's first game."""
    g = G.filter(pl.col("season") == season)
    prior = g.filter(pl.col("date") < day)
    if prior.height < 20:
        return []
    def table(df: pl.DataFrame) -> dict[str, list[int]]:
        t: dict[str, list[int]] = {}
        for x in df.iter_rows(named=True):
            hw = x["home_score"] > x["away_score"]
            for team, won in ((x["home"], hw), (x["away"], not hw)):
                row = t.setdefault(team, [0, 0, 0])  # games, wins, regulation losses
                row[0] += 1
                row[1] += won
                row[2] += (not won) and x["last_period"] == "REG"
        return t
    before = table(prior)
    unbeaten = [t for t, v in before.items() if v[2] == 0 and v[0] >= 3]
    winless = [t for t, v in before.items() if v[1] == 0 and v[0] >= 3]
    out = []
    if unbeaten == [loser] and (G.filter((pl.col("game_id") == gid)).row(0, named=True)["last_period"] == "REG"):
        out.append({"tag": "Snapped", "text": f"{_cap(W.the(loser))} were the last team in the NHL without a regulation loss.", "score": 66})
    if winless == [winner]:
        out.append({"tag": "Finally", "text": f"{_cap(W.the(winner))} were the last team in the NHL without a win.", "score": 64})
    return out


ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}


def _hat_run(P: pl.DataFrame, pid: int, gid: int) -> int:
    """How many games in a row, ending with this one, the player scored three or more (1 when only tonight)."""
    mine = P.filter((pl.col("player_id") == pid) & (pl.col("game_id") <= gid)).sort("date", "game_id")
    run = 0
    for g in reversed(mine["g"].to_list()):
        if g < 3:
            break
        run += 1
    return run


def _gordie_howe(P: pl.DataFrame, gid: int, season: int) -> list[int]:
    """Players with a goal, an assist and a fighting major in one game."""
    from pipeline.export import penalties
    p = penalties.rows(season)
    if not p.height:
        return []
    fought = set(p.filter((pl.col("game_id") == gid) & (pl.col("pen_desc") == "fighting"))["p1"].drop_nulls().to_list())
    tonight = P.filter((pl.col("game_id") == gid) & pl.col("player_id").is_in(list(fought)))
    return [r["player_id"] for r in tonight.iter_rows(named=True) if r["g"] >= 1 and r["a1"] + r["a2"] >= 1]


DROUGHT_GAMES = 10  # games played since his last goal before "his first in N games" is added to a first goal of the season


def _drought(P: pl.DataFrame, pid: int, gid: int, day: str) -> str:
    """", his first in 19 games" or ", his first since Feb 28": how long since the scorer's last goal, counted in games he
    PLAYED (Sora, 2026-10-16: NHL.com said Copp had gone 19 games and Hedman had not scored since Feb 28; the count never
    says why the games were missed, since a long absence and a slump look alike in the game log). Empty when our game
    logs (2023-24 on) hold no earlier goal."""
    before = P.filter((pl.col("player_id") == pid) & (pl.col("date") < day) & (pl.col("game_id") != gid)).sort("date", "game_id")
    scored = before.filter(pl.col("g") > 0)
    if not scored.height:
        return ""
    last = scored.row(-1, named=True)
    played = before.filter(pl.col("date") > last["date"]).height
    d, today = Date.fromisoformat(last["date"][:10]), Date.fromisoformat(day[:10])
    if (today - d).days > 120:  # over a summer or a long absence the date says it; the games played follow
        year = f", {d.year}" if d.year != today.year else ""  # "since Feb 28, 2025" across a year end
        return f", his first since {d.strftime('%b')} {d.day}{year}" + (f" ({num(played)} games played since)" if played >= DROUGHT_GAMES else "")
    return f", his first in {num(played)} games" if played >= DROUGHT_GAMES else ""


def _swing(gid: int) -> list[dict]:
    p = SITE_DATA / "scores" / f"{gid}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("players", [])


def _next_games(after: str, teams: tuple[str, str]) -> dict[str, dict]:
    try:
        sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    except OSError:
        return {}
    out = {}
    for g in sorted(sched, key=lambda g: g["start"]):
        if g["date"] <= after:
            continue
        for t in teams:
            if t not in out and t in (g["home"], g["away"]):
                out[t] = g
    return out


def _old_team_next(t: str, opp: str, day: str, season: int, W: Words) -> list[dict]:
    """A player who faces his old team in his club's next game, for the first time since leaving."""
    _, P, N = book()
    cur = _current_team(P, season)
    out = []
    for pid, team in cur.items():
        if team != t:
            continue
        g = P.filter(pl.col("player_id") == pid).sort("date")
        teams = g["team"].to_list()
        prev = next((x for x in reversed(teams) if x != t), None)
        if prev != opp:
            continue
        for_them = g.filter(pl.col("team") == opp)
        if for_them.height < 40 or g.filter((pl.col("date") > for_them["date"].max()) & (pl.col("opp") == opp)).height:
            continue
        out.append((for_them.height, pid))
    out.sort(reverse=True)
    return [{"tag": "Up next", "text": f"{N[pid]} faces his old team, {W.place(opp)}, on {weekday(day)}."} for _, pid in out[:1]]


def run_recaps(season: int = CURRENT_SEASON) -> int:
    """Add a story to every post-game file. Runs after `game_scores`, which it reads for the Swing."""
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    n = 0
    for p in sorted((SITE_DATA / "recaps").glob("2*.json")):
        r = json.loads(p.read_text())
        if r.get("pending"):  # the story leans on Swing and the graded calls: written once the shift chart is in
            continue
        try:
            story = recap_story(r, places, season)
        except Exception as e:  # a story is a nice-to-have: never lose the page over it
            print(f"story for {r['game_id']} failed: {e!r}")
            story = None
        if story:
            r["story"] = story
            p.write_text(json.dumps(r, separators=(",", ":"), ensure_ascii=False))
            n += 1
    return n
