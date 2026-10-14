"""The home page's data (Sora, 2026-10-14): last night's results and the coming games, each with an interest score,
a few reasons and a one-line note; league leaders; and last night's standout stat.

Interest follows Sora's notes on the game list (export/storylines.py has the detectors): a rare individual night,
brothers, a star out of character, rivalry (division, arenas close together, last spring's playoff series), fresh
homecomings, drama (one-goal finishes, overtime, comebacks, late winners, reviews), the bottom of the table, and a club
down to a little-used goalie. The browser adds a boost for games in the visitor's own division, so the score here is
the same for everyone.

Nothing here shows a win chance before a game: upcoming games are scored without the hidden pre-game figure. After a
game, the post-game page reveals it, so a finished upset may say "We gave Calgary 26%".

Writes site/public/data/home.json.
"""
import json
import re
from datetime import date as Date, datetime, timezone

import polars as pl

from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import penalties, storylines, stories

NOTE_MAX = 30  # characters: a note is one line beside the score on a phone
CHIPS = 4
UPCOMING_DAYS = 3  # schedule dates ahead that get scored (today and tomorrow wherever the visitor is, with room)
LEADERS = 5
STANDOUT = {  # stat: (column, words for one game, words for the board, recorded by the arena's scorer)
    "g": ("g", "scored {n} goals", "goals", False),
    "a": ("a", "had {n} assists", "assists", False),
    "p": ("p", "had {n} points", "points", False),
    "sog": ("sog", "put {n} shots on goal", "shots on goal", False),
    "hits": ("hits", "threw {n} hits", "hits", True),
    "blocks": ("blocks", "blocked {n} shots", "blocked shots", True),
}
CALL_NOUN = {"volume": "shot volume", "quality": "shot quality", "suppression": "D", "qualityAllowed": "D", "dShots": "active D",
             "inClose": "net-front game", "forecheck": "forecheck", "physical": "hitting", "pp": "power play", "pk": "penalty kill",
             "depth": "depth", "pace": "pace", "turnover": "forecheck"}
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _sentences(text: str, most: int) -> str:
    """The first few sentences of a paragraph, keeping initials ("J.T. Miller") whole."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    out = []
    for p in parts:
        if out and re.search(r"(\b[A-Z]\.){1,2}$", out[-1]):
            out[-1] += " " + p
        else:
            out.append(p)
    return " ".join(out[:most])


class Namer:
    def __init__(self, places: dict[str, str]):
        self.W = stories.Words(places)
        self.G, self.P, self.N = stories.book()

    def last(self, pid: int, season: int = CURRENT_SEASON) -> str:
        """His last name, or his full name when a teammate shares it (the Tkachuks)."""
        return stories._short(pid, self.P, self.N, season).split(" (#")[0] if pid in self.N else ""

    def nick(self, t: str) -> str:
        return self.W.nick.get(t, t)

    def poss(self, t: str) -> str:
        n = self.nick(t)
        return n + ("’" if n.endswith("s") else "’s")


def _fit(*options: str | None) -> str | None:
    """The first option short enough for one line."""
    return next((o for o in options if o and len(o) <= NOTE_MAX), None)


def _pick(parts: list[tuple[float, str | None, str | None]]) -> tuple[int, list[str], str | None]:
    """(score, chips, note) from (weight, chip, note) parts."""
    parts = sorted((p for p in parts if p[0] > 0), key=lambda p: -p[0])
    chips = list(dict.fromkeys(c for _, c, _ in parts if c))[:CHIPS]
    # the note is the strongest reason that is particular to this game; a plain "One-goal game" only when nothing is
    note = next((_fit(n, c) for _, c, n in parts if n and _fit(n, c)), None) or next((_fit(c) for c in chips if _fit(c)), None)
    return round(sum(p[0] for p in parts)), chips, note


def _rivals(a: str, b: str, season: int, div: dict[str, str]) -> list[tuple[float, str | None, str | None]]:
    out = []
    if div.get(a) and div.get(a) == div.get(b):
        out.append((5, "Division rivals", None))
    if storylines.neighbors(a, b):
        out.append((4, "Regional rivals", None))
    sr = storylines.playoff_series(a, b, season)
    if sr:
        out.append((12 if sr["games"] >= 6 else 8, "Playoff rematch", "Playoff rematch"))
    return out


# --- finished games ----------------------------------------------------------------------------------------------------

def result_interest(r: dict, nm: Namer, div: dict[str, str], season: int) -> dict:
    gid, away, home = r["game_id"], r["away"], r["home"]
    row = nm.G.filter(pl.col("game_id") == gid)
    home_id = row["home_id"][0] if row.height else None
    goals = stories._goals(gid, season, home_id) if home_id is not None else []
    s = r["score"]
    winner = home if s[home] > s[away] else away
    loser = away if winner == home else home
    W = nm.W
    side = lambda g: home if g["home"] else away
    diff = s[winner] - s[loser]
    en = sum(1 for g in goals if side(g) == winner and g["strength"] == "en")
    margin = diff if diff <= 1 else max(1, diff - en)
    parts: list[tuple[float, str | None, str | None]] = []
    end = r.get("end", "REG")
    if margin <= 1:
        parts.append((12, "One-goal game", None))
    elif margin == 2:
        parts.append((5, None, None))
    if end == "OT":
        parts.append((8, "Won in overtime", None))
    elif end == "SO":
        parts.append((6, "Shootout", None))
    ties = sum(1 for g in goals if g["hs"] == g["as"])
    if ties >= 3:
        parts.append((3 * ties, f"Tied {_num(ties)} times", f"Tied {_num(ties)} times"))
    lead = {home: 0, away: 0}
    for g in goals:
        lead[home], lead[away] = max(lead[home], g["hs"] - g["as"]), max(lead[away], g["as"] - g["hs"])
    blown = stories._blown(goals, winner == home)
    if lead[loser] >= 2:
        parts.append((14, f"Came back from {_num(lead[loser])} down", f"Came back from {_num(lead[loser])} down"))
    elif blown:
        parts.append((10, f"Blew {blown}, won anyway", f"Blew {blown}, won anyway"))
    gw = next((g for g in goals if side(g) == winner and (g["hs"] if g["home"] else g["as"]) == s[loser] + 1), None)
    if end == "REG" and gw and gw["period"] == 3 and gw["sec"] >= 900 and margin <= 1:
        parts.append((6, "Late winner", f"{nm.last(gw['p1'])}’s late winner"))
    rev = r.get("reveal") or {}
    if rev.get("p_home") is not None:
        pw = rev["p_home"] if winner == home else 1 - rev["p_home"]
        if pw < 0.45:
            pct = round(pw * 100)
            parts.append(((0.45 - pw) * 80, f"We gave {W.place(winner)} {pct}%", f"We gave {W.place(winner)} {pct}%"))
    share = ((r.get("replays") or {}).get("share") or {}).get(winner)
    if share is not None and share < 0.45:
        parts.append(((0.45 - share) * 60, "Outchanced, won anyway", "Outchanced, won anyway"))
    # individual nights
    by: dict[int, list[dict]] = {}
    pts: dict[int, list[int]] = {}
    for g in goals:
        by.setdefault(g["p1"], []).append(g)
        pts.setdefault(g["p1"], [0, 0])[0] += 1
        for h in (g["p2"], g["p3"]):
            if h:
                pts.setdefault(h, [0, 0])[1] += 1
    for pid, gs in by.items():
        if any(g["goalie"] for g in gs):
            parts.append((30, "Goalie goal", f"{nm.last(pid)} scored, in net"))
        if len(gs) >= 4:
            parts.append((26, f"{nm.last(pid)} scored {_num(len(gs))}", f"{nm.last(pid)} scored {_num(len(gs))}"))
        elif len(gs) == 3:
            natural = storylines.natural_hat(goals) == pid
            parts.append((22 if natural else 18, "Natural hat trick" if natural else "Hat trick",
                          f"{nm.last(pid)}’s {'natural ' if natural else ''}hat trick"))
        if len(gs) >= 3:
            helper = storylines.set_up_all(goals, pid)
            if helper:
                kin = helper in storylines.siblings().get(pid, set())
                parts.append((12 if kin else 4, f"His brother set up all {_num(len(gs))}" if kin else f"{nm.last(helper)} set up all {_num(len(gs))}", None))
    for pid, (g_, a_) in pts.items():
        if g_ + a_ >= 5:
            what = f"{a_} assists" if not g_ else f"{g_ + a_} points"
            parts.append((12, f"{nm.last(pid)}: {what}", f"{nm.last(pid)}: {what}"))
        elif g_ + a_ == 4 and g_ < 3:
            parts.append((5, f"{nm.last(pid)}: 4 points", None))
    if any(g["strength"] == "ps" for g in goals):
        parts.append((5, "Penalty-shot goal", None))
    keepers = {x["team"]: x for x in r.get("goalies", []) if x.get("started")}
    if s[loser] == 0 and keepers.get(winner):
        k = keepers[winner]
        parts.append((6, f"{nm.last(k['id']) or k['name']} shutout", f"{nm.last(k['id']) or k['name']} shutout"))
    try:
        from pipeline.export import firsts
        found = firsts.for_game(gid, season, nm.G, nm.P)
    except Exception:
        found = []
    for x in found:
        who = nm.last(x["pid"])
        if not who:
            continue
        if x["kind"] == "first_goal":
            parts.append((5, "First NHL goal", f"{who}’s first NHL goal"))
        elif x["kind"] == "milestone":
            what = {"game": "game", "goal": "goal", "assist": "assist", "point": "point"}[x["what"]]
            parts.append((6, f"{who}’s {x['mark']:,}th {what}", f"{who}’s {x['mark']:,}th {what}"))
        elif x["kind"] == "club_goal":
            parts.append((4, "First goals for a new club" if (pts.get(x["pid"]) or [0])[0] > 1 else "First goal for a new club", None))
        elif x["kind"] == "debut" and not x.get("goalie"):
            parts.append((2, "NHL debut", None))
    # fights
    pen = penalties.rows(season).filter(pl.col("game_id") == gid) if season else pl.DataFrame()
    stars = {x["pid"]: x for x in storylines.star_fights(gid, season, r["date"])}
    fights = 0
    if not pen.is_empty():
        for w in penalties.whistles(pen):
            fights += w["fights"]
            mine = [pid for pid, _ in w["fighters"] if pid in stars]
            if mine:
                pid = mine[0]
                rare = bool(storylines.rare_fight_words(stars[pid]["record"]))
                others = [p for p, _ in w["fighters"] if p != pid and p]
                text = f"{nm.last(pid)} and {nm.last(others[0])} fought" if w["fights"] == 1 and others else f"{nm.last(pid)} fought"
                parts.append((26 if rare else 18, text, text))
    if fights:
        parts.append((min(8, 4 * fights), "A fight" if fights == 1 else f"{_num(fights).capitalize()} fights", None))
    # brothers on opposite sides
    tonight = nm.P.filter(pl.col("game_id") == gid)
    if stories._brothers(tonight, away, home):
        parts.append((8, "Brothers on opposite sides", "Brothers on opposite sides"))
    # reviews
    rv = storylines.reviews(gid, season)
    if any(x["kind"] == "stood" for x in rv):
        parts.append((4, "Goal stood after a challenge", None))
    if stories._overturned(gid, season):
        parts.append((5, "Goal taken off on review", None))
    parts += _rivals(away, home, season, div)
    surprise = max((x.get("score", 0) for x in r.get("surprises", [])), default=0)
    parts.append((4 * surprise, None, None))
    score, chips, note = _pick(parts)
    st = r.get("story") or {}
    calls = [c for c in r.get("calls", []) if c.get("verdict") in ("held", "partly", "missed")]
    held = sum(c["verdict"] == "held" for c in calls)
    tally = None
    if calls:
        n = len(calls)
        tally = (("The preview call held" if held else "The preview call missed") if n == 1 else
                 ("Both preview calls held" if n == 2 else f"All {_num(n)} preview calls held") if held == n else
                 f"{_num(held).capitalize()} of {_num(n)} preview calls held")
    return {"id": gid, "date": r["date"], "away": away, "home": home, "as": s[away], "hs": s[home], "end": end,
            "venue": r.get("venue"), "score": score, "chips": chips, "note": note, "head": st.get("head"),
            "story": _sentences((st.get("paras") or [""])[0], 3), "calls": tally, "div": div.get(away) == div.get(home)}


# --- coming games ------------------------------------------------------------------------------------------------------

def call_note(c: dict, nm: Namer) -> str | None:
    """A matchup in a few words: "Stars’ PP vs. Avalanche’s kill", "Wilson’s line on Suzuki"."""
    k, t, o, m = c.get("kind"), c.get("team"), c.get("opp"), c.get("metric")
    if k in ("duel", "clash") and m in ("pp", "pk"):
        return _fit(f"{nm.poss(t)} PP vs. {nm.poss(o)} kill", f"{nm.nick(t)} PP vs. {nm.nick(o)} PK")
    if k in ("duel", "clash"):
        what = {"volume": "shots", "quality": "chances", "turnover": "forecheck"}.get(m, CALL_NOUN.get(m, m))
        return _fit(f"{nm.poss(t)} {what} vs. {nm.poss(o)} D", f"{nm.nick(t)} {what} vs. {nm.nick(o)} D")
    if k == "matchup":
        ch = c.get("check") or {}
        mine = [p for p in ch.get("unit_ids", []) if nm.last(p) and nm.last(p) in c.get("head", "")]
        theirs = [p for p in ch.get("opp_ids", []) if nm.last(p) and nm.last(p) in c.get("head", "")]
        if mine and theirs:
            pair = str(ch.get("unit", "")).startswith("P")
            who = f"{nm.last(mine[0])} and {nm.last(mine[1])}" if pair and len(mine) >= 2 else f"{nm.last(mine[0])}’s {'pair' if pair else 'line'}"
            return _fit(f"{who} on {nm.last(theirs[0])}", f"{nm.last(mine[0])}’s {'pair' if pair else 'line'} on {nm.last(theirs[0])}", c.get("head"))
        return _fit(c.get("head"))
    if k == "physical":
        return "Two heavy-hitting teams"
    if k == "pace":
        return _fit(c.get("head"), "Expect a fast game")
    if k == "edge":
        noun = CALL_NOUN.get(m)
        return _fit(f"{nm.poss(t)} {noun}" if noun else None, c.get("head"))
    if k == "rest":
        return _fit(c.get("head"), f"{nm.nick(o)} played last night")
    return _fit(c.get("head"))


CALL_WEIGHT = {"duel": 6, "clash": 4, "matchup": 4, "physical": 3, "pace": 3, "edge": 2, "rest": 2, "history": 1, "changed": 2}


def coming_interest(g: dict, nm: Namer, div: dict[str, str], season: int, star_ids: set[int]) -> dict:
    gid, away, home, day = g["id"], g["away"], g["home"], g["date"]
    pv_file = SITE_DATA / "previews" / f"{gid}.json"
    pv = json.loads(pv_file.read_text()) if pv_file.exists() else {}
    parts: list[tuple[float, str | None, str | None]] = []
    W = nm.W
    try:
        facts = stories.preview_facts(away, home, day, W, season, None, gid)
    except Exception as e:  # a nice-to-have: never lose the page over it
        print(f"home facts for {gid} unavailable: {e!r}")
        facts = {"items": [], "meetings": [], "heads": [], "form": {}}
    cur = stories._current_team(nm.P.filter(pl.col("date") < day), season)
    for i in facts["items"]:
        tag, sc = i["tag"], i.get("score", 0)
        pid = i.get("pid")
        who = nm.last(pid) if pid else ""
        if tag == "Brothers":
            ps = i.get("pids") or []
            same = len(ps) == 2 and nm.last(ps[0]) == nm.last(ps[1])
            parts.append((12, "Brothers face off", f"{who} against {who}" if same else "Brothers face off"))
        elif tag == "In net":
            parts.append((10, "A goaltending question", f"{nm.poss(next((t for t in (away, home) if nm.nick(t) in i['text']), away))} goalie question"))
        elif tag == "Rematch":
            parts.append((14 if sc >= 79 else 10, "Playoff rematch", "Playoff rematch"))
        elif tag == "Old team" and sc >= 80 and pid:  # the first meeting since he left; 85+ = 150+ games with them
            back = cur.get(pid) == away
            parts.append((12 if sc >= 85 else 4, f"{who} faces his old team", f"{who} back {W.at(home)}" if back else f"{who} faces his old team"))
        elif tag == "Playoff race":
            parts.append((8, "Playoff race", None))
        elif tag == "New coach":
            parts.append((6, "New coach", None))
        elif tag == "Milestone":
            parts.append((4, None, None))
    if facts.get("basement"):
        parts.append((10, "Two of the league’s worst", "Somebody has to win"))
    m = facts.get("meetings") or []
    if m:
        last = m[-1]
        if last["date"] == stories._day_before(day):
            parts.append((10, "Rematch from last night", "Rematch from last night"))
        blood = penalties.bad_blood(last["game_id"], season, nm.N)
        if blood:
            sf = [x for x in storylines.star_fights(last["game_id"], season, last["date"]) if nm.last(x["pid"])]
            if sf:
                parts.append((14, f"{nm.last(sf[0]['pid'])} fought last time", f"{nm.last(sf[0]['pid'])} fought last time"))
            else:
                parts.append((blood["score"] / 8, "Bad blood last time", None))
    rec = storylines.table_before(nm.G, season, day)
    if all(t in rec and rec[t]["gp"] >= storylines.BASEMENT_GP and rec[t]["best"] <= 6 for t in (away, home)):
        parts.append((6, "Two of the league’s best", "Two of the league’s best"))
    parts += [x for x in _rivals(away, home, season, div) if x[1] != "Playoff rematch"]  # told by the preview's item
    claims = pv.get("claims") or []
    for k, c in enumerate(claims[:2]):
        parts.append((CALL_WEIGHT.get(c.get("kind"), 2) / (k + 1), None, call_note(c, nm)))
    stars_here = sum(1 for p, t in cur.items() if t in (away, home) and p in star_ids)
    parts.append((min(6, 1.5 * stars_here), None, None))
    score, chips, note = _pick(parts)
    st = pv.get("story") or {}
    paras = st.get("paras") or []
    return {"id": gid, "date": day, "start": g["start"], "away": away, "home": home, "venue": g.get("venue"), "score": score,
            "chips": chips, "note": note, "head": st.get("head"), "story": _sentences(" ".join(paras[:2]), 3) if paras else None,
            "preview": bool(pv), "div": div.get(away) == div.get(home)}


# --- leaders -------------------------------------------------------------------------------------------------------------

def _season_lines(season: int) -> pl.DataFrame:
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet")
    g = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type") == REGULAR)
    abbr = dict(zip(g["home_id"].to_list() + g["away_id"].to_list(), g["home"].to_list() + g["away"].to_list()))
    pg = pg.join(g.select("game_id", "date"), on="game_id")
    return pg.with_columns(a=pl.col("a1") + pl.col("a2"), team=pl.col("team_id").replace_strict(abbr, default=None)).with_columns(
        p=pl.col("g") + pl.col("a"))


def _board(pg: pl.DataFrame, col: str, last_day: str, N: dict[int, str], decimals: int = 0) -> list[dict]:
    team = pg.sort("date").group_by("player_id").agg(pl.col("team").last())
    tot = pg.group_by("player_id").agg(pl.col(col).sum().alias("v"), pl.len().alias("gp"),
                                       pl.col(col).filter(pl.col("date") == last_day).sum().alias("ln")).join(team, on="player_id")
    tot = tot.filter(pl.col("v") > 0).sort(["v", "gp"], descending=[True, False]).head(LEADERS)
    out = []
    for r in tot.iter_rows(named=True):
        v, ln = (round(r["v"], decimals), round(r["ln"], decimals)) if decimals else (int(r["v"]), int(r["ln"]))
        out.append({"id": r["player_id"], "name": N.get(r["player_id"], ""), "team": r["team"], "gp": r["gp"], "v": v, "ln": ln})
    return out


def _goalie_board(last_day: str, recaps: dict[int, dict]) -> list[dict]:
    f = SITE_DATA / "goalies.json"
    if not f.exists():
        return []
    gj = json.loads(f.read_text())
    _, _, N = stories.book()
    night: dict[int, float] = {}
    for r in recaps.values():
        if r.get("date") == last_day:
            for x in r.get("goalies", []):
                if x.get("gsax") is not None:
                    night[x["id"]] = night.get(x["id"], 0) + x["gsax"]
    rows = []
    for team, gs in gj.get("teams", {}).items():
        for x in gs:
            s = x.get("season") or {}
            if s.get("starts"):
                rows.append({"id": x["id"], "name": N.get(x["id"], x["name"]), "team": team, "gp": s["starts"], "v": round(s["gsax"], 1),
                             "ln": round(night[x["id"]], 1) if x["id"] in night else 0})
    rows.sort(key=lambda r: (-r["v"], r["gp"]))
    return rows[:LEADERS]


def leaders(season: int, last_day: str | None, recaps: dict[int, dict]) -> dict:
    _, _, N = stories.book()
    pg = _season_lines(season)
    day = last_day or ""
    return {"p": _board(pg, "p", day, N), "g": _board(pg, "g", day, N), "a": _board(pg, "a", day, N),
            "x": _board(pg, "ixg_net", day, N, 1), "gs": _goalie_board(day, recaps)}


def standout(season: int, last_day: str | None) -> dict | None:
    """The stat whose best single game last night was rarest, measured against every player-game this season and last."""
    if not last_day:
        return None
    _, _, N = stories.book()
    pg = _season_lines(season)
    past = [pg]
    if (TABLES / str(season - 1) / "player_game.parquet").exists():
        past.append(_season_lines(season - 1))
    every = pl.concat([x.select(*[c for c, *_ in STANDOUT.values()]) for x in past], how="vertical_relaxed")
    night = pg.filter(pl.col("date") == last_day)
    if night.is_empty():
        return None
    best = None
    for key, (col, *_rest) in STANDOUT.items():
        v = night[col].max()
        if not v:
            continue
        rarity = (every[col] >= v).mean()
        if best is None or rarity < best[0]:
            best = (rarity, key, v)
    if best is None:
        return None
    _, key, v = best
    col, verb, noun, scorer = STANDOUT[key]
    who = night.filter(pl.col(col) == v).sort("sec", descending=True).row(0, named=True)
    pid = who["player_id"]
    board_all = pg.group_by("player_id").agg(pl.col(col).sum().alias("v"), pl.col(col).filter(pl.col("date") == last_day).sum().alias("ln"))
    board_all = board_all.with_columns(rk=pl.col("v").rank("min", descending=True), prk=(pl.col("v") - pl.col("ln")).rank("min", descending=True))
    me = board_all.filter(pl.col("player_id") == pid).row(0, named=True)
    season_best = pg.filter(pl.col("date") < last_day)[col].max() or 0
    lead = ("The most by anyone in one game this season" if v > season_best else
            "Tied for the most by anyone in one game this season" if v == season_best else
            f"Only {(pg[col] >= v).sum() - 1} other games this season have matched it")
    ord_ = lambda n: f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    rk, prk = int(me["rk"]), int(me["prk"])
    move = (f", moving him from {ord_(prk)} to {ord_(rk)}" if rk < prk and rk <= LEADERS else
            f", and he’s {ord_(rk)} this season" if rk <= LEADERS else "")
    sub = f"{lead}{move}." + (f" {noun.capitalize()} as the home scorer recorded them." if scorer else "")
    n = int(v)
    verb_n = verb.format(n=n)
    return {"stat": key, "noun": noun, "id": pid, "team": who["team"], "head": f"{N.get(pid, '')} {verb_n}", "sub": sub,
            "rows": _board(pg, col, last_day, N)}


# --- run -------------------------------------------------------------------------------------------------------------------

def run(season: int = CURRENT_SEASON) -> dict:
    teams = json.loads((SITE_DATA / "teams.json").read_text())
    places = {t["abbr"]: t["place"] for t in teams}
    div = {t["abbr"]: t.get("division") for t in teams}
    nm = Namer(places)
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    recaps = {}
    for p in (SITE_DATA / "recaps").glob("2*.json"):
        r = json.loads(p.read_text())
        recaps[r["game_id"]] = r
    finals = sorted({g["date"] for g in sched if g.get("final")})
    days = finals[-2:]
    results = []
    for d in days:
        games = [g for g in sched if g["date"] == d]
        rows = []
        for g in games:
            r = recaps.get(g["id"])
            if r and g.get("final") and not r.get("pending"):
                try:
                    rows.append(result_interest(r, nm, div, season))
                except Exception as e:  # never lose the page over one game
                    print(f"home interest for {g['id']} failed: {e!r}")
            elif g.get("final"):
                rows.append({"id": g["id"], "date": d, "away": g["away"], "home": g["home"], "as": g["as"], "hs": g["hs"],
                             "end": g.get("end"), "venue": g.get("venue"), "score": 0, "chips": [], "note": None,
                             "pending": bool(r and r.get("pending")), "div": div.get(g["away"]) == div.get(g["home"])})
        results.append({"date": d, "games": len(games), "rows": sorted(rows, key=lambda x: -x["score"])})
    today = datetime.now(timezone.utc).date()
    ahead = sorted({g["date"] for g in sched if not g.get("final") and g["date"] >= (today.fromordinal(today.toordinal() - 1)).isoformat()})[:UPCOMING_DAYS]
    from pipeline.metrics import relevancy
    try:
        star_ids = set(relevancy.standouts(season, today.isoformat()))
    except Exception:
        star_ids = set()
    coming = []
    for d in ahead:
        rows = []
        for g in (g for g in sched if g["date"] == d and not g.get("final")):
            try:
                rows.append(coming_interest(g, nm, div, season, star_ids))
            except Exception as e:
                print(f"home interest for {g['id']} failed: {e!r}")
        coming.append({"date": d, "games": sum(1 for g in sched if g["date"] == d), "rows": sorted(rows, key=lambda x: -x["score"])})
    last_day = finals[-1] if finals else None
    latest = {}
    for r in sorted(recaps.values(), key=lambda r: r["game_id"]):
        head = (r.get("story") or {}).get("head")
        for t in (r["away"], r["home"]):
            latest[t] = {"id": r["game_id"], "head": head}
    out = {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": results, "coming": coming,
           "leaders": leaders(season, last_day, recaps), "standout": standout(season, last_day), "latest": latest}
    (SITE_DATA / "home.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return {"results": sum(len(x["rows"]) for x in results), "coming": sum(len(x["rows"]) for x in coming)}


if __name__ == "__main__":
    print(run())
