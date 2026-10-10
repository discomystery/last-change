"""Preview snapshots: the "What to watch" calls for every game in the next week.

Every call is a prediction the post-game page grades by rule: a headline and body for reading, a `call` sentence that
says exactly what is predicted, and a `check` (metric, team, baseline, threshold) the recap measures. The rulebook:

  clash     a team's strong trait meets the opponent's weak one: it reaches its usual level tonight
  pace      both teams play fast (or both slow): the game runs faster (or slower) than their usual
  physical  both teams hit a lot: the combined hit score reaches their usual
  matchup   the home team's strongest home matching habit: it gets that matchup again
  edge      the trait where the two sit furthest apart: the team at the better end comes out ahead tonight

Triggers and wording are fixed rules (no free text), picked deterministically from the game id, so a preview can be
generated for any game from the numbers alone. RULES_VERSION goes up whenever the rulebook changes; previews rebuilt
after the fact are regenerated under the new rules, while live previews stay exactly as saved at puck drop. A game's file is rewritten on every run until puck drop, then frozen: once the game has started the
file is never touched again, so the recap grades exactly what the preview said.

Reads the site JSON the other exports have just written (fingerprints, lines), so it must run after them.
"""
import json
import zlib
from functools import lru_cache
from datetime import datetime, timedelta, timezone

from pipeline.config import SITE_DATA

DAYS_AHEAD = 7
RULES_VERSION = 5  # 1: the first rulebook, with ungraded "contrast" notes; 2: every call graded (edge replaces contrast);
# 3: calls picked for interest (duels, changed teams, matchup plans, goalie records), casual voice, pace bar raised
# 4: shot danger told as a chance per shot; point shots split into shots from defensemen and shots from in close
# 5: bodies are numbers-free (ranks in words); the figures move to `nums`, shown under the call; `gist` feeds the story
# (offense trait, defense trait it runs into, what the offense does, what the defense does)
CLASHES = [
    ("volume", "suppression", "shot volume", "shot suppression"),
    ("quality", "qualityAllowed", "shot quality", "preventing dangerous shots"),
    ("turnover", "breakdowns", "chances off turnovers", "avoiding breakdowns"),
    ("pp", "pk", "power play", "penalty kill"),
]
def a_pct(v: float) -> str:
    """A chance written with its article: "a 6.3%", "an 8.1%", "an 11.0%"."""
    s = f"{100 * v:.1f}%"
    return ("an " if s[0] == "8" or s.split(".")[0] in ("11", "18") else "a ") + s


STRONG, WEAK = 70, 35  # league percentiles (higher is better) for a "meets a soft spot" call
# What the recap will measure for each trait, in the game itself.
MEASURE = {
    "volume": "5-on-5 shot attempts per 60, adjusted for score and venue",
    "quality": "the average shot’s chance of going in, by expected goals",
    "turnover": "share of 5-on-5 expected goals within 5 seconds of winning the puck",
    "pp": "expected goals per 60 on the power play",
}
# The prediction itself, in plain words, for the team named first: the recap checks exactly this.
CLASH_CALL = {  # w: the attacking team's words (see `team_words`)
    "volume": lambda w, v: f"{w['X']} take at least their usual {v:.1f} shot attempts per 60 at 5-on-5.",
    "quality": lambda w, v: f"{w['Xp']} average shot has at least their usual {100 * v:.1f}% chance of going in.",
    "turnover": lambda w, v: f"{w['X']} get at least their usual {v:.1f}% of their 5-on-5 chances right after winning the puck.",
    "pp": lambda w, v: f"{w['Xp']} power play creates at least their usual {v:.1f} expected goals per 60.",
}
# How each trait's value reads in a sentence.
SAY = {
    "volume": lambda v: f"{v:.1f} shot attempts per 60 at 5-on-5",
    "quality": lambda v: f"{a_pct(v)} chance of scoring on the average shot",
    "turnover": lambda v: f"{v:.1f}% of their chances come right after winning the puck",
    "pp": lambda v: f"{v:.1f} expected goals per 60",
}

# Voice (user, 2026-10-10): casual, the way a fan talks. Team nicknames lean ("the Blues"), with the place name mixed in;
# teams are "they"; no numbers in headlines; the body says why in a sentence, then what we expect. Ranks read
# "6th in the league", never "6th of 32".
# Placeholders: {X} / {Y} the nickname with "the" ("the Blues", plural verbs), {Xp} / {Yp} its possessive ("the Blues’"),
# {Xc} / {Yc} the place ("St. Louis", singular verbs; a nickname for the two New York clubs). Each pool is picked from
# the game and the call (not at random), so the wording stays put between runs. A leading * marks wording that claims
# the team is among the league's best, used only for a top-five rank.
CLASH_WORDS = {
    "volume": (
        ["{X} should pile up the shots", "Expect {X} to fire from everywhere", "{Y} give up shots, and {X} take them",
         "{X} are going to test {Yp} goalie early and often"],
        ["{X} shoot a lot, and {Y} are {yw} at keeping shots down. Look for a big shot total.",
         "*Few teams shoot like {X}, and {Y} are {yw} at keeping shots away. It could be a long night in {Yp} end.",
         "{Yc} has been giving up shots all season, and {X} rarely pass one up."],
    ),
    "quality": (
        ["{X} should get some good looks", "{Y} give up good looks, and {X} take them", "{Yp} goalie is in for some dangerous shots",
         "Quality over quantity for {X}"],
        ["{X} make their shots count, and {Y} are {yw} at preventing dangerous shots. Expect {X} to get some real chances.",
         "*{Xp} shots are some of the most dangerous in the league, and {Y} are {yw} at preventing dangerous ones. Look for plenty of good chances.",
         "{Y} have struggled to prevent dangerous shots, and {X} don’t waste many."],
    ),
    "turnover": (
        ["{Y} can’t afford to cough it up", "{X} feed on mistakes, and {Y} make them", "Watch {Yp} puck management",
         "Turnovers could swing this one"],
        ["{X} turn stolen pucks into chances, and {Y} are {yw} at avoiding breakdowns. A sloppy pass or two could cost {Y}.",
         "*Almost nobody punishes turnovers like {X}, and {Y} give the puck away in bad spots.",
         "{Y} give up more dangerous chances after losing the puck than most, and {X} pounce."],
    ),
    "pp": (
        ["{Xp} power play has a target", "Penalties could cost {Y}", "Special teams lean toward {X}", "{Y} can’t afford to take penalties"],
        ["{Xp} power play is {xw}, and {Yp} kill is {yw}. Every trip to the box hurts {Y} more than most.",
         "{Yp} penalty kill is {yw}, and {Xp} power play is {xw}. Discipline matters more than usual for {Y} tonight.",
         "*{Xp} power play is one of the league’s best, and {Yp} kill is {yw}. {Y} need to stay out of the box."],
    ),
}
PACE_WORDS = {
    "fast": (["Expect a track meet", "Shots should fly both ways", "Nobody’s sitting back in this one"],
             "{Ac} plays some of the league’s fastest games, and so does {Hc}. We think this one runs hotter than either team’s usual."),
    "slow": (["Expect a grind", "Bring a pillow", "Don’t blink and you still might miss the shots"],
             "{Ac} plays some of the league’s slowest games, and so does {Hc}. We think this one is even quieter than either team’s usual."),
}
# The gist of each call, one short sentence with no numbers, for the story at the top of the page.
CLASH_GIST = {"volume": "{X} shoot a lot, and {Y} give up plenty.", "quality": "{X} make their shots count, and {Y} allow dangerous ones.",
              "turnover": "{X} pounce on loose pucks, and {Y} cough them up.", "pp": "{Xp} power play has a soft target in {Yp} kill."}


def standing(rank: int) -> str:
    """A league rank (1 best of 32) in words, for numbers-free prose."""
    return ("the league’s best" if rank == 1 else "one of the league’s best" if rank <= 5 else "near the top of the league" if rank <= 10
            else "around the middle of the league" if rank <= 22 else "near the bottom of the league" if rank <= 27
            else "one of the league’s worst" if rank <= 31 else "the league’s worst")
PHYSICAL_HEADS = ["Bodies will fly", "This one’s going to leave marks", "Two of the league’s heaviest-hitting teams"]
TOP = 5  # "among the best" wording needs a top-five rank
BOTH = 70  # both teams at or above this percentile (or both at or below 100 - BOTH) for a pace or physical call

# Strength against strength (the "duel"): one team's best habit meets the other team's best defence of it. Which side
# wins is close to a coin flip, so these are the hottest calls; the lean comes from a regression of each game's figure
# on the attacking team's level and the defending team's level (each from that season's other games, 2023-24 to
# 2025-26, about 7,800 team-games): (weight on the offense, weight on the defense). In games where both sides ranked
# in the top quarter, the attack beat the league average 51% of the time for shot volume, 52% for chance quality and
# 55% on the power play, so power plays win these more often than kills do.
DUELS = {  # offense trait: (defense trait, weights, what the offense does, what the defense does)
    "volume": ("suppression", (0.891, 0.855), "shot volume", "keeping shots away"),
    "quality": ("qualityAllowed", (0.683, 0.611), "chance quality", "preventing dangerous shots"),
    "pp": ("pk", (0.897, 0.589), "the power play", "the penalty kill"),
}
DUEL_BAR = 75  # both teams at or above this percentile
DUEL_HEADS = ["Something has to give", "Strength against strength", "The matchup inside the matchup", "Best against best"]
DUEL_LEAN = {  # who we side with
    "offense": ["We think {X} still get theirs.", "Our lean: {Xp} {oname} wins out.", "We’ll side with {X}."],
    "defense": ["Our lean: {Y} hold the line.", "We’ll side with {Yp} defense.", "We think {Y} win this one."],
}
DUEL_LEAN_PP = {"offense": ["Our lean: {Xp} power play wins out. Power plays usually beat kills in these spots."],
                "defense": ["Our lean: {Yp} kill holds up, even though power plays usually win these."]}

# "Not the team you remember": this season a team is clearly not what their two-season blend says. Only once the
# season has as many games as the trait needs to be half signal, and only when the season's 80% range sits wholly on
# one side of the blend. The call: tonight lands on this season's side of the blend.
CHANGED = {  # trait: (ranked for, the team's ..., better, worse); every trait here is measured for one team in one game
    "volume": ("shot volume", "shot volume", "shooting more", "shooting less"),
    "suppression": ("keeping shots away", "shot suppression", "tighter defensively", "giving up more shots"),
    "quality": ("chance quality", "chance quality", "getting better looks", "settling for worse looks"),
    "qualityAllowed": ("preventing dangerous shots", "defense against dangerous shots", "giving up fewer dangerous shots", "giving up more dangerous shots"),
    "physical": ("hitting", "hitting", "hitting more", "hitting less"),
    "pp": ("the power play", "power play", "a sharper power play", "a quieter power play"),
    "pk": ("the penalty kill", "penalty kill", "a tighter kill", "a leakier kill"),
}
CHANGED_GAP = 25  # season and blend percentiles at least this far apart
CHANGED_MOVE = 0.05  # and the figures themselves at least 5% apart
CHANGED_HEADS = {"better": ["{Xp} {thing} is better than you remember", "Don’t judge {X} by last season", "{X} have changed"],
                 "worse": ["{Xp} {thing} isn’t what it was", "Not the {Xn} you remember", "{X} have slipped"]}
CHANGED_HEADS_PHYSICAL = {"better": ["{X} are hitting more than you remember", "{X} have toughened up"],
                          "worse": ["{X} aren’t hitting like they used to", "Not the {Xn} you remember"]}

# Home matchups, told as what the coach is doing: hunting a mismatch for his scorers, sending a checking line at the
# other team's stars, or going power against power.
MATCHUP_WORDS = {
    "hunt": (["{H} will hunt a mismatch", "{Hstar}’s line gets the soft minutes", "{H} want {Hstar} away from the big guns"],
             "That’s {coach} chasing an easier matchup for the team’s scorers."),
    "shadow": (["A shadow job for {mine}", "{H} will send their checkers at {Ostar}", "{Ostar} gets company tonight"],
               "That’s a checking assignment: {coach} wants this group on {theirline}."),
    "best": (["Best on best: {Hstar} against {Ostar}", "{H} want {Hstar} head to head with {Ostar}", "Power against power"],
             "Strength on strength, and {coach} is choosing it."),
    "even": (["{H} like {Hstar}’s line against {Ostar}’s", "Like for like: {Hstar} against {Ostar}"],
             "Line for line, and {coach} keeps choosing it."),
    "pair": (["{mine} get the {Ostar} assignment", "{H} will send {mine} at {Ostar}", "A tough night ahead for {Ostar}"],
             "That’s {coach} picking a defense pair for {theirline}."),
}

# Goalie records against one team: tested over 2023-24 to 2026-27 (911 goalie-opponent pairs with four or more
# starts), a goalie's saves above expected against one team in half his meetings predicts the other half not at all
# (r = -0.02). So a striking record against tonight's opponent is a coincidence, and the call says so.
HISTORY_MIN_STARTS, HISTORY_MIN_SHOTS, HISTORY_GAP = 5, 120, 0.03  # save-percentage gap from his usual
HISTORY_HEADS = {"good": ["Does {G} own {O}? Probably not", "{Gp} record against {O} is a coincidence", "Don’t count on {G} to stand on his head again"],
                 "bad": ["{O} aren’t {Gp} kryptonite", "{Gp} rough record against {O} is a coincidence", "{G} isn’t cursed against {O}"]}

HISTORY_BODIES = [
    "{G} has been {how} against {O} since 2023-24, {cmp} his usual. It looks like a pattern, "
    "but across three seasons a goalie’s record against one team told us nothing about the next meeting. Expect the usual {G}.",
    "Since 2023-24, {G} has been {how} against {O}, {cmp} his usual. Fans love a pattern like this, "
    "but we checked three seasons of them and they predicted nothing. Expect the usual {G}.",
]
PRIORITY = {"duel": 0, "changed": 1, "matchup": 2, "history": 3, "clash": 4, "pace": 5, "physical": 5, "edge": 6}
MAX_CALLS, MIN_CALLS, MAX_PER_KIND = 4, 2, 2


def _pick(pool: list[str], *key) -> str:
    return pool[zlib.crc32("-".join(map(str, key)).encode()) % len(pool)]


MIN_HOME_GAMES, MIN_MINUTES = 3, 30  # before a home matching habit becomes a call
ORD = ["first", "second", "third", "fourth"]
NICKS: dict[str, str] = {}  # team -> nickname, from teams.json (filled by `names`)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _last(name: str) -> str:
    return name.split(" ", 1)[-1]


def _lasts(names: list[str]) -> list[str]:
    """Last names, with a first initial where two share one (the Protas brothers)."""
    lasts = [_last(n) for n in names]
    return [f"{n[0]}. {l}" if lasts.count(l) > 1 else l for n, l in zip(names, lasts)]


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _cap(t: str) -> str:
    return t[0].upper() + t[1:]


def _poss(name: str) -> str:
    return name + ("’" if name.endswith("s") else "’s")


def names(places: dict) -> dict[str, str]:
    if not NICKS:
        try:
            NICKS.update({t["abbr"]: t.get("nick") or t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())})
        except (OSError, KeyError, ValueError):
            pass
    return {t: NICKS.get(t, p) for t, p in places.items()}


def team_words(t: str, places: dict, prefix: str) -> dict[str, str]:
    """How a sentence names a team: "the Blues" (plural verbs), "the Blues’", and "St. Louis" (singular verbs)."""
    nick = names(places).get(t, places.get(t, t))
    the = f"the {nick}"
    place = places.get(t, t)
    city = the if place.startswith("NY ") else place  # "NY Rangers" reads badly as a place
    return {prefix: the, f"{prefix}p": _poss(the), f"{prefix}c": city, f"{prefix}n": nick}


def _fmt(text: str, **w) -> str:
    """Fill a template and capitalise each sentence's first letter ("the Blues ..." at the start becomes "The Blues")."""
    out = text.format(**w)
    out = _cap(out)
    return ". ".join(_cap(s) if s else s for s in out.split(". "))


def clash_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    out = []
    for off, dfn, oname, dname in CLASHES:
        for x, y in ((away, home), (home, away)):
            o, d = fp[x]["dims"][off]["blend"], fp[y]["dims"][dfn]["blend"]
            if o["pct"] >= STRONG and d["pct"] <= WEAK:
                words = {**team_words(x, places, "X"), **team_words(y, places, "Y"),
                         "xr": _ordinal(o["rank"]), "yr": _ordinal(d["rank"]), "xv": SAY[off](o["v"]),
                         "xw": standing(o["rank"]), "yw": standing(d["rank"])}
                league = o["v"] * 100 / o["index"] if o.get("index") else None
                out.append({
                    "kind": "clash", "gap": o["pct"] - d["pct"], "metric": off, "team": x, "opp": y,
                    "head": _fmt(_pick(CLASH_WORDS[off][0], gid, off, x), **words),
                    "call": _cap(CLASH_CALL[off](words, o["v"])),
                    "body": _fmt(_pick([b.lstrip("*") for b in CLASH_WORDS[off][1] if o["rank"] <= TOP or not b.startswith("*")], gid, off, x, "body"), **words),
                    "nums": f"{places[x]} {words['xr']} for {oname} ({words['xv']}) · {places[y]} {words['yr']} for {dname}",
                    "gist": _fmt(CLASH_GIST[off], **words),
                    "cite": f"Style fingerprint · this season blended with last · {fp[x]['games']} and {fp[y]['games']} games this season",
                    "check": {"metric": off, "measure": MEASURE[off], "team": x, "baseline": round(o["v"], 4),
                              "league": round(league, 4) if league else None, "direction": "above",
                              "rule": "held if tonight's figure is at or above the team's usual level; partly if between the league average and that level; otherwise didn't happen"},
                })
    out.sort(key=lambda c: -c["gap"])
    seen, picked = set(), []
    for c in out:  # one call per trait pairing
        if c["metric"] not in seen:
            seen.add(c["metric"])
            picked.append(c)
    return picked[:3]


def duel_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    """Strength against strength: pick the side the regression leans to, against the league average."""
    out = []
    for off, (dfn, (wo, wd), oname, dname) in DUELS.items():
        for x, y in ((away, home), (home, away)):
            o, d = fp[x]["dims"][off]["blend"], fp[y]["dims"][dfn]["blend"]
            if o["pct"] < DUEL_BAR or d["pct"] < DUEL_BAR or not o.get("index") or not d.get("index"):
                continue
            lo, ld = o["v"] * 100 / o["index"], d["v"] * 100 / d["index"]
            lean = lo + wo * (o["v"] - lo) + wd * (d["v"] - ld)  # what tonight's figure for x is expected to be
            side = "offense" if lean >= lo else "defense"
            words = {**team_words(x, places, "X"), **team_words(y, places, "Y"), "oname": oname, "dname": dname,
                     "xr": _ordinal(o["rank"]), "yr": _ordinal(d["rank"]), "xv": SAY[off](o["v"])}
            lean_txt = _pick((DUEL_LEAN_PP if off == "pp" else DUEL_LEAN)[side], gid, off, x, "lean")
            body = "{X} are " + standing(o["rank"]) + " for " + oname + ", and {Y} are " + standing(d["rank"]) + " at " + dname + ". " + lean_txt
            call = _cap(DUEL_CALL[off][side](words, lo))
            out.append({
                "kind": "duel", "gap": min(o["pct"], d["pct"]), "metric": off, "team": x, "opp": y, "lean": side,
                "head": _fmt(_pick(DUEL_HEADS, gid, off, x), **words),
                "body": _fmt(body, **words), "call": call,
                "nums": f"{places[x]} {words['xr']} for {oname} ({words['xv']}) · {places[y]} {words['yr']} for {dname}",
                "gist": _fmt("It’s strength against strength: {X} for " + oname + ", {Y} at " + dname + ".", **words),
                "cite": f"Style fingerprint · this season blended with last · {fp[x]['games']} and {fp[y]['games']} games this season",
                "check": {"metric": off, "measure": MEASURE[off], "team": x, "baseline": round(lo, 4), "usual": round(o["v"], 4),
                          "league": round(lo, 4), "direction": "above" if side == "offense" else "below",
                          "rule": "held if tonight's figure lands on the predicted side of the league average; otherwise didn't happen"},
            })
    out.sort(key=lambda c: -c["gap"])
    return out


DUEL_CALL = {  # trait: {side: the call in words}, w the attacking (X) and defending (Y) teams, lg the league average
    "volume": {"offense": lambda w, lg: f"{w['X']} take more than the league-average {lg:.1f} shot attempts per 60 at 5-on-5 against {w['Y']}.",
               "defense": lambda w, lg: f"{w['Y']} keep {w['X']} under the league-average {lg:.1f} shot attempts per 60 at 5-on-5."},
    "quality": {"offense": lambda w, lg: f"{w['Xp']} chances are more dangerous than the league average ({a_pct(lg)} chance of scoring on the average shot) against {w['Y']}.",
                "defense": lambda w, lg: f"{w['Y']} keep {w['Xp']} chances less dangerous than the league average ({a_pct(lg)} chance of scoring on the average shot)."},
    "pp": {"offense": lambda w, lg: f"{w['Xp']} power play creates more than the league-average {lg:.1f} expected goals per 60 against {w['Y']}.",
           "defense": lambda w, lg: f"{w['Yp']} kill holds {w['Xp']} power play under the league-average {lg:.1f} expected goals per 60."},
}


def changed_claims(gid: int, away: str, home: str, fp: dict, places: dict, stab: dict) -> list[dict]:
    """Teams playing clearly unlike their two-season selves, once the season has enough games to say so."""
    from pipeline.metrics.team_style import DIMS
    out = []
    for t, o in ((away, home), (home, away)):
        games = fp[t].get("games", 0)
        for k, (name, thing, better, worse) in CHANGED.items():
            dim = fp[t]["dims"].get(k, {})
            b, s = dim.get("blend"), dim.get("season")
            if not b or not s or s.get("lo") is None or games < (stab.get(k, {}).get("k_games") or 999):
                continue
            if abs(s["pct"] - b["pct"]) < CHANGED_GAP or s["lo"] <= b["pct"] <= s["hi"] or abs(s["v"] - b["v"]) < CHANGED_MOVE * abs(b["v"]):
                continue
            up = s["pct"] > b["pct"]  # better than the blend (percentiles are oriented so higher is better)
            higher = DIMS[k][3]
            words = {**team_words(t, places, "X"), "name": name, "thing": thing}
            show = EDGE[k][4] if k in EDGE else (lambda v: f"{v:.1f}")
            sv, bv = (s["index"], b["index"]) if k in INDEX_TRAITS else (s["v"], b["v"])
            body = (f"Over this season and last, {words['X']} have been {standing(b['rank'])} for {name}. This season alone they’ve been "
                    f"{standing(s['rank'])}, {better if up else worse}. "
                    f"We think tonight looks more like this season than the old {words['Xn']}.")
            # "above" the blend means a bigger figure; for traits where lower is better, better means below
            direction = "above" if up == higher else "below"
            out.append({
                "kind": "changed", "gap": abs(s["pct"] - b["pct"]), "metric": k, "team": t, "opp": o,
                "head": _fmt(_pick((CHANGED_HEADS_PHYSICAL if k == "physical" else CHANGED_HEADS)["better" if up else "worse"], gid, k, t), **words),
                "body": _cap(body),
                "nums": f"{_cap(name)}: {_ordinal(b['rank'])} over two seasons ({show(bv)}), {_ordinal(s['rank'])} this season ({show(sv)})",
                "gist": f"{_cap(words['X'])} aren’t the team you remember for {name}.",
                "call": (f"{_cap(words['X'])} hit {'more' if up else 'less'} than their blended level ({show(bv)})." if k == "physical" else
                         f"{_cap(words['Xp'])} {thing} is {'better' if up else 'worse'} than their blended level of {show(bv)}."),
                "cite": f"Style fingerprint · blended and this season only · {games} games this season",
                "check": {"metric": k, "team": t, "baseline": round(b["v"], 4), "season": round(s["v"], 4), "index_traits": k in INDEX_TRAITS,
                          "league": round(b["v"] * 100 / b["index"], 4) if b.get("index") else None, "direction": direction,
                          "rule": "held if tonight's figure lands on this season's side of the blended level; otherwise didn't happen"},
            })
    out.sort(key=lambda c: -c["gap"])
    return out


@lru_cache(maxsize=1)
def _player_points():
    """Every skater-game over this season and last (points and date), from the tables as they stand."""
    import polars as pl

    from pipeline.config import CURRENT_SEASON, TABLES
    fr = []
    for s in (CURRENT_SEASON - 1, CURRENT_SEASON):
        d = TABLES / str(s)
        if (d / "player_game.parquet").exists():
            dates = pl.read_parquet(d / "games.parquet", columns=["game_id", "date"])
            fr.append(pl.read_parquet(d / "player_game.parquet", columns=["game_id", "player_id", "g", "a1", "a2"]).join(dates, on="game_id"))
    return pl.concat(fr) if fr else None


def _points(before: str | None = None) -> dict[int, float]:
    """Points per game for every skater over this season and last, from games before `before` (a date) if given.
    Used only to name a line's best-known player."""
    try:
        import polars as pl
        d = _player_points()
        if d is None:
            return {}
        if before:
            d = d.filter(pl.col("date") < before[:10])
        d = d.group_by("player_id").agg(p=(pl.col("g") + pl.col("a1") + pl.col("a2")).sum() / pl.len())
        return dict(zip(d["player_id"], d["p"]))
    except Exception as e:
        print(f"points unavailable: {e!r}")
        return {}


def _star(unit: dict, pts: dict) -> str:
    """The unit's best-known player: most points per game, else the last one listed (usually the centre)."""
    ids, players = unit.get("ids", []), unit.get("players", [])
    if pts and ids:
        i = max(range(len(ids)), key=lambda j: pts.get(ids[j], 0.0))
        return _last(players[i])
    return _last(players[-1]) if players else ""


def matchup_claim(gid: int, away: str, home: str, lines: dict, places: dict, pts: dict | None = None,
                  coach: str | None = None) -> dict | None:
    """`coach` is the home team's head coach before this game; without one the call says \"their coach\"."""
    h, a = lines.get(home), lines.get(away)
    if not h or not a or not h.get("matchups"):
        return None
    mu = h["matchups"]["home"]
    if mu["games"] < MIN_HOME_GAMES:
        return None
    rows = [(r, j, v, v - r["expected"][j]) for r in mu["rows"] if r["minutes"] >= MIN_MINUTES for j, v in enumerate(r["share"]) if v is not None]
    rows = [x for x in rows if x[3] >= 10]
    if not rows:
        return None
    r, j, v, _ = max(rows, key=lambda x: x[3])
    unit = next(u for u in h["units"] if u["label"] == r["label"])
    opp = next((u for u in a["units"] if u["label"] == f"L{j + 1}"), None)
    if not opp:
        return None
    pts = pts if pts is not None else {}
    mine = _and(_lasts(unit["players"]))
    theirs = _and(_lasts(opp["players"]))
    lab, n = r["label"][0], int(r["label"][1:])
    style = ("pair" if lab == "P" else "best" if n == 1 and j == 0 else "hunt" if n < j + 1 else "shadow" if n > j + 1 else "even")
    heads, why = MATCHUP_WORDS[style]
    w = {**team_words(home, places, "H"), **team_words(away, places, "O"), "mine": mine, "theirs": theirs,
         "Hstar": _star(unit, pts), "Ostar": _star(opp, pts)}
    theirline = "the other team’s top line" if j == 0 else f"opponents’ {ORD[j]} lines"
    group = f"{mine}" if lab == "P" else f"{w['Hstar']}’s line ({mine})"
    body = (f"At home, {w['H']} keep sending {group} out against opponents’ {ORD[j]} lines, far more often than chance would. "
            f"{why.format(theirline=theirline, coach=coach or 'their coach')} Tonight {w['Op']} {ORD[j]} line is {theirs}.")
    return {
        "kind": "matchup", "metric": "matchup_share", "team": home, "opp": away, "style": style,
        "head": _fmt(_pick(heads, gid, "matchup"), **w),
        "call": f"{_cap(w['Hp'])} {r['label']} ({mine}) spends at least {v}% of its 5-on-5 time against {w['Op']} {ORD[j]} line.",
        "body": _cap(body),
        "nums": f"At home: {v}% of this group’s 5-on-5 time against {ORD[j]} lines, about {r['expected'][j]}% with no line matching",
        "gist": f"At home, {coach or 'their coach'} likes to pick matchups, so watch who goes out against {w['Op']} {ORD[j]} line.",
        "cite": f"Who plays against whom · {places[home]} home games this season · {mu['games']} games, {r['minutes']:.0f} minutes for this group at 5-on-5",
        "check": {"metric": "matchup_share", "measure": f"share of {places[home]} {r['label']}'s 5-on-5 time against {places[away]} L{j + 1}",
                  "team": home, "unit": r["label"], "unit_ids": unit["ids"], "opp_line": f"L{j + 1}", "opp_ids": opp["ids"],
                  "opp_lines": {u["label"]: u["ids"] for u in a["units"] if u["label"][0] == "L"},
                  "baseline": v, "threshold": r["expected"][j], "direction": "above",
                  "rule": "held if the share is at or above the home habit; partly if above the no-matching level; otherwise didn't happen"},
    }


@lru_cache(maxsize=1)
def _goalie_history() -> "object | None":
    """Every start over 2023-24 to now: goalie, opponent, date, shots on goal, goals. None when the tables are missing."""
    try:
        import polars as pl

        from pipeline.config import CURRENT_SEASON, FULL_SEASONS, TABLES
        from pipeline.metrics import goalies
        fr = []
        for s in [*FULL_SEASONS, CURRENT_SEASON]:
            if not (TABLES / str(s) / "events.parquet").exists():
                continue
            g = goalies.per_game(s).filter(pl.col("started"))
            gm = pl.read_parquet(TABLES / str(s) / "games.parquet").select("game_id", "home_id", "away_id", "home", "away")
            g = g.join(gm, on="game_id").with_columns(opp=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("away")).otherwise(pl.col("home")))
            fr.append(g.select("goalie_id", "opp", "date", "sa", "ga"))
        return pl.concat(fr) if fr else None
    except Exception as e:
        print(f"goalie history unavailable: {e!r}")
        return None


def history_claims(gid: int, away: str, home: str, goalies_site: dict, hist, places: dict) -> list[dict]:
    """A goalie (his team's busiest this season) whose record against tonight's opponent is far from his usual.
    `hist` holds only starts before the game (see `call_extras`)."""
    if hist is None:
        return []
    import polars as pl
    out = []
    for t, o in ((away, home), (home, away)):
        roster = goalies_site.get("teams", {}).get(t) or []
        if not roster:
            continue
        g = max(roster, key=lambda x: ((x.get("season") or {}).get("starts", 0), (x.get("blend") or {}).get("starts", 0)))
        mine = hist.filter(pl.col("goalie_id") == g["id"])
        if mine.is_empty():
            continue
        vs = mine.filter(pl.col("opp") == o)
        n, sa, ga = vs.height, int(vs["sa"].sum() or 0), int(vs["ga"].sum() or 0)
        all_sa, all_ga = int(mine["sa"].sum()), int(mine["ga"].sum())
        if n < HISTORY_MIN_STARTS or sa < HISTORY_MIN_SHOTS or all_sa - sa < 300:
            continue
        sv_vs, sv_all = 1 - ga / sa, 1 - (all_ga - ga) / (all_sa - sa)
        if abs(sv_vs - sv_all) < HISTORY_GAP:
            continue
        good = sv_vs > sv_all
        G = _last(g["name"])
        w = {"G": G, "Gp": _poss(G), **team_words(o, places, "O")}
        fmt = lambda s: f"{s:.3f}".lstrip("0")
        body = _pick(HISTORY_BODIES, gid, g["id"], "body").format(how="a wall" if good else "shaky", cmp="well above" if good else "well below", **w)
        out.append({
            "kind": "history", "gap": abs(sv_vs - sv_all) * 1000, "metric": "goalie_sv", "team": t, "opp": o,
            "head": _fmt(_pick(HISTORY_HEADS["good" if good else "bad"], gid, g["id"]), **w),
            "body": _cap(body),
            "nums": f"{n} starts against {places.get(o, o)}: {fmt(sv_vs)} save percentage · {fmt(sv_all)} against everyone else",
            "gist": f"Don’t read much into {_poss(G)} record against {w['O']}.",
            "call": f"If {G} starts, his save percentage lands closer to his usual {fmt(sv_all)} than to his {fmt(sv_vs)} against {w['O']}.",
            "cite": f"Goalie starts since 2023-24 · {n} against {places.get(o, o)}, {all_sa - sa:,} shots against everyone else",
            "check": {"metric": "goalie_sv", "team": t, "goalie_id": g["id"], "baseline": round(sv_all, 4), "record": round(sv_vs, 4),
                      "starts": n, "direction": "closer",
                      "rule": "held if he starts and his save percentage is closer to his usual than to his record against this team; can't judge if he doesn't start"},
        })
    out.sort(key=lambda c: -c["gap"])
    return out[:1]  # one goalie myth a game is plenty


# Edge calls: on a trait where the two teams sit far apart, the team at the better end comes out ahead tonight.
# trait: (name, low end, high end, what the recap compares, how a single figure reads)
EDGE = {
    "volume": ("shot volume", "selective", "relentless", "5-on-5 shot attempts per 60", lambda v: f"{v:.1f} per 60"),
    "quality": ("shot quality", "hopeful", "dangerous", "how dangerous the average shot was", lambda v: f"{100 * v:.1f}% a shot" if v else "–"),
    "suppression": ("shot suppression", "porous", "stingy", "5-on-5 shot attempts allowed per 60", lambda v: f"{v:.1f} per 60"),
    "qualityAllowed": ("preventing dangerous shots", "exposed", "sheltered", "how dangerous the average shot allowed was", lambda v: f"{100 * v:.1f}% a shot" if v else "–"),
    "point": ("point-shot reliance", "down low", "point-heavy", "share of 5-on-5 shot attempts from the point", lambda v: f"{v:.0f}%"),  # retired; grades old calls
    "dShots": ("shots from defensemen", "forward-led", "active D", "share of 5-on-5 shot attempts taken by defensemen", lambda v: f"{v:.0f}%"),
    "inClose": ("shots from in close", "long-range", "close-range", "share of 5-on-5 shots from within 20 feet", lambda v: f"{v:.0f}%"),
    "forecheck": ("forecheck pressure", "passive", "hounding", "forecheck score (arena-adjusted, 100 is league average)", lambda v: f"forecheck score {v:.0f}"),
    "physical": ("physicality", "finesse", "bruising", "hit score in close games (arena-adjusted, 100 is league average)", lambda v: f"hit score {v:.0f}"),
    "depth": ("depth", "top-heavy", "deep", "bottom-six share of forward ice time", lambda v: f"{v:.0f}%"),
    "pp": ("power play", "harmless", "lethal", "power-play expected goals per 60", lambda v: f"{v:.1f} per 60"),
    "pk": ("penalty kill", "leaky", "airtight", "expected goals allowed per 60 on the penalty kill", lambda v: f"{v:.1f} per 60"),
}
SAME = {"volume": "suppression", "suppression": "volume", "quality": "qualityAllowed", "qualityAllowed": "quality", "pp": "pk", "pk": "pp"}
INDEX_TRAITS = {"forecheck", "physical"}  # compared as a score against league average, never as a count
EDGE_CALL = {  # the prediction in plain words: {top} comes out ahead of {bot} on this tonight
    "volume": "{top} take more 5-on-5 shot attempts per 60 than {bot}.", "quality": "{topp} shots are more dangerous on average than {botp}.",
    "suppression": "{top} allow fewer 5-on-5 shot attempts per 60 than {bot}.", "qualityAllowed": "{top} prevent dangerous shots better than {bot} do.",
    "point": "{top} take a bigger share of their shots from the point than {bot}.",
    "dShots": "{topp} defensemen take a bigger share of the team’s shot attempts than {botp}.",
    "inClose": "{top} take a bigger share of their shots from within 20 feet than {bot}.", "forecheck": "{top} put more forecheck pressure on than {bot}.",
    "physical": "{top} hit more than {bot} in the close stretches of the game.", "depth": "{top} spread their forward ice time deeper down the lineup than {bot}.",
    "pp": "{topp} power play creates more per 60 than {botp}.", "pk": "{topp} penalty kill allows less per 60 than {botp}.",
}
EDGE_HEADS = {  # {top} is the team at the better (or higher) end of the scale, {bot} the other
    "volume": ["{top} will outshoot {bot}", "Volume against patience"],
    "quality": ["{top} hunt better looks than {bot}", "Good looks against hopeful ones"],
    "suppression": ["{top} lock it down; {bot} don’t", "One stingy defense, one leaky one"],
    "qualityAllowed": ["{top} shut down the dangerous stuff; {bot} don’t", "Sheltered against exposed"],
    "point": ["{top} fire from the point; {bot} work down low", "Two different ideas of a good shot"],
    "dShots": ["{topp} defense joins the attack; {botp} stays home", "Active D against forward-led"],
    "inClose": ["{top} get in close; {bot} shoot from distance", "In close against from distance"],
    "forecheck": ["{top} hound the puck; {bot} sit back", "Forecheck against patience"],
    "physical": ["Bruisers against finesse", "{top} hit; {bot} mostly don’t"],
    "depth": ["{top} roll four lines; {bot} lean on their top six", "Depth against star power"],
    "pp": ["One power play bites, one doesn’t", "{topp} power play outclasses {botp}"],
    "pk": ["{top} kill penalties far better than {bot}", "Airtight against leaky on the kill"],
}
EDGE_BODIES = ["{top} are {wt} for {name}, at the {hi} end; {bot} are {wb}, at the {lo} end. Expect that gap to show.",
               "On {name}, {topc} is {wt} and {botc} is {wb}. That’s about as far apart as two teams get."]
EDGE_GAP, EDGE_END = 50, 25  # a "hot enough" edge: percentiles 50+ apart, with one team in the top or bottom quarter


def edge_check(k: str, top: str, bot: str, fp: dict) -> dict:
    """What the recap compares for an edge call: both teams' usual figures and the usual gap between them, oriented so
    a positive gap means `top` is ahead. Traits shown as a score are stored as that score."""
    from pipeline.metrics.team_style import DIMS
    higher = DIMS[k][3]
    t, b = fp[top]["dims"][k]["blend"], fp[bot]["dims"][k]["blend"]
    league = t["v"] * 100 / t["index"] if t.get("index") else None
    if k in INDEX_TRAITS:
        tv, bv = t["index"], b["index"]
    else:
        tv, bv = t["v"], b["v"]
    gap = (tv - bv) if higher else (bv - tv)
    return {"metric": k, "measure": EDGE[k][3], "team": top, "opp": bot, "higher": higher,
            "baseline": {top: round(tv, 4), bot: round(bv, 4)}, "gap": round(gap, 4),
            "league": round(league, 4) if league else None, "direction": "ahead",
            "rule": "held if the team comes out ahead by at least half its usual margin; partly if it comes out ahead by less; otherwise didn't happen"}


def edge_claim(gid: int, away: str, home: str, fp: dict, places: dict, skip: set, hot_only: bool = False) -> dict | None:
    """The trait where the two teams sit furthest apart, called as: the team at the better end comes out ahead tonight.
    With `hot_only`, only a gap wide enough to be worth a call (EDGE_GAP, EDGE_END); otherwise the widest one."""
    gap = lambda k: abs(fp[away]["dims"][k]["blend"]["pct"] - fp[home]["dims"][k]["blend"]["pct"])
    keys = [k for k in EDGE if k not in skip and k in fp[away]["dims"] and k in fp[home]["dims"]]
    if hot_only:
        keys = [k for k in keys if gap(k) >= EDGE_GAP and max(fp[t]["dims"][k]["blend"]["pct"] for t in (away, home)) >= 100 - EDGE_END
                and min(fp[t]["dims"][k]["blend"]["pct"] for t in (away, home)) <= EDGE_END]
    if not keys:
        return None
    k = max(keys, key=gap)
    name, lo, hi = EDGE[k][:3]
    a, h = fp[away]["dims"][k]["blend"], fp[home]["dims"][k]["blend"]
    top, bot = (away, home) if a["pct"] >= h["pct"] else (home, away)
    rt, rb = (a, h) if top == away else (h, a)
    tw, bw = team_words(top, places, "top"), team_words(bot, places, "bot")
    w = {"name": ("the " + name) if k in ("pp", "pk") else name, **tw, **bw, "rt": _ordinal(rt["rank"]), "rb": _ordinal(rb["rank"]), "hi": hi, "lo": lo,
         "wt": standing(rt["rank"]), "wb": standing(rb["rank"])}
    return {
        "kind": "edge", "gap": gap(k), "metric": k, "team": top, "opp": bot,
        "head": _fmt(_pick(EDGE_HEADS[k], gid, k), **w),
        "body": _fmt(_pick(EDGE_BODIES, gid, k, "body"), **w),
        "nums": f"{_cap(name)}: {places[top]} {w['rt']}, {places[bot]} {w['rb']}",
        "gist": _fmt("{top} and {bot} sit at opposite ends for " + name + ".", **w),
        "call": _fmt(EDGE_CALL[k], **w),
        "cite": f"Style fingerprint · this season blended with last · {fp[away]['games']} and {fp[home]['games']} games this season",
        "check": edge_check(k, top, bot, fp),
    }


def tempo_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    """Calls about the game as a whole: pace (both teams fast or both slow) and physicality (both heavy hitters). The pace
    bar is the faster (or slower) of the two teams' usual, not their average: two fast teams playing a fast game is the
    obvious read, so the call says this one runs faster than either usually does."""
    out = []
    cite = f"Style fingerprint · this season blended with last · {fp[away]['games']} and {fp[home]['games']} games this season"
    a, h = fp[away]["dims"]["pace"]["blend"], fp[home]["dims"]["pace"]["blend"]
    w = {**team_words(away, places, "A"), **team_words(home, places, "H")}
    for kind, both, direction in (("fast", a["pct"] >= BOTH and h["pct"] >= BOTH, "above"), ("slow", a["pct"] <= 100 - BOTH and h["pct"] <= 100 - BOTH, "below")):
        if both:
            heads, body = PACE_WORDS[kind]
            league = a["v"] * 100 / a["index"]
            bar = max(a["v"], h["v"]) if kind == "fast" else min(a["v"], h["v"])
            out.append({
                "kind": "pace", "gap": min(a["pct"], h["pct"]) if kind == "fast" else 100 - max(a["pct"], h["pct"]), "metric": "pace", "team": away, "opp": home,
                "head": _pick(heads, gid, "pace"),
                "call": f"The game runs at {'more' if kind == 'fast' else 'fewer'} than {bar:.1f} shots per 60 at 5-on-5, {'faster' if kind == 'fast' else 'slower'} than either team’s usual.",
                "body": _fmt(body, **w),
                "nums": f"Pace (shots per 60 at 5-on-5, both teams): {places[away]} {_ordinal(a['rank'])} ({a['v']:.1f}), {places[home]} {_ordinal(h['rank'])} ({h['v']:.1f})",
                "gist": "Both teams like a fast game." if kind == "fast" else "Both teams play slow, low-event hockey.",
                "cite": cite,
                "check": {"metric": "pace", "measure": "shots per 60 at 5-on-5 (blocked shots left out), both teams combined, adjusted for score and venue",
                          "team": None, "baseline": round(bar, 2), "average": round((a["v"] + h["v"]) / 2, 2), "league": round(league, 2), "direction": direction,
                          "rule": f"held if tonight's pace is {direction} the {'faster' if kind == 'fast' else 'slower'} team's usual; partly if {direction} the league average; otherwise didn't happen"},
            })
    a, h = fp[away]["dims"]["physical"]["blend"], fp[home]["dims"]["physical"]["blend"]
    if a["pct"] >= BOTH and h["pct"] >= BOTH:
        out.append({
            "kind": "physical", "gap": min(a["pct"], h["pct"]), "metric": "physical", "team": away, "opp": home,
            "head": _pick(PHYSICAL_HEADS, gid, "physical"),
            "call": f"The two teams’ combined hit score reaches their usual {(a['index'] + h['index']) / 2:.0f}.",
            "body": _fmt("{A} and {H} are both among the league’s heaviest hitters, even after adjusting for each arena’s scorer. Keep your head up.", **w),
            "nums": f"Hit score (100 is league average): {places[away]} {a['index']} ({_ordinal(a['rank'])}), {places[home]} {h['index']} ({_ordinal(h['rank'])})",
            "gist": "Expect plenty of hitting.",
            "cite": cite,
            "check": {"metric": "physical", "measure": "hits per 60 in close games, both teams, as a score against league average (arena-adjusted)",
                      "team": None, "baseline": round((a["index"] + h["index"]) / 2, 1), "league": 100, "direction": "above",
                      "rule": "held if tonight's combined hit score is at or above the two teams' average; partly if above 100; otherwise didn't happen"},
        })
    return out


def calls(gid: int, away: str, home: str, fp: dict, lines: dict, places: dict, extra: dict | None = None) -> list[dict]:
    """The two to four "What to watch" calls for one game, from fingerprints and lines as they stood before it.

    Calls are chosen for how interesting they are, not how safe: strength against strength first (a real question),
    then a team that has changed, a coach's matchup plan, a goalie record that is really a coincidence, a strength into
    a weakness, a game-wide pace or hitting call, and last a wide gap between the two on one trait. At most two of one
    kind; topped up with the widest gap when fewer than two qualify. `extra` holds what only a full run has (the
    fingerprints' stabilization table, points per game, goalie history and goalies.json)."""
    extra = extra or {}
    cands = duel_claims(gid, away, home, fp, places)
    cands += changed_claims(gid, away, home, fp, places, extra.get("stab") or {})
    mc = matchup_claim(gid, away, home, lines, places, extra.get("pts"), (extra.get("coaches") or {}).get(home))
    if mc:
        cands.append(mc)
    cands += history_claims(gid, away, home, extra.get("goalies") or {}, extra.get("hist"), places)
    cands += clash_claims(gid, away, home, fp, places)
    cands += tempo_claims(gid, away, home, fp, places)
    ec = edge_claim(gid, away, home, fp, places, set(), hot_only=True)
    if ec:
        cands.append(ec)
    claims, per_kind, used = [], {}, set()
    for c in sorted(cands, key=lambda c: (PRIORITY[c["kind"]], -c.get("gap", 0))):
        # one call per trait pairing: a duel on shot volume and a clash on shot volume would say the same thing twice
        key = SAME.get(c["metric"], c["metric"]) if c["kind"] in ("duel", "clash", "edge", "changed") else None
        if per_kind.get(c["kind"], 0) >= MAX_PER_KIND or (key and (key in used or c["metric"] in used)) or len(claims) >= MAX_CALLS:
            continue
        if key:
            used |= {key, c["metric"]}
        per_kind[c["kind"]] = per_kind.get(c["kind"], 0) + 1
        claims.append(c)
    while len(claims) < MIN_CALLS:
        ec = edge_claim(gid, away, home, fp, places, used | {SAME[m] for m in used if m in SAME})
        if ec is None:
            break
        used |= {ec["metric"]}
        claims.append(ec)
    heads = set()
    for i, c in enumerate(claims):
        if c["head"] in heads and c["kind"] == "duel":  # two duels in one game: don't open both the same way
            c["head"] = next((h for h in DUEL_HEADS if h not in heads), c["head"])
        heads.add(c["head"])
        c.pop("gap", None)
        c["id"] = f"{gid}-{i + 1}"
    return claims


def records(sched: list[dict], before: str) -> dict[str, str]:
    """Each team's wins-losses-overtime losses from the games finished before a start time."""
    rec = {}
    for g in sched:
        if not g["final"] or g["start"] >= before:
            continue
        win, lose = (g["home"], g["away"]) if g["hs"] > g["as"] else (g["away"], g["home"])
        rec.setdefault(win, [0, 0, 0])[0] += 1
        rec.setdefault(lose, [0, 0, 0])[1 if g["end"] == "REG" else 2] += 1
    return {t: "-".join(map(str, r)) for t, r in rec.items()}


def bundle(away: str, home: str, site: dict, record: dict) -> dict:
    """The slice of every data file the preview page reads, for these two teams, as it stood when the preview was made.
    Saved inside the snapshot so the page can still be shown, unchanged, after the game."""
    two = lambda d: {**{k: v for k, v in d.items() if k != "teams"}, "teams": {t: d["teams"][t] for t in (away, home) if t in d["teams"]}}
    return {"fingerprints": two(site["fingerprints"]), "lines": two(site["lines"]), "goalies": two(site["goalies"]),
            "goal_sources": two(site["goal_sources"]), "records": {t: record.get(t, "0-0-0") for t in (away, home)}}


def site_files(folder) -> dict:
    return {k: json.loads((folder / f"{k}.json").read_text()) for k in ("fingerprints", "lines", "goalies", "goal_sources")}


def call_extras(site: dict, before: str) -> dict:
    """What some calls need beyond the site files, from games before `before` (the game's start) only, so a rebuilt
    preview sees what a live one would have."""
    import polars as pl
    from pipeline.config import CURRENT_SEASON
    from pipeline.ingest import coaches
    hist = _goalie_history()
    return {"coaches": coaches.as_of(CURRENT_SEASON, before),"stab": site["fingerprints"].get("stabilization") or {}, "goalies": site.get("goalies") or {},
            "pts": _points(before), "hist": hist.filter(pl.col("date") < before[:10]) if hist is not None else None}


def snapshot(g: dict, site: dict, places: dict, sched: list[dict], now: datetime, chance: dict | None = None, **extra) -> dict:
    """`chance` is the win model's morning figure for the game; only the words it picks are saved, never the number."""
    away, home = g["away"], g["home"]
    fp = site["fingerprints"]["teams"]
    claims = calls(g["id"], away, home, fp, site["lines"]["teams"], places, call_extras(site, g["start"]))
    out = {"game_id": g["id"], "start": g["start"], "away": away, "home": home, "venue": g["venue"],
           "snapshot_at": now.isoformat(timespec="seconds"), "rules": RULES_VERSION, "data_as_of": site["fingerprints"]["generated_at"], **extra,
           "claims": claims, "pregame": bundle(away, home, site, records(sched, g["start"]))}
    if chance:
        from pipeline.export import outlook
        out["outlook"] = outlook.notes(g["id"], away, home, chance, fp, places)
    try:  # the plain-English story at the top of the page; the preview stands without it
        from pipeline.export import stories
        out["story"] = stories.preview_story(g["id"], away, home, g["start"], places, claims, out.get("outlook"), site["lines"]["teams"])
    except Exception as e:
        print(f"story for {g['id']} failed: {e!r}")
    return out


def write_index(out_dir) -> int:
    index = []
    for p in sorted(q for q in out_dir.glob("*.json") if q.name != "index.json"):
        s = json.loads(p.read_text())
        index.append({"id": s["game_id"], "start": s["start"], "away": s["away"], "home": s["home"], "claims": len(s["claims"]),
                      "rebuilt": bool(s.get("rebuilt"))})
    (out_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return len(index)


def _chances(games: list[dict]) -> dict[int, dict]:
    """Morning win chances for these games, used only to pick wording. Previews still work without them."""
    if not games:
        return {}
    try:
        from pipeline.config import CURRENT_SEASON
        from pipeline.export import outlook
        return outlook.morning(CURRENT_SEASON, games)
    except Exception as e:  # e.g. tables missing in a test: no matchup note rather than no preview
        print(f"win chances unavailable: {e!r}")
        return {}


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    site = site_files(SITE_DATA)
    out_dir = SITE_DATA / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = frozen = 0
    todo = []
    for g in sched:
        start = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        if start <= now:
            frozen += (out_dir / f"{g['id']}.json").exists()
            continue  # started or finished: an existing snapshot stays exactly as it was at puck drop
        if g["final"] or start > now + timedelta(days=DAYS_AHEAD):
            continue
        if g["away"] in site["fingerprints"]["teams"] and g["home"] in site["fingerprints"]["teams"]:
            todo.append(g)
    chances = _chances(todo)
    for g in todo:
        snap = snapshot(g, site, places, sched, now, chances.get(g["id"]))
        (out_dir / f"{g['id']}.json").write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
        written += 1
    return {"written": written, "frozen": frozen, "indexed": write_index(out_dir)}
