"""Plain-English intros for team and player pages.

A few sentences at the top of each page, numbers-light, saying who a team or player is by our numbers, whether they
are good, whether they have been good, and (for players) where they have played and any storylines for the next game:
a first game back against a former team, a run of points against the next opponent, a career record against them.

Everything is rule-based from data we already hold or fetch (NHL career game logs in `ingest/careers.py`). Wording
follows the site's voice: teams are "they" with nicknames, no numbers in the first sentence, ranks in words.
"""
from __future__ import annotations

import json
import zlib
from datetime import date

import polars as pl

from pipeline.config import CURRENT_SEASON, PLAYOFFS, REGULAR, SITE_DATA, TABLES
from pipeline.metrics import adjust

HISTORY = 4  # seasons of team history to describe (we hold full tables from 2022-23)


# ---------------------------------------------------------------- helpers

def _pick(pool: list[str], *key) -> str:
    """Stable choice from a wording pool (same text every run, varied across teams and players)."""
    return pool[zlib.crc32("|".join(map(str, key)).encode()) % len(pool)]


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:] if t else t


def _and(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _ordinal(n: int) -> str:
    s = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{s}"


WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def _num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _season_name(s: int) -> str:
    return f"{s}-{str(s + 1)[-2:]}"


def _teams() -> dict[str, dict]:
    return {t["abbr"]: t for t in json.loads((SITE_DATA / "teams.json").read_text())}


def _nick(t: dict) -> str:
    return t.get("nick") or t["place"]


def _the(t: dict) -> str:
    return f"the {_nick(t)}"


def _rank_words(rank: int, n: int = 32) -> str:
    """A league rank in words, never a bare "14th of 32"."""
    if rank == 1:
        return "the best in the league"
    if rank <= 3:
        return "as good as anyone in the league"
    if rank <= 8:
        return "among the league's best"
    if rank <= n // 2:
        return "a bit better than average"
    if rank <= n - 8:
        return "a bit worse than average"
    if rank <= n - 3:
        return "among the league's worst"
    return "as bad as anyone in the league"


# ---------------------------------------------------------------- team history

def _results(season: int) -> pl.DataFrame:
    """One row per team per game: points earned, goals, regular season or playoffs."""
    g = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("state").is_in(["OFF", "FINAL"]))
    rows = []
    for r in g.iter_rows(named=True):
        extra = r["last_period"] in ("OT", "SO")
        for side, other in (("home", "away"), ("away", "home")):
            gf, ga = r[f"{side}_score"], r[f"{other}_score"]
            rows.append({"game_id": r["game_id"], "date": r["date"], "type": r["game_type"], "team": r[side], "opp": r[other],
                         "home": side == "home", "gf": gf, "ga": ga, "win": gf > ga, "otl": gf < ga and extra})
    return pl.DataFrame(rows)


def _playoff_round(res: pl.DataFrame, team: str) -> int:
    """0 = missed, 1-4 = the round they went out in, 5 = won the Cup."""
    p = res.filter((pl.col("type") == PLAYOFFS) & (pl.col("team") == team))
    if p.is_empty():
        return 0
    last = p.sort("game_id")[-1]
    rnd = int(str(last["game_id"][0])[-3])
    series = p.filter(pl.col("game_id").cast(pl.Utf8).str.slice(0, 9) == str(last["game_id"][0])[:9])
    return 5 if rnd == 4 and series["win"].sum() == 4 else rnd


def team_history(seasons: list[int]) -> dict[str, list[dict]]:
    """Per team, per past season: points rank, points share, playoff result."""
    out: dict[str, list[dict]] = {}
    for s in seasons:
        res = _results(s)
        reg = res.filter(pl.col("type") == REGULAR).group_by("team").agg(
            gp=pl.len(), pts=(2 * pl.col("win").cast(pl.Int32) + pl.col("otl").cast(pl.Int32)).sum(),
            gd=(pl.col("gf") - pl.col("ga")).sum()).sort(["pts", "gd"], descending=True).with_row_index("rank", offset=1)
        for r in reg.iter_rows(named=True):
            out.setdefault(r["team"], []).append({"season": s, "rank": r["rank"], "pts": r["pts"], "gp": r["gp"],
                                                  "playoffs": _playoff_round(res, r["team"])})
    return out


def xg_share(season: int) -> dict[str, dict]:
    """5v5 score-and-venue adjusted xG share this season, per team, plus games with the better of the chances."""
    tg = pl.read_parquet(TABLES / str(season) / "team_game.parquet").filter(pl.col("strength") == "5v5")
    t = adjust.apply(tg, adjust.weights())
    games = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type") == REGULAR)
    abbr = dict(zip(games["home_id"], games["home"])) | dict(zip(games["away_id"], games["away"]))
    per = t.filter(pl.col("game_id").is_in(games["game_id"].to_list())).group_by("game_id", "team_id").agg(
        pl.col("xgf_adj").sum(), pl.col("xga_adj").sum())
    tot = per.group_by("team_id").agg(pl.col("xgf_adj").sum(), pl.col("xga_adj").sum(), gp=pl.len(),
                                      better=(pl.col("xgf_adj") > pl.col("xga_adj")).sum())
    tot = tot.with_columns(share=pl.col("xgf_adj") / (pl.col("xgf_adj") + pl.col("xga_adj"))).sort("share", descending=True).with_row_index("rank", offset=1)
    return {abbr[r["team_id"]]: {"share": r["share"], "rank": r["rank"], "gp": r["gp"], "better": r["better"]} for r in tot.iter_rows(named=True)}


# ---------------------------------------------------------------- team identity

# How a fingerprint extreme reads in a sentence: (top-of-league phrase, bottom-of-league phrase). Verb phrases after
# "The Hurricanes ...". None = that end says nothing interesting (e.g. low "second chances").
STYLE = {
    "volume": ("fire from everywhere", "pick their spots rather than pile up shots"),
    "quality": ("get to the dangerous chances", "settle for a lot of hopeful shots"),
    "rebounds": ("crash the net for second chances", None),
    "turnover": ("pounce on turnovers", None),
    "dShots": ("send their defensemen in to shoot", "leave most of the shooting to the forwards"),
    "inClose": ("get right to the net", "shoot from distance"),
    "suppression": ("give up very few shots", "let the other team shoot a lot"),
    "qualityAllowed": ("keep the other team to hopeful shots", "give up dangerous chances"),
    "breakdowns": ("rarely get burned after losing the puck", "get caught after turnovers too often"),
    "pace": ("play fast, wide-open games", "grind games down"),
    "forecheck": ("hound the puck in the other team's end", "sit back rather than forecheck"),
    "physical": ("hit everything that moves", "play a finesse game"),
    "depth": ("roll all four lines", "lean hard on their top players"),
}
# Two traits that read better as one thought: (trait, end, trait, end) -> phrase.
PAIRS = {
    ("volume", "hi", "quality", "lo"): "fire from everywhere, though a lot of it is hopeful",
    ("volume", "lo", "quality", "hi"): "pick their spots, but get to the dangerous chances when they shoot",
    ("suppression", "hi", "qualityAllowed", "lo"): "give up very few shots, though the ones they allow are dangerous",
    ("suppression", "lo", "qualityAllowed", "hi"): "let the other team shoot, but mostly hopeful shots",
    ("volume", "hi", "suppression", "hi"): "spend most of the night in the other team's end",
    ("volume", "lo", "suppression", "lo"): "spend a lot of the night in their own end",
}
# Special teams, goaltending and discipline: a clause of their own, (good, bad).
EXTRA = {
    "pp": ("their power play is lethal", "their power play has no bite"),
    "pk": ("their penalty kill is airtight", "their penalty kill leaks"),
    "goalie": ("their goaltending has been stealing games", "their goaltending has let them down"),
    "powerKill": ("they create chances even when shorthanded", None),
    "discipline": ("they stay out of the box", "they spend a lot of time in the box"),
}
OPPOSED = [{"volume", "pace"}, {"suppression", "pace"}, {"quality", "inClose"}, {"forecheck", "physical"}]


def _extremes(fp: dict, keys, cut: int) -> list[tuple[str, str, int]]:
    """(trait, "hi"/"lo", distance from the middle) for traits at or beyond the cut, most extreme first."""
    out = []
    for k in keys:
        e = (fp["dims"].get(k) or {}).get("blend")
        if e and e["pct"] >= 100 - cut:
            out.append((k, "hi", e["pct"] - 50))
        elif e and e["pct"] <= cut:
            out.append((k, "lo", 50 - e["pct"]))
    return sorted(out, key=lambda x: -x[2])


def team_style(fp: dict) -> tuple[list[str], list[str]]:
    """Phrases for the team's two most extreme 5-on-5 traits (pairs read as one thought), and the clauses for special
    teams and goaltending at the far ends. Returns (phrases, trait keys used)."""
    ext = [(k, end, d) for k, end, d in _extremes(fp, STYLE, 20) if STYLE[k][0 if end == "hi" else 1]]
    phrases, used = [], []
    for k, end, _ in ext:
        if k in used or any({k, u} <= pair for u in used for pair in OPPOSED):
            continue
        pair = next(((k2, e2) for k2, e2, _ in ext if k2 not in used and k2 != k
                     and ((k, end, k2, e2) in PAIRS or (k2, e2, k, end) in PAIRS)), None)
        if pair:
            phrases.append(PAIRS.get((k, end, *pair)) or PAIRS[(*pair, k, end)])
            used += [k, pair[0]]
        else:
            phrases.append(STYLE[k][0 if end == "hi" else 1])
            used.append(k)
        if len(phrases) == 2:
            break
    return phrases, used


def team_extras(fp: dict) -> list[tuple[str, str]]:
    """Special teams, goaltending and discipline at the far ends: (trait, "hi"/"lo"), at most two."""
    return [(k, end) for k, end, _ in _extremes(fp, EXTRA, 15) if EXTRA[k][0 if end == "hi" else 1]][:2]


def _record(t: dict) -> str:
    return f"{t['w']}-{t['l']}-{t['otl']}"


def _run_streak(recent: list[dict]) -> tuple[int, bool]:
    """Current run of wins or losses: (games, won)."""
    if not recent:
        return 0, False
    won = recent[-1]["win"]
    n = 0
    for r in reversed(recent):
        if r["win"] != won:
            break
        n += 1
    return n, won


# Voice (user, 2026-10-10: the first drafts read "stiff"): written the way a fan would tell a friend about a team.
# Lead with a verdict that ties the past to the present, then whether the play backs up the record, then how they
# play, then special teams. Vary the openings; no two sentences start the same way.
def team_intro(abbr: str, t: dict, fp: dict, xg: dict, hist: list[dict], recent: list[dict]) -> dict:
    X, nick = _the(t), _nick(t)
    key = (abbr,)
    past = sorted(hist, key=lambda h: h["season"])[-HISTORY:]
    last = past[-1] if past else None
    made = sum(1 for h in past if h["playoffs"])
    dry = 0
    for h in reversed(past):
        if h["playoffs"]:
            break
        dry += 1
    gp, x = t["gp"], xg.get(abbr)
    pts_pct = t["pts"] / (2 * gp) if gp else 0.5
    rec = "good" if pts_pct >= 0.6 else "bad" if pts_pct < 0.45 else "even"
    play = None if not x else "good" if x["share"] >= 0.53 else "bad" if x["share"] <= 0.47 else "even"
    run, won = _run_streak(recent)
    record = _record(t)
    out = []

    # 1. The hook: last season and this one in a breath.
    if not gp:
        hook = f"{_cap(X)} haven't played yet this season."
    elif last and last["playoffs"] == 5:
        hook = {"good": f"The defending champs are picking up right where they left off, {record} out of the gate.",
                "even": f"The defending champs have had a so-so start at {record}.",
                "bad": f"Life as defending champs has started slowly: they're {record}."}[rec]
    elif last and last["playoffs"] == 4:
        hook = {"good": f"Last spring's runners-up look hungry: they're {record}.",
                "even": f"Last spring's runners-up are {record}, still finding their feet.",
                "bad": f"There's no Final hangover quite like this one: last spring's runners-up are {record}."}[rec]
    elif dry >= 3:
        hook = {"good": f"Could this be the year? {_cap(X)} haven't made the playoffs in {_num(dry)} seasons, but they're {record} so far.",
                "even": f"{_cap(X)} are {record}, still looking for their first playoff spot in {_num(dry)} seasons.",
                "bad": f"The rebuild grinds on in {t['place']}: no playoffs in {_num(dry)} seasons, and a {record} start."}[rec]
    elif last and not last["playoffs"] and made:
        hook = {"good": f"After missing the playoffs last spring, {X} have bounced back to {record}.",
                "even": f"After missing the playoffs last spring, {X} are {record}.",
                "bad": f"Last season was one to forget in {t['place']}, and this one hasn't started much better: they're {record}."}[rec]
    elif made == len(past) and past:
        hook = {"good": _pick([f"Same old {nick}: {record}, and in the mix again.", f"{_cap(X)} are {record}, which is what we've come to expect."], *key),
                "even": f"{_cap(X)}, playoff regulars, are {record} so far.",
                "bad": f"{_cap(X)} have made the playoffs {_num(len(past))} years running, but they're {record} this time."}[rec]
    else:
        hook = f"{_cap(X)} are {record} so far."
    if run >= 3 and gp:
        hook = hook[:-1] + f", {'winners' if won else 'losers'} of {_num(run)} straight."
    out.append(hook)

    # 2. Does the play back it up?
    if x and gp:
        n, k = x["gp"], x["better"]
        most = "most" if k * 2 > n else "half" if k * 2 == n else "few"
        say = {
            ("good", "good"): _pick(["And it's no mirage: they've had the better of the chances in {c}.", "They've earned it, too, with the better of the chances in {c}."], *key, "g").format(c=f"all {_num(n)} games" if k == n else f"{_num(k)} of their {_num(n)} games"),
            ("good", "even"): "The play has been closer than the record, though.",
            ("good", "bad"): "Don't get too comfortable, though: they've been outchanced in most of those games, and that tends to catch up with a team.",
            ("even", "good"): "They've played better than that record, too, winning the battle for chances more often than not.",
            ("even", "even"): "That's about how they've played, too: some nights on top, some not.",
            ("even", "bad"): "If anything the record is kind: they've been outchanced more often than not.",
            ("bad", "good"): "The results haven't come yet, but they've had the better of the chances most nights, so don't write them off.",
            ("bad", "even"): "They've played better than that, though, so a bit of luck could turn it around.",
            ("bad", "bad"): "And honestly, they haven't played much better than that.",
        }[(rec, play)]
        if most == "few" and play == "bad" and rec != "bad" and k == 0:
            say = say.replace("in most of those games", "in every one of those games")
        if gp < 10 and "though" not in say and "write them off" not in say:
            say += " " + _pick(["Early days, of course.", "It's early, mind you.", "Small sample, sure."], *key, "early")
        out.append(say)

    # 3. How they play.
    phrases, used = team_style(fp)
    if phrases:
        frame = _pick(["Expect them to {p}.", "Night to night, they {p}.", "On the ice, they {p}.", "Their game? They {p}."], *key, "style")
        out.append(frame.format(p=_and(phrases)))
    else:
        out.append("Stylewise there's nothing extreme about them: a middle-of-the-road team in almost every way.")

    # 4. Special teams and goaltending, only at the far ends.
    ext = team_extras(fp)
    if ext:
        cl = [EXTRA[k][0 if end == "hi" else 1] for k, end in ext]
        joint = " and " if len({end for _, end in ext}) == 1 else ", but "
        out.append(_cap(joint.join(cl)) + ".")
    return {"team": abbr, "name": t["name"], "text": out, "style": used + [k for k, _ in ext]}


# ---------------------------------------------------------------- players

POS_NOUN = {"C": "center", "L": "winger", "R": "winger", "D": "defenseman"}
LINE_WORD = {"L1": "top", "L2": "second", "L3": "third", "L4": "fourth", "P1": "top", "P2": "second", "P3": "third"}
# Standout skills (blend percentile 85+ among his peers, likely range clear of the middle). Base verbs after "he can"
# would read oddly, so these complete "He ..." in the third person.
SKILL = {
    "offImpact": "tilts the ice when he's out there",
    "defImpact": "shuts down the other team's chances",
    "chances": "gets to dangerous scoring chances",
    "shooting": "shoots at every opportunity",
    "finishing": "finishes better than most",
    "playmaking": "sets up a lot of goals",
    "powerPlay": "is a real threat on the power play",
    "shThreat": "stays dangerous even while killing penalties",
    "hits": "plays a heavy, physical game",
    "blocks": "throws himself in front of shots",
    "takeaways": "picks a lot of pockets",
    "drawsPenalties": "draws a lot of penalties",
    "faceoffs": "wins his draws",
}


def _skills(p: dict) -> list[str]:
    found = []
    for k in SKILL:
        e = (p.get("traits", {}).get(k) or {}).get("blend")
        if e and e.get("ok") and e["pct"] >= 85 and e.get("lo", 0) >= 60:
            found.append((k, e["pct"]))
    found.sort(key=lambda kv: -kv[1])
    return [SKILL[k] for k, _ in found[:2]]


def _role(p: dict, teams: dict) -> str | None:
    """"He centers Carolina's second line, plays on the top power-play unit and kills penalties"."""
    role = p.get("role") or {}
    place = teams.get(p["team"], {"place": p["team"]})["place"]
    line = (role.get("line") or {}).get("label")
    bits = []
    if line in LINE_WORD:
        if p["pos"] == "D":
            bits.append(f"anchors {place}'s {LINE_WORD[line]} pair" if line == "P1" else f"plays on {place}'s {LINE_WORD[line]} pair")
        elif p["pos"] == "C":
            bits.append(f"centers {place}'s {LINE_WORD[line]} line")
        else:
            bits.append(f"plays on {place}'s {LINE_WORD[line]} line")
    pp = role.get("pp") or {}
    if pp.get("label") == "PP1":
        bits.append("runs the top power play" if pp.get("role") == "Quarterback" else "plays on the top power-play unit")
    elif pp.get("label") == "PP2":
        bits.append("plays on the second power-play unit")
    pk = (role.get("pk") or {}).get("role")
    if pk in ("starter", "second"):
        bits.append("kills penalties")
    if not bits:
        return None
    s = "He " + _and(bits)
    if role.get("toi_rank") == 1 and role.get("gp", 0) >= 2:
        s += f", and no {teams.get(p['team'], {}).get('nick', '')} {'defenseman' if p['pos'] == 'D' else 'forward'} plays more"
    return s + "."


def _nth(n: int) -> str:
    return ["zeroth", "first", "second", "third", "fourth", "fifth"][n] if n <= 5 else _ordinal(n)


def _usual_ppg(seasons: list[dict]) -> float | None:
    full = _full(seasons)[-3:]
    return sum(x["p"] for x in full) / sum(x["gp"] for x in full) if full else None


def _label(p: dict, ppg: float | None) -> str | None:
    if ppg is None:
        return None
    if p["pos"] == "D":
        return "elite" if ppg >= 0.7 else None
    return "elite" if ppg >= 1.25 else "ppg" if ppg >= 0.95 else None


# Voice (user, 2026-10-10: "stiff"): lead with a hook (his start, a new team, or what he is), then fold role, skills
# and track record into a few varied sentences rather than one fact per line.
def player_intro(p: dict, career: dict | None, teams: dict, next_game: dict | None, today: str,
                 opp_players: list[dict] | None = None) -> dict:
    last = p["last"]
    key = (p["id"],)
    team = teams.get(p["team"], {"place": p["team"], "nick": p["team"]})
    seasons = _nhl_seasons(career["landing"]) if career else []
    games = _games(career["logs"]) if career else []
    stints = _stints(games) if career else []
    full = _full(seasons)
    usual = _usual_ppg(seasons)
    label = _label(p, usual)
    s = (p.get("stats") or {}).get("season") or {}
    gp, pts = s.get("gp", 0), s.get("p", 0)
    cur = [g for g in games if g["type"] == REGULAR and g["gameId"] // 1_000_000 == CURRENT_SEASON]
    gn, _ = _streak(cur, "goals")
    pn, _ = _streak(cur, "points")
    hot = usual is not None and usual >= 0.3 and gp >= 3 and pts / gp >= 1.4 * usual and pts >= 4
    cold = usual is not None and usual >= 0.5 and gp >= 4 and pts / gp <= 0.4 * usual
    new = None
    if len(stints) >= 2 and stints[-1]["team"] == p["team"] and (date.fromisoformat(today) - date.fromisoformat(stints[-1]["first"])).days <= 200:
        prev = stints[-2]
        pt = teams.get(prev["team"], {"place": prev["team"], "nick": prev["team"]})
        new = {"years": _seasons_span(prev["first"], prev["last"]), "place": pt["place"], "nick": _nick(pt)}
    out = []

    # 1. Hook.
    run = f", with goals in {_num(gn)} straight" if gn >= 3 else f", with a point in {_num(pn)} straight" if pn >= 4 else ""
    if new:
        were = f"{_num(new['years'])} seasons with the {new['nick']}" if new["years"] >= 3 else f"a stop in {new['place']}"
        if hot:
            out.append(f"{last} has wasted no time in {team['place']}: after {were}, he's got {pts} points in {_num(gp)} games{run}.")
        else:
            out.append(f"{last} is the new face in {team['place']}, after {were}.")
    elif hot and label == "elite":
        out.append(_pick([f"{last} is doing {last} things again: {pts} points in {_num(gp)} games{run}.",
                          f"Same {last}, different season: {pts} points in {_num(gp)} games{run}."], *key))
    elif hot:
        out.append(f"{last} has come out flying: {pts} points in {_num(gp)} games{run}.")
    elif cold:
        out.append(f"It's been a quiet start for {last}: {_num(pts)} point{'s' if pts != 1 else ''} in {_num(gp)} games.")
    elif label and len(full) < 3:
        what = "one of the league's best" if label == "elite" else "a point-a-game player"
        out.append(f"{last} is only in his {_nth(len(full) + 1)} season and already {what}.")
    elif label == "elite":
        out.append(f"{last} is one of the best {'defensemen' if p['pos'] == 'D' else 'players'} in the league, and {team['place']} knows it.")
    else:
        out.append(None)

    # 2. Role, in one sentence (it becomes the opener if there was no hook).
    role = _role(p, teams)
    if out[0] is None:
        out = [role.replace("He ", f"{last} ", 1) if role else f"{last} is a {POS_NOUN.get(p['pos'], 'player')} for {team['place']}."]
    elif role:
        out.append(role)

    # 3. What sets him apart.
    sk = _skills(p)
    if sk:
        out.append(_pick(["What sets him apart: he {s}.", "At his best, he {s}.", "His calling card? He {s}."], *key, "sk").format(s=_and(sk)))

    # 4. Track record.
    if full:
        lp = full[-1]["p"]
        goals30 = sum(1 for x in full if x["gp"] >= 60 and x["g"] * 82 / x["gp"] >= 30)
        years = len(seasons) - (seasons[-1]["season"] // 10000 == CURRENT_SEASON)
        if label and len(_full(seasons)) >= 3:
            more = f", and he's scored 30 goals {_num(goals30)} times" if goals30 >= 2 and p["pos"] != "D" else ""
            out.append(_pick(["None of this is new: he had {lp} points last season{m}.", "He's been doing this for years: {lp} points last season{m}."], *key, "cr").format(lp=lp, m=more))
        elif label:
            out.append(f"He had {lp} points last season.")
        elif lp >= 45 or (p["pos"] == "D" and lp >= 35):
            out.append(f"He had {lp} points last season.")

    stories = []
    if career and next_game:
        p = {**p, "draft_team": (career["landing"].get("draftDetails") or {}).get("teamAbbrev")}
        stories = opponent_notes(p, games, stints, next_game["opp"], teams, next_game["date"] == today)
        stories += reunions(stints, games, opp_players or [])
    return {"id": p["id"], "text": [x for x in out if x], "next": stories}


def opponent_notes(p: dict, games: list[dict], stints: list[dict], opp: str, teams: dict, tonight: bool) -> list[str]:
    """Storylines for his next game, against `opp`: a first game back, a run against them, a career record."""
    out = []
    o = teams.get(opp, {"place": opp, "nick": opp})
    O = f"the {_nick(o)}"
    when = "tonight" if tonight else "next time out"
    reg = [g for g in games if g["type"] == REGULAR]
    vs = [g for g in reg if g["opponentAbbrev"] == opp and g["teamAbbrev"] != opp]
    former = [s for s in stints[:-1] if s["team"] == opp]
    if former:
        since = [g for g in vs if g["gameDate"] > former[-1]["last"]]
        spell = sum(_seasons_span(s["first"], s["last"]) for s in former)
        drafted = p.get("draft_team") == opp
        if not since:
            out.append(f"This one's personal. {p['last']} spent {_num(spell)} season{'s' if spell != 1 else ''} in {o['place']}"
                       f"{', the team that drafted him,' if drafted else ''} and faces {O} {when} for the first time since leaving.")
        else:
            out.append(f"{_cap(when)} is a date with his old team: he spent {_num(spell)} season{'s' if spell != 1 else ''} with {O}.")
    if len(vs) >= 3:
        gn, _ = _streak(vs, "goals")
        pn, pts = _streak(vs, "points")
        total = sum(g["points"] for g in vs)
        usual = sum(g["points"] for g in reg) / len(reg)
        if gn >= 3:
            out.append(f"{_cap(O)} know him well: he's scored in {_num(gn)} straight games against them.")
        elif pn >= 5:
            out.append(_pick([f"{_cap(O)} can't seem to keep him off the scoresheet: he's had a point in {_num(pn)} straight games against them, {pts} points in all.",
                              f"He loves seeing {O}: a point in {_num(pn)} straight meetings, {pts} points in that run."], p["id"], opp))
        elif len(vs) >= 10 and usual >= 0.4 and total / len(vs) >= 1.3 * usual:
            out.append(f"He's always enjoyed playing {O}: {total} points in {len(vs)} career games against them.")
        elif len(vs) >= 10 and usual >= 0.6 and total / len(vs) <= 0.6 * usual:
            out.append(f"{_cap(O)} have had his number over the years: just {total} points in {len(vs)} career games.")
    return out


def reunions(stints: list[dict], games: list[dict], opp_players: list[dict]) -> list[str]:
    """A former long-time teammate (three or more seasons on the same club) on the other side for the first time."""
    for q in opp_players:
        now = q["stints"][-1]
        if any(g["opponentAbbrev"] == now["team"] and g["gameDate"] >= now["first"] for g in games):
            continue  # they have met since he moved
        for a in stints:
            for b in q["stints"][:-1]:
                if a["team"] != b["team"]:
                    continue
                lo, hi = max(a["first"], b["first"]), min(a["last"], b["last"])
                if hi >= lo and _seasons_span(lo, hi) >= 3:
                    return [f"He'll also see an old friend: {q['name']}, his teammate for {_num(_seasons_span(lo, hi))} seasons, is on the other side for the first time."]
    return []


def _nhl_seasons(career: dict) -> list[dict]:
    """Regular-season NHL rows from the landing response, merged per season (a traded player has one row per team)."""
    rows: dict[int, dict] = {}
    for s in career.get("seasonTotals", []):
        if s.get("leagueAbbrev") != "NHL" or s.get("gameTypeId") != REGULAR:
            continue
        r = rows.setdefault(s["season"], {"season": s["season"], "gp": 0, "g": 0, "a": 0, "p": 0})
        for k, src in (("gp", "gamesPlayed"), ("g", "goals"), ("a", "assists"), ("p", "points")):
            r[k] += s.get(src) or 0
    return [rows[k] for k in sorted(rows)]


def _games(logs: dict) -> list[dict]:
    """Every NHL game from the career logs, oldest first."""
    out = [{**g, "type": int(key.split("-")[1])} for key, gl in logs.items() for g in gl]
    return sorted(out, key=lambda g: (g["gameDate"], g["gameId"]))


def _stints(games: list[dict]) -> list[dict]:
    """Spells with each club, in order: team, first and last game date, regular-season games."""
    out: list[dict] = []
    for g in games:
        if out and out[-1]["team"] == g["teamAbbrev"]:
            out[-1]["last"] = g["gameDate"]
            out[-1]["gp"] += g["type"] == REGULAR
        else:
            out.append({"team": g["teamAbbrev"], "first": g["gameDate"], "last": g["gameDate"], "gp": int(g["type"] == REGULAR)})
    return out


def _season_of(d: str) -> int:
    y, m = int(d[:4]), int(d[5:7])
    return y if m >= 8 else y - 1


def _seasons_span(first: str, last: str) -> int:
    return _season_of(last) - _season_of(first) + 1


def _full(seasons: list[dict]) -> list[dict]:
    return [s for s in seasons if s["season"] // 10000 < CURRENT_SEASON and s["gp"] >= 40]




def _streak(games: list[dict], key: str) -> tuple[int, int]:
    """Games in a row (most recent first) with at least one `key`, and the points in that run."""
    n = pts = 0
    for g in reversed(games):
        if g[key] > 0:
            n += 1
            pts += g["points"]
        else:
            break
    return n, pts




# ---------------------------------------------------------------- export

def _careers() -> dict[int, dict]:
    from pipeline.ingest import careers
    return careers.load_all()


def run(today: str | None = None) -> dict:
    """Write intros/teams.json (all 32) and intros/players.json (everyone with a player page)."""
    today = today or date.today().isoformat()
    teams = _teams()
    fp = json.loads((SITE_DATA / "fingerprints.json").read_text())["teams"]
    xg = xg_share(CURRENT_SEASON)
    hist = team_history(list(range(CURRENT_SEASON - HISTORY, CURRENT_SEASON)))
    res = _results(CURRENT_SEASON).filter(pl.col("type") == REGULAR).sort("game_id")
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    nxt: dict[str, dict] = {}
    for g in sched:
        if not g["final"] and g["date"] >= today:
            for t, o in ((g["home"], g["away"]), (g["away"], g["home"])):
                nxt.setdefault(t, {"date": g["date"], "opp": o, "id": g["id"], "home": t == g["home"]})
    team_out = {a: team_intro(a, teams[a], fp[a], xg, hist.get(a, []), res.filter(pl.col("team") == a).to_dicts())
                for a in teams if a in fp}

    careers = _careers()
    index = json.loads((SITE_DATA / "players" / "index.json").read_text())
    pages = {}
    for row in index:
        f = SITE_DATA / "players" / f"{row['id']}.json"
        if f.exists():
            pages[row["id"]] = json.loads(f.read_text())
    stints = {pid: _stints(_games(c["logs"])) for pid, c in careers.items()}
    by_team: dict[str, list[dict]] = {}
    for pid, p in pages.items():
        if pid in stints:
            by_team.setdefault(p["team"], []).append({"name": p["name"], "stints": stints[pid]})
    player_out = {}
    for pid, p in pages.items():
        if p.get("pos") == "G" or "traits" not in p:
            continue
        n = nxt.get(p["team"])
        player_out[pid] = player_intro(p, careers.get(pid), teams, n, today, by_team.get(n["opp"], []) if n else [])
        if n:
            player_out[pid]["game"] = n
    curly = lambda xs: [x.replace("'", "’") for x in xs]
    for v in team_out.values():
        v["text"] = curly(v["text"])
    for v in player_out.values():
        v["text"], v["next"] = curly(v["text"]), curly(v["next"])
    out = SITE_DATA / "intros"
    out.mkdir(parents=True, exist_ok=True)
    (out / "teams.json").write_text(json.dumps({"generated": today, "teams": team_out}, separators=(",", ":")))
    (out / "players.json").write_text(json.dumps({"generated": today, "players": player_out}, separators=(",", ":")))
    return {"teams": len(team_out), "players": len(player_out), "with_career": sum(1 for k in player_out if k in careers)}
