"""Preview snapshots: the "What to watch" calls for every game in the next week.

Every call is a prediction the post-game page grades by rule: a headline and body for reading, a `call` sentence that
says exactly what is predicted, and a `check` (metric, team, baseline, threshold) the recap measures. The rulebook:

  clash     a team's strong trait meets the opponent's weak one: it reaches its usual level tonight
  pace      both teams play fast (or both slow): the game runs faster (or slower) than their usual
  physical  both teams hit a lot: the combined hit score reaches their usual
  matchup   the home team's strongest home matching habit: it gets that matchup again
  edge      the trait where the two sit furthest apart: the team at the better end comes out ahead tonight

Triggers and wording are fixed rules (no free text), picked deterministically from the game id, so a preview can be
generated for any game from the numbers alone. A game's file is rewritten on every run until puck drop, then left alone
here. Once the game is final, pregame.py rebuilds its preview from the games before it whenever RULES_VERSION or
METHODS goes up (the user's decision, 2026-10-12, while the site is in testing), keeping the original saved before
puck drop in raw/previews_live/.

Reads the site JSON the other exports have just written (fingerprints, lines), so it must run after them.
"""
import json
import re
import zlib
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone

from pipeline.export.grammar import and_list as _and, cap as _cap, ordinal as _ordinal, possessive as _poss
from pipeline.config import SITE_DATA

DAYS_AHEAD = 7
RULES_VERSION = 23  # 1: the first rulebook, with ungraded "contrast" notes; 2: every call graded (edge replaces contrast);
# 3: calls picked for interest (duels, changed teams, matchup plans, goalie records), casual voice, pace bar raised
# 4: shot danger told as a chance per shot; point shots split into shots from defensemen and shots from in close
# 5: bodies are numbers-free (ranks in words); the figures move to `nums`, shown under the call; `gist` feeds the story
# 6: the rest edge (one team on the second night of a back-to-back)
# 7: shot volume, shots against and pace quoted as scores, with what they look like as recorded (`see`); ranks as chips
# 8: special-teams calls add what each side has been known for over five seasons of real goals; verb agreement fixed
# 9: shot-quality clashes only top five against bottom five; no turnover clashes (both from a 2023-25 backtest)
# 11: second round of notes: edges carry stakes, fewer semicolons and "league"s, no rest venue, plainer changed calls
# 12: third round of notes: hotter goalie-history calls, "chance would suggest", why shots in close matter, "even though" leans, shared Missing items
# 13: softer leans at the ends ("should be favored", "work cut out"; no "miracle" or "surprise")
# 14: tonight's coach (no matchup call for a new coach without a home habit of his own), "New coach" and "Playoff race"
#     items, season series counted right, no 33rd of 32 (outside-coverage comparison, 2026-10-14)
# 15: matchup, rest and goalie-history calls end in team pills like every other call (figures and source in the tooltips)
# 16: a chattier, hotter voice (Sora, 2026-10-14): more wording per call, hot words only where the numbers are extreme
# 17: sentence rules (Sora, 2026-10-15): one contrast word per sentence, a team named by place once a sentence, and
# an edge body never opens by repeating its headline
# 18: clubs that dress 11 forwards and 7 defensemen: depth leaves those games out and is never an edge for a club that
#     does it most nights; NHL.com's projected lines and status facts in Who to watch (2026-10-16)
# 19: "best against best" only for two of the league's best teams meeting (Sora, 2026-10-16): duels and top-line
#     matchups get other headlines, and the story leads with it when the standings say so
# 20: power-play calls say how many power plays to expect from the two teams' penalties taken and drawn (context only)
# 21: a matchup call never makes the headline, "Round two" rarely does, big milestones outrank Out notes, nothing about a player
#     listed as out, penalty context only when it points the call's way (Sora's review of 2025-26, 2026-10-17)
# 22: a winless or perfect start, or a 6+ game run, leads the story and the headline (Sora, 2026-10-18: the Flyers' 0-6-3)
# 23: Out items say when a club has been hit hardest by absences (games missed weighed by chances and GSAx), and a "Filling in"
#     item tells a forward who rarely takes faceoffs taking a missing center's draws (absences.py; Sora, 2026-10-19)
# 10: Sora's review notes: contrasts joined with "but", edges told plainly, "as far apart as two teams get" only for ranks 26+ apart, coach named first
METHODS = 2  # 2: date cuts use the game's local date (late starts saw their own game). Bump when a change to the numbers behind previews (ratings, traits, chance model) should reach past ones
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


# Shot rates as scores (Sora, 2026-10-12). A team's shot rate weighted for score and venue is no longer a count anyone
# made, so the calls quote it as a score where 100 is the league average, like the arena-scored hits, and then say what
# that looks like as recorded: shot attempts in a game, and shots on goal on the scoreboard. Grading is unchanged (the
# checks still store the rates); only the words are.
SCORES = {"volume": "shot volume score", "suppression": "shots-against score", "pace": "pace score"}
GAME_MIN = 48.7  # minutes of 5-on-5 in a typical game, 2023-24 to 2025-26 (empty-net time aside)


def score(v: float, league: float) -> int:
    return round(100 * v / league)


def per_game(rate: float) -> int:
    """A rate per 60 minutes of 5-on-5 as a count in a typical game."""
    return round(rate * GAME_MIN / 60)


def sog_one(attempts: float) -> int:
    """Shots on goal on the scoreboard (every situation) a team usually has with this many 5-on-5 shot attempts
    (fitted on 2023-24 to 2025-26 team-games, r 0.63; power plays, blocks and misses make it rough)."""
    return round(9.3 + 0.397 * attempts)


def sog_both(shots: float) -> int:
    """Shots on goal on the scoreboard, both teams, with this many 5-on-5 shots between them (blocked ones aside)."""
    return round(22.4 + 0.488 * shots)


# Rank chips under a call: (short name, how a team's figure reads in the chip's tooltip).
CHIP = {
    "volume": ("shot volume", lambda e: f"shot volume score {e['index']}"),
    "suppression": ("shots against", lambda e: f"shots-against score {e['index']}"),
    "pace": ("pace", lambda e: f"pace score {e['index']}"),
    "quality": ("shot quality", lambda e: f"{a_pct(e['v'])} chance of scoring on the average shot"),
    "qualityAllowed": ("dangerous shots allowed", lambda e: f"{a_pct(e['v'])} chance of scoring on the average shot against"),
    "turnover": ("chances off turnovers", lambda e: f"{e['v']:.1f}% of chances right after winning the puck"),
    "breakdowns": ("avoiding breakdowns", lambda e: f"{e['v']:.2f} defensive breakdowns per 60"),
    "pp": ("power play", lambda e: f"{e['v']:.1f} expected goals per 60 on the power play"),
    "pk": ("penalty kill", lambda e: f"{e['v']:.1f} expected goals allowed per 60 shorthanded"),
    "physical": ("physicality", lambda e: f"hit score {e['index']}"),
    "forecheck": ("forecheck", lambda e: f"forecheck score {e['index']}"),
    "dShots": ("shots from defensemen", lambda e: f"{e['v']:.0f}% of shot attempts from defensemen"),
    "inClose": ("shots from in close", lambda e: f"{e['v']:.0f}% of shots from within 20 feet"),
    "depth": ("depth", lambda e: f"{e['v']:.0f}% of forward ice time to the bottom six"),
}


def chip(t: str, k: str, e: dict, places: dict, cite: str, text: str | None = None, when: str = "") -> dict:
    """One rank chip: the team's abbreviation and rank, with the figure and the source in its tooltip."""
    return {"team": t, "text": text or f"{_ordinal(e['rank'])} {CHIP[k][0]}",
            "tip": f"{places.get(t, t)}: {CHIP[k][1](e)}, {_ordinal(e['rank'])} of 32{when}. {cite.replace(' · ', ', ')}."}


STRONG, WEAK = 70, 35  # league percentiles (higher is better) for a "meets a soft spot" call
# Shot quality only meets a soft spot as a call at the ends: top five against bottom five held 54% over 2023-25
# against 49% at the 70/35 bar (Sora chose the higher bar, 2026-10-12). Turnover clashes are not made at all:
# they held 34% at 70/35 and 29% at the ends, since a team with a big turnover habit rarely beats its own usual.
CLASH_ENDS = {"quality": 5}
NO_CLASH = {"turnover"}
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
         "{X} are going to test {Yp} goalie early and often", "Somebody warn {Yp} goalie", "{X} are going to let it fly"],
        ["{X} shoot a lot, and {Y} are {yw} at keeping shots down. Look for a big shot total.",
         "*Few teams shoot like {X}, and {Y} are {yw} at keeping shots away. It could be a long night in {Yp} end.",
         "{Yc} has been giving up shots all season, and {X} rarely pass one up.",
         "*{X} shoot from anywhere, and {Y} are {yw} at stopping it. {Yp} goalie had better be ready from the opening faceoff.",
         "{X} love to shoot, and {Y} keep letting teams do it. Get ready for a shooting gallery."],
    ),
    "quality": (
        ["{X} should get some good looks", "{Y} give up good looks, and {X} take them", "{Yp} goalie is in for some dangerous shots",
         "Quality over quantity for {X}", "{X} smell blood"],
        ["{X} make their shots count, and {Y} are {yw} at preventing dangerous shots. Expect {X} to get some real chances.",
         "*{Xp} shots are some of the most dangerous in the league, and {Y} are {yw} at preventing dangerous ones. Look for plenty of good chances.",
         "{Y} have struggled to prevent dangerous shots, and {X} don’t waste many opportunities.",
         "*When {X} shoot, it’s usually a real chance, and {Y} are {yw} at stopping that. {Yp} goalie is going to be busy in all the wrong ways."],
    ),
    "turnover": (
        ["{Y} can’t afford to cough it up", "{X} feed on mistakes, and {Y} make them", "Watch {Yp} puck management",
         "Turnovers could swing this one"],
        ["{X} turn stolen pucks into chances, and {Y} are {yw} at avoiding breakdowns. A sloppy pass or two could cost {Y}.",
         "*Almost nobody punishes turnovers like {X}, and {Y} give the puck away in bad spots.",
         "{Y} give up more dangerous chances after losing the puck than most, and {X} pounce."],
    ),
    "pp": (
        ["{Xp} power play has a target", "Penalties could cost {Y}", "Special teams lean toward {X}", "{Y} can’t afford to take penalties",
         "Stay out of the box, {Yn}"],
        ["{Xp} power play is {xw}, and {Yp} kill is {yw}. Every trip to the box hurts {Y} more than most.",
         "{Yp} penalty kill is {yw}, and {Xp} power play is {xw}. Discipline matters more than usual for {Y} tonight.",
         "*{Xp} power play is one of the league’s best, and {Yp} kill is {yw}. {Y} need to stay out of the box."],
    ),
}
PACE_WORDS = {
    "fast": (["Expect a track meet", "Shots should fly both ways", "Nobody’s sitting back in this one", "Buckle up"],
             "{A} play some of the league’s fastest games, and so do {H}. We think this one runs hotter than either team’s usual."),
    "slow": (["Expect a grind", "Bring a pillow", "Don’t blink and you still might miss the shots", "Trench warfare", "Every inch will be earned"],
             "{A} play some of the league’s slowest games, and so do {H}. We think this one is even quieter than either team’s usual."),
}
# The gist of each call, one short sentence with no numbers, for the story at the top of the page.
CLASH_GIST = {"volume": "{X} shoot a lot, and {Y} give up plenty.", "quality": "{X} make their shots count, and {Y} allow dangerous ones.",
              "turnover": "{X} pounce on loose pucks, and {Y} cough them up.", "pp": "{Xp} power play has a soft target in {Yp} kill."}


def short(words: str) -> str:
    """A second standing in one sentence without saying "league" again (Sora, 2026-10-12: the word was everywhere)."""
    return words.replace("the league’s ", "the ").replace(" of the league", "")


def league_once(text: str) -> str:
    """Says "league" once: any standing after the first mention is shortened."""
    i = text.find("league")
    return text if i < 0 else text[:i + 6] + short(text[i + 6:]).replace(" in the league", "")


def standing(rank: int) -> str:
    """A league rank (1 best of 32) in words, for numbers-free prose."""
    return ("the league’s best" if rank == 1 else "one of the league’s best" if rank <= 5 else "near the top of the league" if rank <= 10
            else "around the middle of the league" if rank <= 22 else "near the bottom of the league" if rank <= 27
            else "one of the league’s worst" if rank <= 31 else "the league’s worst")
PHYSICAL_HEADS = ["Bodies will fly", "This one’s going to leave marks", "Two of the league’s heaviest-hitting teams",
                  "Keep your head up", "Ice packs on standby"]
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
DUEL_HEADS = ["Something has to give", "Strength against strength", "The matchup inside the matchup", "Who blinks first?",
              "Unstoppable force, meet immovable object", "Heavyweight bout"]
DUEL_LEAN = {  # who we side with
    "offense": ["We think {X} still get theirs.", "Our lean: {Xp} {oname} wins out.", "We’ll side with {X}.",
                "Hot take: {X} break through anyway."],
    "defense": ["Our lean: {Y} hold the line.", "We’ll side with {Yp} defense.", "We think {Y} win this one.",
                "Hot take: {Y} shut it down."],
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
CHANGED_HEADS = {"better": ["{Xp} {thing} is better than you remember", "Don’t judge {X} by last season", "{X} have changed",
                            "Forget what you knew about {X}", "Meet the new {Xn}"],
                 "worse": ["{Xp} {thing} isn’t what it was", "Not the {Xn} you remember", "{X} have slipped",
                           "What happened to {Xp} {thing}?"]}
CHANGED_HEADS_PHYSICAL = {"better": ["{X} are hitting more than you remember", "{X} have toughened up"],
                          "worse": ["{X} aren’t hitting like they used to", "Not the {Xn} you remember"]}

# Home matchups, told as what the coach is doing: hunting a mismatch for his scorers, sending a checking line at the
# other team's stars, or going power against power.
MATCHUP_WORDS = {
    "hunt": (["{H} will hunt a mismatch", "{Hstar}’s line gets the soft minutes", "{H} want {Hstar} away from the big guns"],
             "{Coach} has been chasing an easier matchup for the team’s scorers."),
    "shadow": (["A shadow job for {mine}", "{H} will send their checkers at {Ostar}", "{Ostar} gets company tonight"],
               "{Coach} has been using this group as a checking line on {theirline}."),
    "best": (["Top line on top line: {Hstar} against {Ostar}", "{H} want {Hstar} head to head with {Ostar}", "Power against power"],
             "{Coach} has been choosing strength on strength."),
    "even": (["{H} like {Hstar}’s line against {Ostar}’s", "Like for like: {Hstar} against {Ostar}"],
             "{Coach} keeps choosing line for line."),
    "pair": (["{mine} get the {Ostar} assignment", "{H} will send {mine} at {Ostar}", "A tough night ahead for {Ostar}"],
             "{Coach} has been picking a defense pair for {theirline}."),
}

# Goalie records against one team: tested over 2023-24 to 2026-27 (911 goalie-opponent pairs with four or more
# starts), a goalie's saves above expected against one team in half his meetings predicts the other half not at all
# (r = -0.02). So a striking record against tonight's opponent is a coincidence, and the call says so.
HISTORY_MIN_STARTS, HISTORY_MIN_SHOTS, HISTORY_GAP = 5, 120, 0.03  # save-percentage gap from his usual
HISTORY_HEADS = {"good": ["Don’t buy {Gp} record against {O}", "{Gp} hold on {O} won’t last", "{G} doesn’t own {O}"],
                 "bad": ["{G} is due against {O}", "Don’t buy {Gp} slump against {O}", "{O} don’t own {G}",
                         "There’s no curse: {G} against {O}"]}
# A hotter take than "it's a coincidence" (Sora, 2026-10-12): say what to expect instead.
HISTORY_BODIES = {
    "good": ["{G} has been a wall against {O} since 2023-24, well above his usual. But three seasons of data say a goalie’s record "
             "against one team tells us nothing about the next meeting, so don’t be surprised if {G} isn’t that wall tonight.",
             "Since 2023-24, {G} has been a wall against {O}, well above his usual. Fans love a pattern like this, "
             "but we checked three seasons of them and they predicted nothing. Don’t be surprised if {O} finally get to him."],
    "bad": ["{G} has been shaky against {O} since 2023-24, well below his usual. But three seasons of data say a goalie’s record "
            "against one team tells us nothing about the next meeting, so don’t be surprised if he looks like himself tonight.",
            "Since 2023-24, {G} has been shaky against {O}, well below his usual. Fans love a pattern like this, "
            "but we checked three seasons of them and they predicted nothing. Don’t be surprised if he’s back to his usual self."],
}
PRIORITY = {"duel": 0, "changed": 1, "matchup": 2, "history": 3, "rest": 3, "clash": 4, "pace": 5, "physical": 5, "edge": 6}
MAX_CALLS, MIN_CALLS, MAX_PER_KIND = 4, 2, 2


def _pick(pool: list[str], *key) -> str:
    return pool[zlib.crc32("-".join(map(str, key)).encode()) % len(pool)]


MIN_HOME_GAMES, MIN_MINUTES = 3, 30  # before a home matching habit becomes a call
COACH_HOME_GAMES = 10  # a habit is tonight's coach's own once he has coached this many of its home games, or half
ORD = ["first", "second", "third", "fourth"]
NICKS: dict[str, str] = {}  # team -> nickname, from teams.json (filled by `names`)


def _last(name: str) -> str:
    return name.split(" ", 1)[-1]


def _lasts(names: list[str]) -> list[str]:
    """Last names, with a first initial where two share one (the Protas brothers)."""
    lasts = [_last(n) for n in names]
    return [f"{n[0]}. {l}" if lasts.count(l) > 1 else l for n, l in zip(names, lasts)]


def _games(a: int, b: int) -> str:
    """How many games this season each side's figures include, for a call's citation."""
    return "no games yet this season" if a == b == 0 else f"{a} and {b} games this season"


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


def special_teams_record(c: dict, places: dict, surprises) -> str | None:
    """A sentence on what the two teams' special teams have been known for over five seasons of real goals, for a
    power-play or penalty-kill call (Sora, 2026-10-12: a famous kill or a famously bad power play is part of the story,
    not just this season's chances). Ends in "though" when the record cuts against the call."""
    m, kind = c.get("metric"), c.get("kind")
    chk = c.get("check") or {}
    if m not in ("pp", "pk") or kind not in ("clash", "duel", "edge"):
        return None
    if kind == "edge":
        top, bot = chk.get("team", c.get("team")), chk.get("opp", c.get("opp"))
        roles = [(top, m, 1), (bot, m, -1)]  # the call says top is better than bot
    else:
        roles = [(c["team"], "pp", 1), (c["opp"], "pk", -1)]  # the call says this power play does well against that kill
    found = [(k[0] * want, k[1]) for t, dim, want in roles if t and (k := surprises.known_for(t, dim, places))]
    if not found:
        return None
    text = surprises._and([w for _, w in found])
    against = all(s < 0 for s, _ in found)
    return f"{text[0].upper()}{text[1:]}{', though' if against else ''}."


def _fmt(text: str, **w) -> str:
    """Fill a template and capitalise each sentence's first letter ("the Blues ..." at the start becomes "The Blues")."""
    out = text.format(**w)
    out = _cap(out)
    return ". ".join(_cap(s) if s else s for s in out.split(". "))


# How many power plays a team should get: half from how often the opponent goes shorthanded, half from how often it
# draws calls (fingerprints' `taken` and `drawn`, per 60 of play). Over 2024-25 to 2026-27 a team 0.15+ above the
# league on this got 2.90 power plays a game and one 0.15+ below got 2.34 (2.67 for all), but a graded call on the
# count held only about 4 in 10 against 1 in 3 by chance, so it is told as context on power-play calls, never graded
# (Sora chose "Context only", 2026-10-16). The ends are about the top and bottom seventh of matchups.
PP_CHANCES = 0.15
PEN_TAKES, PEN_DRAWS = 30, 70  # percentiles that count as a reason: takes a lot (fewer is better), draws a lot
PEN_MORE = ["{Y} take a lot of penalties and {X} draw plenty of them, so their power play should get its chances.",
            "{Y} take a lot of penalties, so {Xp} power play should get its chances.",
            "{X} draw a lot of penalties, so their power play should get its chances."]
PEN_FEWER = ["{Y} rarely take penalties and {X} rarely draw them, so their power play may not get many chances.",
             "{Y} rarely take penalties, so {Xp} power play may not get many chances.",
             "{X} rarely draw penalties, so their power play may not get many chances."]
PEN_BOTH = {True: "These two hand each other plenty of power plays, so special teams should get a real say.",
            False: "These two rarely give each other power plays, so special teams may not get much of a say."}


def penalty_context(c: dict, fp: dict, places: dict) -> str | None:
    """A sentence on how many power plays to expect, for a power-play or penalty-kill call: the second link of a
    chain whose first link is the graded call. Clashes and duels follow the call's power play; an edge (which compares
    both sides' special teams) gets the game as a whole."""
    m, kind = c.get("metric"), c.get("kind")
    if m not in ("pp", "pk") or kind not in ("clash", "duel", "edge"):
        return None
    try:
        e = lambda t, k: fp[t]["dims"][k]["blend"]
        league = sum(e(t, "taken")["v"] for t in fp) / len(fp)
        chances = lambda x, y: (e(y, "taken")["v"] + e(x, "drawn")["v"]) / 2 - league
        if kind == "edge":
            a, h = c["team"], c["opp"]
            both = (chances(a, h) + chances(h, a)) / 2
            sides = [chances(a, h), chances(h, a)]
            if both >= PP_CHANCES and min(sides) > 0:
                return PEN_BOTH[True]
            if both <= -PP_CHANCES and max(sides) < 0:
                return PEN_BOTH[False]
            return None
        x, y = c["team"], c["opp"]
        v = chances(x, y)
        tk, dr = e(y, "taken")["pct"], e(x, "drawn")["pct"]
    except (KeyError, TypeError, ZeroDivisionError):  # fingerprints saved before `taken` and `drawn`
        return None
    w = {**team_words(x, places, "X"), **team_words(y, places, "Y")}
    # Only when it points the call's way (Sora's review of 2025-26, 2026-10-17: "their power play should get its chances"
    # under a call that the kill would hold): a power-play call wants more power plays, a penalty-kill call fewer.
    wants_more = (m == "pp") if kind == "clash" else ((m == "pp") == ((c.get("lean") or "offense") == "offense"))
    if v >= PP_CHANCES and wants_more:
        why = (tk <= PEN_TAKES, dr >= PEN_DRAWS)
        pool = PEN_MORE
    elif v <= -PP_CHANCES and not wants_more:
        why = (tk >= 100 - PEN_TAKES, dr <= 100 - PEN_DRAWS)
        pool = PEN_FEWER
    else:
        return None
    if not any(why):
        return None
    return _fmt(pool[0] if all(why) else pool[1] if why[0] else pool[2], **w)


def clash_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    out = []
    for off, dfn, oname, dname in CLASHES:
        if off in NO_CLASH:
            continue
        for x, y in ((away, home), (home, away)):
            o, d = fp[x]["dims"][off]["blend"], fp[y]["dims"][dfn]["blend"]
            ends = CLASH_ENDS.get(off)
            if ends and not (o["rank"] <= ends and d["rank"] > len(fp) - ends):
                continue
            if o["pct"] >= STRONG and d["pct"] <= WEAK:
                words = {**team_words(x, places, "X"), **team_words(y, places, "Y"),
                         "xr": _ordinal(o["rank"]), "yr": _ordinal(d["rank"]), "xv": SAY[off](o["v"]),
                         "xw": standing(o["rank"]), "yw": standing(d["rank"])}
                league = o["v"] * 100 / o["index"] if o.get("index") else None
                cite = f"Playing style · this season blended with last · {_games(fp[x]['games'], fp[y]['games'])}"
                sc = off in SCORES and league
                if sc:
                    n = per_game(o["v"])
                    words["xv"] = f"score {o['index']}"
                    call = f"{_cap(words['Xp'])} {SCORES[off]} is at least their usual {o['index']}."
                    see = f"About {n} shot attempts at 5-on-5, usually around {sog_one(n)} shots on goal on the scoreboard."
                else:
                    call, see = _cap(CLASH_CALL[off](words, o["v"])), None
                out.append({
                    "kind": "clash", "gap": o["pct"] - d["pct"], "metric": off, "team": x, "opp": y,
                    "head": _fmt(_pick(CLASH_WORDS[off][0], gid, off, x), **words),
                    "call": call, **({"see": see} if see else {}),
                    "body": league_once(_fmt(_pick([b.lstrip("*") for b in CLASH_WORDS[off][1] if o["rank"] <= TOP or not b.startswith("*")], gid, off, x, "body"), **words)),
                    "nums": f"{places[x]} {words['xr']} for {oname} ({words['xv']}) · {places[y]} {words['yr']} for {dname}",
                    "chips": [chip(t_, k_, e_, places, cite) for t_, k_, e_ in ((x, off, o), (y, dfn, d)) if k_ in CHIP],
                    "gist": _fmt(CLASH_GIST[off], **words),
                    "cite": cite,
                    "check": {"metric": off, "measure": MEASURE[off], "team": x, "baseline": round(o["v"], 4),
                              "league": round(league, 4) if league else None, "direction": "above", **({"scale": "score"} if sc else {}),
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
            body = "{X} are " + standing(o["rank"]) + " for " + oname + ", but {Y} are " + short(standing(d["rank"])) + " at " + dname + ". " + lean_txt
            cite = f"Playing style · this season blended with last · {_games(fp[x]['games'], fp[y]['games'])}"
            sc = off in SCORES
            see = None
            if sc:
                n = per_game(lo)
                words["xv"] = f"score {o['index']}"
                call = (f"{_cap(words['Xp'])} {SCORES[off]} is above 100, the league average, against {words['Y']}." if side == "offense"
                        else f"{_cap(words['Y'])} keep {words['Xp']} {SCORES[off]} under 100, the league average.")
                see = (f"More than {n} shot attempts for {words['X']} at 5-on-5, which usually means {sog_one(n)} or more shots on goal on the scoreboard."
                       if side == "offense" else
                       f"Fewer than {n} shot attempts for {words['X']} at 5-on-5, which usually means {sog_one(n)} or fewer shots on goal on the scoreboard.")
            else:
                call = _cap(DUEL_CALL[off][side](words, lo))
            out.append({
                "kind": "duel", "gap": min(o["pct"], d["pct"]), "metric": off, "team": x, "opp": y, "lean": side,
                "head": _fmt(_pick(DUEL_HEADS, gid, off, x), **words),
                "body": _fmt(body, **words), "call": call, **({"see": see} if see else {}),
                "nums": f"{places[x]} {words['xr']} for {oname} ({words['xv']}) · {places[y]} {words['yr']} for {dname}",
                "chips": [chip(t_, k_, e_, places, cite) for t_, k_, e_ in ((x, off, o), (y, dfn, d)) if k_ in CHIP],
                "gist": _fmt("It’s strength against strength: {X} are " + standing(o["rank"]) + " for " + oname + ", but {Y} are " + short(standing(d["rank"])) + " at " + dname + ".", **words),
                "cite": cite,
                "check": {"metric": off, "measure": MEASURE[off], "team": x, "baseline": round(lo, 4), "usual": round(o["v"], 4),
                          "league": round(lo, 4), "direction": "above" if side == "offense" else "below", **({"scale": "score"} if sc else {}),
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
            sc = k in SCORES
            show = (lambda v: f"score {v:.0f}") if sc else EDGE[k][4] if k in EDGE else (lambda v: f"{v:.1f}")
            sv, bv = (s["index"], b["index"]) if k in INDEX_TRAITS or sc else (s["v"], b["v"])
            sb, ss = standing(b["rank"]), standing(s["rank"])
            body = (f"Taken over this season and last, {words['X']} are {sb} for {name}. Look at this season alone, though, and they’ve been "
                    f"{better if up else worse}{'' if ss == sb else ', ' + short(ss)}. "
                    f"We think tonight looks more like this season’s {words['Xn']}.")
            # "above" the blend means a bigger figure; for traits where lower is better, better means below
            direction = "above" if up == higher else "below"
            cite = f"Playing style · blended and this season only · {games} games this season"
            if sc:
                call = f"{_cap(words['Xp'])} {SCORES[k]} is {direction} their blended level of {bv:.0f}."
                what = "shot attempts" if k == "volume" else "shot attempts against"
                see = f"About {per_game(s['v'])} {what} at 5-on-5 the way they’ve played this season, against {per_game(b['v'])} at their blended level."
            else:
                call = (f"{_cap(words['X'])} hit {'more' if up else 'less'} than their blended level ({show(bv)})." if k == "physical" else
                        f"{_cap(words['Xp'])} {thing} is {'better' if up else 'worse'} than their blended level of {show(bv)}.")
                see = None
            out.append({
                "kind": "changed", "gap": abs(s["pct"] - b["pct"]), "metric": k, "team": t, "opp": o,
                "head": _fmt(_pick((CHANGED_HEADS_PHYSICAL if k == "physical" else CHANGED_HEADS)["better" if up else "worse"], gid, k, t), **words),
                "body": _cap(body),
                "nums": f"{_cap(name)}: {_ordinal(b['rank'])} over two seasons ({show(bv)}), {_ordinal(s['rank'])} this season ({show(sv)})",
                "gist": f"{_cap(words['X'])} aren’t the team you remember for {name}.",
                "call": call, **({"see": see} if see else {}),
                "chips": [chip(t, k, b, places, cite, f"{_ordinal(b['rank'])} over two seasons", " over this season and last"),
                          chip(t, k, s, places, cite, f"{_ordinal(s['rank'])} this season", " this season")] if k in CHIP else [],
                "cite": cite,
                "check": {"metric": k, "team": t, "baseline": round(b["v"], 4), "season": round(s["v"], 4), "index_traits": k in INDEX_TRAITS,
                          "league": round(b["v"] * 100 / b["index"], 4) if b.get("index") else None, "direction": direction, **({"scale": "score"} if sc else {}),
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
                  coach: str | dict | None = None) -> dict | None:
    """`coach` is the home team's head coach tonight (a name, or coaches.for_game's dict); without one the call says
    \"their coach\". A habit mostly built under an earlier coach is not his plan, so no call then (eye test on 2025-26,
    2026-10-13: "John Tortorella keeps choosing..." rested on home games coached by Bruce Cassidy)."""
    h, a = lines.get(home), lines.get(away)
    if not h or not a or not h.get("matchups"):
        return None
    mu = h["matchups"]["home"]
    if mu["games"] < MIN_HOME_GAMES:
        return None
    if isinstance(coach, dict):
        if coach["home"] < min(mu["games"], COACH_HOME_GAMES) and coach["home"] * 2 < mu["games"]:
            return None
        coach = coach["coach"]
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
    if style == "shadow" and n < 3:  # a second line on a first line is a tough-minutes job, not a checking line
        heads = [h for h in heads if "checkers" not in h]
        why = "{Coach} has been giving this group the hard job of facing {theirline}."
    w = {**team_words(home, places, "H"), **team_words(away, places, "O"), "mine": mine, "theirs": theirs,
         "Hstar": _star(unit, pts), "Ostar": _star(opp, pts)}
    theirline = "the other team’s top line" if j == 0 else f"opponents’ {ORD[j]} lines"
    group = f"{mine}" if lab == "P" else f"{w['Hstar']}’s line ({mine})"
    body = (f"At home, {w['H']} keep sending {group} out against opponents’ {ORD[j]} lines, far more often than chance would suggest. "
            f"{why.format(theirline=theirline, Coach=_cap(coach or 'their coach'))} Tonight {w['Op']} {ORD[j]} line is {theirs}.")
    return {
        "kind": "matchup", "metric": "matchup_share", "team": home, "opp": away, "style": style,
        "head": _fmt(_pick(heads, gid, "matchup"), **w),
        "call": f"{_cap(w['Hp'])} {r['label']} ({mine}) spends at least {v}% of its 5-on-5 time against {w['Op']} {ORD[j]} line.",
        "body": _cap(body),
        "nums": f"At home: {v}% of this group’s 5-on-5 time against {ORD[j]} lines, about {r['expected'][j]}% with no line matching",
        "gist": f"At home, {coach or 'their coach'} likes to pick matchups, so watch who goes out against {w['Op']} {ORD[j]} line.",
        "cite": f"Matchups · {places[home]} home games this season · {mu['games']} games, {r['minutes']:.0f} minutes for this group at 5-on-5",
        "chips": [
            {"team": home, "text": f"{v}% vs {ORD[j]} lines at home",
             "tip": f"{places[home]}’s {r['label']} ({mine}) at home: {v}% of its 5-on-5 time against opponents’ {ORD[j]} lines. "
                    f"Matchups, {places[home]} home games this season, {mu['games']} games, {r['minutes']:.0f} minutes for this group at 5-on-5."},
            {"team": home, "text": f"{r['expected'][j]}% with no matching",
             "tip": f"With no line matching, this group would face {ORD[j]} lines about {r['expected'][j]}% of the time, about as often as those lines are on the ice."},
        ],
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


def history_claims(gid: int, away: str, home: str, goalies_site: dict, hist, places: dict, likely: dict | None = None) -> list[dict]:
    """A goalie (the likely starter, else his team's busiest this season) whose record against tonight's opponent is
    far from his usual. `hist` holds only starts before the game (see `call_extras`); `likely` is starters.upcoming's
    entry for the game."""
    if hist is None:
        return []
    import polars as pl
    out = []
    for t, o in ((away, home), (home, away)):
        roster = goalies_site.get("teams", {}).get(t) or []
        if not roster:
            continue
        first = ((likely or {}).get(t) or [(None, 0)])[0][0]
        g = next((x for x in roster if x["id"] == first), None) or max(roster, key=lambda x: ((x.get("season") or {}).get("starts", 0), (x.get("blend") or {}).get("starts", 0)))
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
        body = _pick(HISTORY_BODIES["good" if good else "bad"], gid, g["id"], "body").format(**w)
        out.append({
            "kind": "history", "gap": abs(sv_vs - sv_all) * 1000, "metric": "goalie_sv", "team": t, "opp": o,
            "head": _fmt(_pick(HISTORY_HEADS["good" if good else "bad"], gid, g["id"]), **w),
            "body": _cap(body),
            "nums": f"{n} starts against {places.get(o, o)}: {fmt(sv_vs)} save percentage · {fmt(sv_all)} against everyone else",
            "gist": f"Don’t read much into {_poss(G)} record against {w['O']}.",
            "call": f"If {G} starts, his save percentage lands closer to his usual {fmt(sv_all)} than to his {fmt(sv_vs)} against {w['O']}.",
            "cite": f"Goalie starts since 2023-24 · {n} against {places.get(o, o)}, {all_sa - sa:,} shots against everyone else",
            "chips": [
                {"team": t, "text": f"{fmt(sv_vs)} in {n} starts vs {places.get(o, o)}",
                 "tip": f"{G}: {fmt(sv_vs)} save percentage in {n} starts against {places.get(o, o)} since 2023-24."},
                {"team": t, "text": f"{fmt(sv_all)} against everyone else",
                 "tip": f"{G}: {fmt(sv_all)} save percentage against everyone else since 2023-24, on {all_sa - sa:,} shots."},
            ],
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
EDGE_SCORE_CALL = {"volume": "{topp} shot volume score is higher than {botp}.", "suppression": "{topp} shots-against score is lower than {botp}."}
EDGE_SEE = {"volume": "{top} out-attempting {bot} at 5-on-5, usually by about {d}.",
            "suppression": "{top} allowing fewer shot attempts at 5-on-5 than {bot}, usually about {d} fewer."}
EDGE_HEADS = {  # {top} is the team at the better (or higher) end of the scale, {bot} the other
    "volume": ["{top} will outshoot {bot}", "Volume against patience", "{top} will pepper {bot}"],
    "quality": ["{top} hunt better looks than {bot}", "Picky against trigger-happy"],
    "suppression": ["{top} lock it down, {bot} don’t", "One stingy defense, one leaky one", "A brick wall against a revolving door"],
    "qualityAllowed": ["{top} shut down the dangerous stuff, {bot} don’t", "Sheltered against exposed"],
    "point": ["{top} fire from the point, {bot} work down low", "Two different ideas of a good shot"],
    "dShots": ["{topp} defense joins the attack, {botp} stays home", "Active D against forward-led"],
    "inClose": ["{top} get in close, {bot} shoot from distance", "In close against from distance"],
    "forecheck": ["{top} hound the puck, {bot} sit back", "Forecheck against patience"],
    "physical": ["Bruisers against finesse", "{top} hit, {bot} mostly don’t", "{bot} better keep their heads up"],
    "depth": ["{top} roll four lines, {bot} lean on their top six", "Depth against star power"],
    "pp": ["One power play bites, one doesn’t", "{topp} power play outclasses {botp}", "{topp} power play is a weapon"],
    "pk": ["{top} kill penalties far better than {bot}", "Airtight against leaky on the kill"],
}
# Each edge told as a plain contrast first (Sora, 2026-10-12: "Vegas' defensemen shoot a lot, LA's not so much"), with
# "but" so the two halves read as against each other.
EDGE_PLAIN = {
    "volume": "{top} shoot a lot, but {bot} don’t.",
    "quality": "{top} wait for good looks, but {bot} settle for hopeful ones.",
    "suppression": "{top} keep shots away, but {bot} give up plenty.",
    "qualityAllowed": "{top} keep dangerous shots away, but {bot} give up a lot of them.",
    "point": "{top} fire from the point, but {bot} work down low.",
    "dShots": "{topp} defensemen shoot a lot, and {botp} not so much.",
    "inClose": "{top} get in close, but {bot} mostly shoot from distance.",
    "forecheck": "{top} hound the puck, but {bot} sit back.",
    "physical": "{top} hit a lot, but {bot} mostly don’t.",
    "depth": "{top} roll four lines, but {bot} lean on their top six.",
    "pp": "{topp} power play bites, but {botp} doesn’t.",
    "pk": "{top} kill penalties well, but {bot} don’t.",
}
EDGE_BODIES = ["{plain} {topc} is {wt} for {name}, and {botc} is {wbs}.",
               "On {name}, {topc} is {wt}, but {botc} is {wbs}."]
# Why the gap matters tonight, so an edge has stakes (Sora, 2026-10-12: "why care about this difference").
EDGE_STAKES = {
    "volume": "Expect {top} to spend more of the night in the attacking zone.",
    "quality": "The more dangerous chances should be {topp}.",
    "suppression": "Expect {bot} to give up the bigger pile of shots.",
    "qualityAllowed": "The more dangerous chances should come at {botp} net.",
    "point": "Watch {topp} defensemen let it go from the blue line.",
    "dShots": "Watch {topp} defensemen jump into the play.",
    "inClose": "Shots from in close tend to be more dangerous, so expect {top} to do more of their damage around the net.",
    "forecheck": "Expect {top} to win more pucks back deep in {botp} end.",
    "physical": "Expect most of the big hits to come from {top}.",
    "depth": "If this one comes down to the bottom six, that suits {top}.",
    "pp": "Every penalty {bot} take could cost them.",
    "pk": "Penalties should hurt {bot} more than {top}.",
}
_FILLER = {"the", "a", "an", "and", "but", "of", "to", "in", "on", "is", "are", "for", "their", "they", "it", "mostly", "a lot"}


def echoes(head: str, sentence: str) -> bool:
    """A body sentence that only says the headline again (Sora, 2026-10-15: "The Hurricanes hound the puck, the Jets
    sit back" over "The Hurricanes hound the puck, but the Jets sit back."): nearly every headline word is in it and
    it adds hardly anything."""
    words = lambda t: set(re.findall(r"[a-z’']+", t.lower())) - _FILLER
    h, b = words(head), words(sentence)
    return bool(h) and len(h & b) >= 0.6 * len(h) and len(b - h) <= 3


EDGE_FAR = 26  # "about as far apart as two teams get" only for ranks this far apart, say 3rd and 29th (Sora, 2026-10-12)
EDGE_GAP, EDGE_END = 50, 25  # a "hot enough" edge: percentiles 50+ apart, with one team in the top or bottom quarter
# A filler edge (the widest gap, but not a hot one) is told plainly: the two teams are not at opposite ends of anything.
EDGE_MILD_HEADS = ["{topp} edge in {name}", "Where {top} have the edge: {name}"]
EDGE_WHY = {"inClose": " Shots from in close tend to be the more dangerous ones."}  # context for a small gap (Sora, 2026-10-12)
EDGE_MILD_BODY = "{top} are {wt} for {name}, and {bot} are {wbs}. It’s the widest gap between these two, though not a huge one."
EDGE_SMALL = 40  # below this percentile gap the widest gap is said to be a small one (Sora, 2026-10-12)
EDGE_SMALL_BODY = "{top} are {wt} for {name}, and {bot} are {wbs}. That’s the biggest gap between these two, and it isn’t a big one."


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


def plays_eleven(fp: dict, t: str) -> bool:
    """A club that dresses 11 forwards and 7 defensemen most nights (`units.plays_eleven`): its bottom six is five
    players by design, so depth is never a call or an edge for its games (Tampa Bay, 2026-10-15: "If this one comes
    down to the bottom six, that suits the Kraken")."""
    return bool((fp[t].get("lineup") or {}).get("habit"))


def edge_claim(gid: int, away: str, home: str, fp: dict, places: dict, skip: set, hot_only: bool = False) -> dict | None:
    """The trait where the two teams sit furthest apart, called as: the team at the better end comes out ahead tonight.
    With `hot_only`, only a gap wide enough to be worth a call (EDGE_GAP, EDGE_END); otherwise the widest one."""
    gap = lambda k: abs(fp[away]["dims"][k]["blend"]["pct"] - fp[home]["dims"][k]["blend"]["pct"])
    keys = [k for k in EDGE if k not in skip and k in fp[away]["dims"] and k in fp[home]["dims"]
            and not (k == "depth" and any(plays_eleven(fp, t) for t in (away, home)))]
    if hot_only:
        keys = [k for k in keys if gap(k) >= EDGE_GAP and max(fp[t]["dims"][k]["blend"]["pct"] for t in (away, home)) >= 100 - EDGE_END
                and min(fp[t]["dims"][k]["blend"]["pct"] for t in (away, home)) <= EDGE_END]
    if not keys:
        return None
    k = max(keys, key=gap)
    name, lo, hi = EDGE[k][:3]
    a, h = fp[away]["dims"][k]["blend"], fp[home]["dims"][k]["blend"]
    hot = gap(k) >= EDGE_GAP and max(a["pct"], h["pct"]) >= 100 - EDGE_END and min(a["pct"], h["pct"]) <= EDGE_END
    top, bot = (away, home) if a["pct"] >= h["pct"] else (home, away)
    rt, rb = (a, h) if top == away else (h, a)
    tw, bw = team_words(top, places, "top"), team_words(bot, places, "bot")
    cite = f"Playing style · this season blended with last · {_games(fp[away]['games'], fp[home]['games'])}"
    chk = edge_check(k, top, bot, fp)
    sc = k in SCORES
    if sc:
        chk["scale"] = "score"
    w = {"name": ("the " + name) if k in ("pp", "pk") else name, **tw, **bw, "rt": _ordinal(rt["rank"]), "rb": _ordinal(rb["rank"]), "hi": hi, "lo": lo,
         "wt": standing(rt["rank"]), "wb": standing(rb["rank"]), "wbs": short(standing(rb["rank"]))}
    head = _fmt(_pick(EDGE_HEADS[k] if hot else EDGE_MILD_HEADS, gid, k), **w)
    plain = _fmt(EDGE_PLAIN.get(k, ""), **w)
    body = _pick(EDGE_BODIES, gid, k, "body")
    if "{plain}" in body and echoes(head, plain):  # the plain contrast would only repeat the headline
        body = next(b for b in EDGE_BODIES if "{plain}" not in b)
    return {
        "kind": "edge", "gap": gap(k), "metric": k, "team": top, "opp": bot,
        "head": head,
        "body": _fmt((body + (" They’re about as far apart as two teams get." if abs(rt["rank"] - rb["rank"]) >= EDGE_FAR else "") + " " + EDGE_STAKES.get(k, "Expect that gap to show."))
                     if hot else (EDGE_SMALL_BODY if gap(k) < EDGE_SMALL else EDGE_MILD_BODY) + EDGE_WHY.get(k, ""), plain=plain, **w),
        "nums": f"{_cap(name)}: {places[top]} {w['rt']}, {places[bot]} {w['rb']}",
        "chips": [chip(top, k, rt, places, cite), chip(bot, k, rb, places, cite)] if k in CHIP else [],
        "gist": _fmt(EDGE_PLAIN[k] if hot and k in EDGE_PLAIN else "{top} have the edge over {bot} on {name}.", **w),
        "call": _fmt(EDGE_SCORE_CALL[k] if sc else EDGE_CALL[k], **w),
        **({"see": _fmt(EDGE_SEE[k], d=per_game(abs(rt["v"] - rb["v"])), **w)} if sc else {}),
        "cite": cite,
        "check": chk,
    }


def tempo_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    """Calls about the game as a whole: pace (both teams fast or both slow) and physicality (both heavy hitters). The pace
    bar is the faster (or slower) of the two teams' usual, not their average: two fast teams playing a fast game is the
    obvious read, so the call says this one runs faster than either usually does."""
    out = []
    cite = f"Playing style · this season blended with last · {_games(fp[away]['games'], fp[home]['games'])}"
    a, h = fp[away]["dims"]["pace"]["blend"], fp[home]["dims"]["pace"]["blend"]
    w = {**team_words(away, places, "A"), **team_words(home, places, "H")}
    for kind, both, direction in (("fast", a["pct"] >= BOTH and h["pct"] >= BOTH, "above"), ("slow", a["pct"] <= 100 - BOTH and h["pct"] <= 100 - BOTH, "below")):
        if both:
            heads, body = PACE_WORDS[kind]
            league = a["v"] * 100 / a["index"]
            bar = max(a["v"], h["v"]) if kind == "fast" else min(a["v"], h["v"])
            fast = kind == "fast"
            n = per_game(bar)
            out.append({
                "kind": "pace", "gap": min(a["pct"], h["pct"]) if fast else 100 - max(a["pct"], h["pct"]), "metric": "pace", "team": away, "opp": home,
                "head": _pick(heads, gid, "pace"),
                "call": f"The game’s pace score is {'above' if fast else 'below'} {score(bar, league)}, {'faster' if fast else 'slower'} than either team’s usual.",
                "see": (f"{'More' if fast else 'Fewer'} than {n} shots between them at 5-on-5, on goal or wide, which usually means "
                        f"{sog_both(n)} or {'more' if fast else 'fewer'} shots on goal on the scoreboard."),
                "body": _fmt(body, **w),
                "nums": f"Pace score (100 is league average): {places[away]} {_ordinal(a['rank'])} ({a['index']}), {places[home]} {_ordinal(h['rank'])} ({h['index']})",
                "chips": [chip(away, "pace", a, places, cite), chip(home, "pace", h, places, cite)],
                "gist": "Both teams like a fast game." if kind == "fast" else "Both teams play slow, low-event hockey.",
                "cite": cite,
                "check": {"metric": "pace", "measure": "shots per 60 at 5-on-5 (blocked shots left out), both teams combined, adjusted for score and venue",
                          "team": None, "baseline": round(bar, 2), "average": round((a["v"] + h["v"]) / 2, 2), "league": round(league, 2), "direction": direction,
                          "scale": "score", "bar_team": away if bar == a["v"] else home,
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
            "chips": [chip(away, "physical", a, places, cite), chip(home, "physical", h, places, cite)],
            "gist": "Expect plenty of hitting.",
            "cite": cite,
            "check": {"metric": "physical", "measure": "hits per 60 in close games, both teams, as a score against league average (arena-adjusted)",
                      "team": None, "baseline": round((a["index"] + h["index"]) / 2, 1), "league": 100, "direction": "above",
                      "rule": "held if tonight's combined hit score is at or above the two teams' average; partly if above 100; otherwise didn't happen"},
        })
    return out


# Rest edge: one team played last night, the other did not. Over 2023-24 to 2025-26 (809 such team-games), the tired team
# won 41%, took a share of 5-on-5 expected goals 2.0 points below its usual (+/- 0.4), and allowed 2.2 more shot attempts
# per 60. The call: the rested team gets a bigger share of the 5-on-5 chances than it usually does (held 59% of the
# time, against 50% for any team). Both teams on a back-to-back made no difference, and neither did time zones crossed
# or the first home game after a long road trip.
REST_HEADS = ["{T} played last night", "Fresh legs against tired ones", "Second night of a back-to-back for {T}", "{R} come in rested",
              "Tired legs for {T}"]
REST_BODIES = [
    "{T} played last night and get no rest before this one. Teams in that spot usually get the worse of the chances, so we expect {R} to control more of the play than they usually do.",
    "It’s {Tp} second game in two nights. Teams on the back end of those usually spend more time defending, so we think {R} get more of the play than usual.",
]
REST_SAMPLE = 20  # games' weight given to last season when working out a team's usual share of the chances


def rest_days(sched: list[dict], g: dict) -> dict[str, dict]:
    """For each team in `g`: days off before it ("0" = played yesterday) and where it played last, from the schedule."""
    def day_of(x: dict) -> str:
        return x.get("date") or x["start"][:10]
    out = {}
    today = day_of(g)
    for t in (g["away"], g["home"]):
        prev = max((x for x in sched if day_of(x) < today and t in (x["away"], x["home"])), key=day_of, default=None)
        if prev:
            out[t] = {"days": (date.fromisoformat(today) - date.fromisoformat(day_of(prev))).days - 1, "at": prev["home"], "home": prev["home"] == t}
    return out


@lru_cache(maxsize=4)
def _xg_by_game(season: int):
    """Score-and-venue adjusted 5-on-5 expected goals for and against, per team-game, with dates."""
    from pipeline.metrics import adjust, team_style
    try:
        pg = team_style.per_game(season, adjust.weights())
    except FileNotFoundError:
        return None
    return pg.select("team_id", "date", "xgf_adj", "xga_adj")


def usual_share(team: str, before: str, season: int | None = None) -> float | None:
    """A team's usual share of 5-on-5 expected goals before a date: this season, with last season worth REST_SAMPLE games."""
    import polars as pl
    from pipeline.config import CURRENT_SEASON, TABLES
    season = season or CURRENT_SEASON
    f, a = 0.0, 0.0
    for s, weight in ((season, None), (season - 1, REST_SAMPLE)):
        t = _xg_by_game(s)
        p = TABLES / str(s) / "games.parquet"
        if t is None or not p.exists():
            continue
        g = pl.read_parquet(p)
        ids = set(g.filter(pl.col("home") == team)["home_id"].to_list()) | set(g.filter(pl.col("away") == team)["away_id"].to_list())
        t = t.filter(pl.col("team_id").is_in(list(ids)) & (pl.col("date") < before[:10]))
        if t.is_empty():
            continue
        sf, sa = float(t["xgf_adj"].sum()), float(t["xga_adj"].sum())
        if weight:
            sf, sa = sf * weight / t.height, sa * weight / t.height
        f, a = f + sf, a + sa
    return f / (f + a) if f + a > 0 else None


def rest_claim(gid: int, away: str, home: str, places: dict, rest: dict, usual: float | None) -> dict | None:
    """The rest edge, when one team played yesterday and the other has had at least a day off."""
    tired = [t for t in (away, home) if rest.get(t, {}).get("days") == 0]
    if len(tired) != 1 or usual is None:
        return None
    t = tired[0]
    r = home if t == away else away
    if rest.get(r, {}).get("days", 0) < 1:
        return None
    w = {**team_words(t, places, "T"), **team_words(r, places, "R")}
    w["where"] = places.get(rest[t]["at"], rest[t]["at"])
    days = rest[r]["days"]
    return {
        "kind": "rest", "gap": 50, "metric": "rest", "team": r, "opp": t,
        "head": _fmt(_pick(REST_HEADS, gid, "rest"), **w),
        "body": _fmt(_pick(REST_BODIES, gid, "rest", "body"), **w),
        "call": _fmt("{R} get a bigger share of the 5-on-5 chances than they usually do.", **w),
        "nums": f"{places[t]} played yesterday · {places[r]} {'had a day' if days == 1 else f'had {days} days'} off · "
                f"{places[r]}’s usual share of 5-on-5 expected goals: {100 * usual:.1f}%",
        "gist": _fmt("{T} played last night; {R} are rested.", **w),
        "cite": "Schedule · back-to-backs over 2023-24 to 2025-26 (809 games)",
        "chips": [
            {"team": t, "text": "played yesterday", "tip": f"{places[t]} played yesterday. Schedule, back-to-backs over 2023-24 to 2025-26 (809 games)."},
            {"team": r, "text": "a day off" if days == 1 else f"{days} days off", "tip": f"{places[r]} {'had a day' if days == 1 else f'had {days} days'} off before this game."},
            {"team": r, "text": f"usually {100 * usual:.0f}% of the chances",
             "tip": f"{places[r]}’s usual share of 5-on-5 expected goals: {100 * usual:.1f}%, adjusted for score and venue."},
        ],
        "check": {"metric": "rest", "measure": "share of 5-on-5 expected goals, adjusted for score and venue", "team": r,
                  "baseline": round(usual, 4), "league": 0.5, "direction": "above",
                  "rule": "held if the rested team's share of 5-on-5 expected goals is at or above its usual share; otherwise didn't happen"},
    }


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
    mc = matchup_claim(gid, away, home, lines, places, extra.get("pts"), extra.get("bench") or (extra.get("coaches") or {}).get(home))
    if mc:
        cands.append(mc)
    cands += history_claims(gid, away, home, extra.get("goalies") or {}, extra.get("hist"), places, extra.get("likely"))
    rc = rest_claim(gid, away, home, places, extra.get("rest") or {}, extra.get("rest_usual"))
    if rc:
        cands.append(rc)
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
    two = lambda d: {**{k: v for k, v in d.items() if k not in ("teams", "idle")}, "teams": {t: d["teams"][t] for t in (away, home) if t in d["teams"]}}
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


SHOW_LIKELY = 0.6  # our own guess is shown as "likely" only from here (right about three times in four)


def projected_starters(g: dict, likely: dict, rebuilt: bool = False) -> dict[str, dict]:
    """The goalie each team is expected to start, for the page: NHL.com's projection when its preview is up ("nhl"),
    else our own guess when it is fairly sure ("model"). Never the chance itself."""
    from pipeline.config import CURRENT_SEASON
    from pipeline.ingest import nhl_previews
    nhl = nhl_previews.projected(CURRENT_SEASON, g["id"], _iso(g["start"]) if rebuilt else None)
    out = {}
    for t in (g["away"], g["home"]):
        ranked = likely.get(t) or []
        if not ranked:
            continue
        pid, q = ranked[0]
        if nhl.get(t) == pid:
            out[t] = {"id": pid, "from": "nhl"}
        elif q >= SHOW_LIKELY:
            out[t] = {"id": pid, "from": "model"}
    return out


def nhl_lineup(g: dict, rebuilt: bool = False) -> dict | None:
    """NHL.com's projected lines and pairs for the page, named, with each club's shape (11 forwards and 7 defensemen
    flagged), plus the status facts its rules found (`nhl_previews.status_facts`). Nothing else from the article."""
    from pipeline.config import CURRENT_SEASON
    from pipeline.ingest import nhl_previews as NP
    from pipeline.export import wording
    got = NP.projection(CURRENT_SEASON, g["id"], _iso(g["start"]) if rebuilt else None)
    if not got:
        return None
    teams = (g["away"], g["home"])
    people = NP.people_for(CURRENT_SEASON, teams)
    clash = wording.number_clashes(CURRENT_SEASON, wording.initials)
    name = lambda p: clash.get(p) or (f"{people[p][1][:1]}. {people[p][2]}" if p in people else None)
    named = lambda ids: [{"id": p, "name": name(p)} for p in ids if name(p)]
    out = {"fetched": got.get("fetched"), "published": got.get("published"), "teams": {}}
    for t in teams:
        lu = (got.get("lineups") or {}).get(t)
        if not lu or not lu.get("lines"):
            continue
        f, d = NP.shape(lu)
        out["teams"][t] = {"lines": [named(x) for x in lu["lines"]], "pairs": [named(x) for x in lu["pairs"]], "extra_d": named(lu.get("extra_d") or []),
                           "f": f, "d": d, "eleven": f == 11 and d == 7}
    teams_json = {x["abbr"]: x for x in json.loads((SITE_DATA / "teams.json").read_text())} if (SITE_DATA / "teams.json").exists() else {}
    aliases = {t: [x for x in (teams_json.get(t, {}).get(k) for k in ("place", "nick", "name")) if x] for t in teams}
    facts = NP.status_facts(got.get("status"), aliases, people)
    for x in facts:  # "The Lighting are expected to dress 11...": no club named, so the one projected with 11
        if x["kind"] == "eleven" and x["team"] is None:
            x["team"] = next((t for t, v in out["teams"].items() if v["eleven"]), None)
    out["facts"] = [x for x in facts if x["team"]]
    return out if out["teams"] or out["facts"] else None


def snapshot(g: dict, site: dict, places: dict, sched: list[dict], now: datetime, chance: dict | None = None,
             likely: dict | None = None, **extra) -> dict:
    """`chance` is the win model's morning figure for the game; only the words it picks are saved, never the number.
    `likely` is each team's likely starting goalies (starters.upcoming); only their order is saved."""
    away, home = g["away"], g["home"]
    fp = site["fingerprints"]["teams"]
    if likely is None:
        likely = _likely([g], g["date"], nhl_before=_iso(g["start"])).get(g["id"]) or {}
    rest = rest_days(sched, g)
    rested = [t for t in (away, home) if rest.get(t, {}).get("days", 0) >= 1 and any(rest.get(o, {}).get("days") == 0 for o in (away, home) if o != t)]
    # date-based cuts take the game's local date: a late start falls on the next UTC day, and the start's date would
    # let a rebuilt preview see the game itself
    day = g.get("date") or g["start"][:10]
    rest_usual = usual_share(rested[0], day) if rested else None
    from pipeline.config import CURRENT_SEASON
    from pipeline.ingest import coaches
    bench = coaches.for_game(CURRENT_SEASON, g["id"], home, day)
    claims = calls(g["id"], away, home, fp, site["lines"]["teams"], places,
                   {**call_extras(site, day), "likely": likely, "rest": rest, "rest_usual": rest_usual, "bench": bench})
    from pipeline.export import surprises, wording
    for c in claims:
        rep = special_teams_record(c, wording.places(places), surprises)
        if rep:
            c["body"] = f"{c['body']} {rep}"
        pen = penalty_context(c, site["fingerprints"]["teams"], places)
        if pen:
            c["body"] = f"{c['body']} {pen}"
    for c in claims:  # "the Islanders play", not "plays": templates are written for place names
        for k in ("head", "body", "call", "see", "gist"):
            if isinstance(c.get(k), str):
                c[k] = wording.tidy(c[k])
    out = {"game_id": g["id"], "start": g["start"], "away": away, "home": home, "venue": g["venue"],
           "snapshot_at": now.isoformat(timespec="seconds"), "rules": RULES_VERSION, "data_as_of": site["fingerprints"]["generated_at"], **extra,
           "claims": claims, "pregame": bundle(away, home, site, records(sched, g["start"])),
           "starters": {t: [pid for pid, _ in likely.get(t) or []] for t in (away, home)},
           "projected": projected_starters(g, likely, bool(extra.get("rebuilt")))}
    try:
        nhl = nhl_lineup(g, bool(extra.get("rebuilt")))
    except Exception as e:  # the preview stands without it
        print(f"NHL.com lineup for {g['id']} unreadable: {e!r}")
        nhl = None
    if nhl:
        out["nhl_lineup"] = nhl
    if chance:
        from pipeline.export import outlook
        out["outlook"] = outlook.notes(g["id"], away, home, chance, fp, places)
    try:  # the plain-English story at the top of the page; the preview stands without it
        from pipeline.export import stories
        out["story"] = stories.preview_story(g["id"], away, home, g.get("date") or g["start"][:10], places, claims, out.get("outlook"), site["lines"]["teams"], likely=likely,
                                             nhl=nhl, fp=fp)
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


def _iso(start: str) -> str:
    """A schedule start ("2026-10-10T23:00:00Z") in the form NHL.com finds are stamped with ("...+00:00")."""
    return start.replace("Z", "+00:00")


def _likely(games: list[dict], before: str | None = None, nhl_before: str | None = None) -> dict[int, dict]:
    """Likely starting goalies for these games (pipeline/metrics/starters.py). Previews still work without them.
    A rebuilt preview may use an NHL.com projection found before the game's puck drop (`nhl_before`), as a live
    preview that morning would have (eye test, 2026-10-13: it had Woll, Varlamov, Fowler, Ersson, Murashov and Talbot
    right where our own guess missed)."""
    try:
        from pipeline.metrics import starters
        nhl = starters.nhl_picks(games, before=nhl_before) if nhl_before else None
        return starters.upcoming(games, before=before, nhl=nhl)
    except Exception as e:
        print(f"likely starters unavailable: {e!r}")
        return {}


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


@lru_cache(maxsize=1)
def _namesakes() -> frozenset[int]:
    from pipeline.config import CURRENT_SEASON
    from pipeline.ingest import nhl_previews as NP
    return frozenset(i for t, by in NP.known_skaters(CURRENT_SEASON).items() if t.startswith("=") for c in by.values() for i, _ in c)


def fix_namesakes(path, g: dict) -> bool:
    """A started game's snapshot keeps everything as it was at puck drop except NHL.com's projected lines, which are
    read again when they name one of two same-named teammates (`nhl_previews.repair`): a misread there put Vancouver's
    forward Elias Pettersson on a defense pair, which is NHL.com's lineup shown wrong, not something we said."""
    if not path.exists() or not _namesakes():
        return False
    snap = json.loads(path.read_text())
    old = (snap.get("nhl_lineup") or {}).get("teams") or {}
    if not any(p["id"] in _namesakes() for lu in old.values() for k in ("lines", "pairs", "extra_d") for x in lu.get(k) or []
               for p in (x if isinstance(x, list) else [x])):
        return False
    new = (nhl_lineup({**g, "id": snap["game_id"]}, bool(snap.get("rebuilt"))) or {}).get("teams") or {}
    if not new or new == old:
        return False
    snap["nhl_lineup"]["teams"] = new
    path.write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
    return True


def run(now: datetime | None = None, within: timedelta | None = None) -> dict:
    """Write a snapshot for every game in the next DAYS_AHEAD days that hasn't started, or only those starting within
    `within` (the quick game-night pass, which refreshes tonight's lineups and starters)."""
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
            fix_namesakes(out_dir / f"{g['id']}.json", g)
            continue  # started or finished: left as it was at puck drop (pregame.py rebuilds finished ones on a rules change)
        if g["final"] or start > now + (within or timedelta(days=DAYS_AHEAD)):
            continue
        if g["away"] in site["fingerprints"]["teams"] and g["home"] in site["fingerprints"]["teams"]:
            todo.append(g)
    chances, likely = _chances(todo), _likely(todo)
    for g in todo:
        snap = snapshot(g, site, places, sched, now, chances.get(g["id"]), likely.get(g["id"]) or {}, methods=METHODS)
        (out_dir / f"{g['id']}.json").write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
        written += 1
    return {"written": written, "frozen": frozen, "indexed": write_index(out_dir)}
