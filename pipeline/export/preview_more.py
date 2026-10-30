"""The fuller preview (Sora, 2026-10-30, thread on improving the site's voice): after the opening paragraph (meetings
and form), three more paragraphs built from the same kinds of facts as the hand-written drafts in `voice/runs/`:
how each team plays (its most distinctive habits, and its scoring at the ends of the league), who carries the
scoring, and who is expected in net. Everything comes from games before the game's own date, so a rebuilt preview
says what a live one would have."""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON
from pipeline.export.grammar import and_list, article, cap, num, ordinal_word

# Each habit as something a team does, at the high end of its scale and at the low end (plural verbs: teams are
# "they"). Quality is the average shot's chance of going in, never a place on the ice (wording notes, 2026-10-10).
HABITS = {
    "volume": ("throw a lot of pucks at the net", "pick their shots"),
    "quality": ("take a lot of high-quality shots", "take a lot of low-quality shots"),
    "rebounds": ("get plenty of second chances", "rarely get second chances"),
    "turnover": ("pounce on turnovers", "rarely create chances off turnovers"),
    "dShots": ("get their defensemen shooting", "leave the shooting to their forwards"),
    "inClose": ("get their shots from in close", "do a lot of their shooting from distance"),
    "shotsThrough": ("get their shots through to the net", "get a lot of their shots blocked"),
    "suppression": ("keep shots away from their net", "give up a lot of shots"),
    "qualityAllowed": ("keep dangerous shots away", "give up a lot of dangerous shots"),
    "breakdowns": ("rarely break down in front of their goalie", "break down in front of their goalie a lot"),
    "longShifts": ("rarely get pinned in their own end", "get pinned in their own end on long shifts"),
    "blocking": ("block a lot of shots", "don't block many shots"),
    "pointAllowed": ("let the point shots come and guard the net", "take away shots from the point"),
    "icing": ("rarely ice the puck", "ice the puck a lot"),
    "goalie": ("have had some of the best goaltending in the league", "have had shaky goaltending"),
    "pace": ("play fast, open games", "play slow, tight games"),
    "forecheck": ("hound the puck on the forecheck", "sit back rather than chase on the forecheck"),
    "physical": ("hit a lot", "rarely throw hits"),
    "rough": ("get into a lot of chippy games", "keep things calm after the whistle"),
    "pp": ("have a dangerous power play", "have struggled on the power play"),
    "pk": ("kill penalties well", "have a leaky penalty kill"),
    "powerKill": ("create chances on the penalty kill", "rarely threaten on the penalty kill"),
    "depth": ("spread the ice time through the lineup", "lean hard on their top players"),
    "discipline": ("draw more penalties than they take", "take more penalties than they draw"),  # "power play" once a list
}
LABELS = {"volume": "shot volume", "quality": "shot quality", "rebounds": "second chances", "turnover": "offense off turnovers",
          "dShots": "shots from defensemen", "inClose": "shots from in close", "shotsThrough": "getting shots through",
          "suppression": "shot suppression", "qualityAllowed": "keeping dangerous shots away", "breakdowns": "avoiding breakdowns",
          "longShifts": "avoiding long shifts", "blocking": "blocking shots", "pointAllowed": "point shots allowed",
          "icing": "avoiding icings", "goalie": "goaltending", "pace": "pace", "forecheck": "forecheck pressure",
          "physical": "physicality", "rough": "rough stuff", "pp": "the power play", "pk": "the penalty kill",
          "powerKill": "shorthanded threat", "discipline": "discipline", "depth": "depth"}
NEUTRAL = {"pointAllowed", "dShots", "inClose", "pace", "volume", "rough", "physical", "forecheck", "turnover", "depth"}  # no "worst" wording
BOUNCY = {"rebounds", "turnover", "breakdowns"}  # habits that take a hundred games or more to settle: mostly bounces
STYLE_PCT = 80  # a habit is told at the 80th percentile or the 20th and beyond
STYLE_MAX = 3
RANK_TOLD = 3  # a rank is told within this many places of either end
SCORING_EDGE = 4  # goals a game for or against told within this many places of either end
MIN_GAMES = 5  # before this, standings and scoring ranks say little
HOT_POINTS = 7  # points in his team's last five games to be "hot"


def _picks(t: str, fp: dict) -> list[tuple]:
    """Their most distinctive habits, furthest from average first: (distance, trait, blend figures)."""
    dims = (fp.get(t) or {}).get("dims") or {}
    eleven = ((fp.get(t) or {}).get("lineup") or {}).get("habit")  # a club that dresses eleven forwards has no fourth line to weigh
    picked = []
    for k, d in dims.items():
        if k == "depth" and eleven:
            continue
        b = (d or {}).get("blend") or {}
        if k in HABITS and k not in BOUNCY and b.get("pct") is not None and b.get("rank") and (b["pct"] >= STYLE_PCT or b["pct"] <= 100 - STYLE_PCT):
            picked.append((abs(b["pct"] - 50), k, b))
    return sorted(picked, key=lambda x: (-x[0], x[1]))[:STYLE_MAX]


# The lead habit said as who they are, at the top three of a scale: (rank 1, ranks 2-3).
IDENTITY = {
    "physical": ("are the league’s most physical team", "are one of the league’s most physical teams"),
    "forecheck": ("forecheck harder than anyone", "forecheck as hard as almost anyone"),
    "rough": ("get into more chippy games than anyone", "get into more chippy games than almost anyone"),
    "pp": ("have the league’s most dangerous power play", "have one of the league’s most dangerous power plays"),
    "pk": ("have the league’s best penalty kill", "have one of the league’s best penalty kills"),
    "suppression": ("keep shots away better than anyone", "keep shots away better than almost anyone"),
    "qualityAllowed": ("keep dangerous shots away better than anyone", "keep dangerous shots away better than almost anyone"),
    "volume": ("throw more pucks at the net than anyone", "throw more pucks at the net than almost anyone"),
    "quality": ("get the league’s best looks", "get some of the league’s best looks"),
    "pace": ("play the league’s fastest games", "play some of the league’s fastest games"),
    "blocking": ("block more shots than anyone", "block more shots than almost anyone"),
    "icing": ("ice the puck less than anyone", "ice the puck less than almost anyone"),
    "shotsThrough": ("get their shots through better than anyone", "get their shots through better than almost anyone"),
    "goalie": ("have had the league’s best goaltending", "have had some of the league’s best goaltending"),
    "powerKill": ("create more chances on the penalty kill than anyone", "create more chances on the penalty kill than almost anyone"),
    "inClose": ("get more of their shots from in close than anyone", "get more of their shots from in close than almost anyone"),
    "depth": ("roll their lines more evenly than anyone", "roll their lines more evenly than almost anyone"),
}
# The low end of a neutral scale said as who they are (Sora, 2026-10-30, liked "Edmonton leans on its stars more than
# any team in the league: no team gives its bottom six less of the ice time"): (rank 32, ranks 30-31), and the fact.
BOTTOM = {"depth": (("lean on their stars more than any team in the league", "lean on their stars as much as almost anyone"),
                    "gives its bottom six less of the ice time", "give their bottom six less of the ice time")}
# A weakness near the bottom, said as how few teams are worse: (plural verb for "only N teams", singular for "no team").
FEW_WORSE = {
    "quality": ("average a lower chance of scoring per shot", "averages a lower chance of scoring per shot"),
    "suppression": ("give up more shots", "gives up more shots"),
    "qualityAllowed": ("give up more dangerous shots", "gives up more dangerous shots"),
    "pp": ("get less from their power play", "gets less from its power play"),
    "pk": ("kill penalties worse", "kills penalties worse"),
    "longShifts": ("get stuck in their own end more", "gets stuck in its own end more"),
    "shotsThrough": ("get more of their shots blocked", "gets more of its shots blocked"),
    "icing": ("ice the puck more", "ices the puck more"),
}
# Three top habits from one family read as one identity: "The Panthers are the league’s most physical team: first in ..."
FAMILIES = [({"physical", "forecheck", "rough"}, "are the league’s most physical team"),
            ({"suppression", "qualityAllowed", "blocking", "longShifts", "pk", "goalie", "icing"}, "are as hard to score on as anyone"),
            ({"volume", "quality", "shotsThrough", "inClose", "pp"}, "are as dangerous going forward as anyone")]


def _is_good(k, b):
    return k not in NEUTRAL and b["pct"] >= 50


def _is_bad(k, b):
    return k not in NEUTRAL and b["pct"] < 50


def style(t: str, picked: list[tuple], W, stats: dict | None = None, second: bool = False, key=()) -> str | None:
    """Their most distinctive habits in a sentence or two: an identity when they top a scale, the recorded numbers
    behind a habit when they agree with it, and how few teams are worse at a weakness near the bottom."""
    from pipeline.export.stories import _pick
    if not picked:
        return None
    st = stats or {}
    ranks = [b["rank"] for _, k, b in picked]
    X = cap(W.the(t))
    if second:  # the second club is introduced apart from the first ("As for the Jets, they ...")
        X = _pick([X, f"As for {W.the(t)}, they", f"On the other side, {W.the(t)}"], *key, t, "style2")
    keys = {k for _, k, _ in picked}
    fam = next((ident for ks, ident in FAMILIES if keys <= ks), None)
    if fam and len(picked) == 3 and all(r <= RANK_TOLD for r in ranks) and all(b["pct"] >= 50 for _, _, b in picked):
        return f"{X} {fam}: " + and_list([f"{ordinal_word(b['rank'])} in {LABELS[k]}" for _, k, b in picked])
    ups, downs = [], []
    ident = next((k for _, k, b in picked if b["pct"] >= 50 and b["rank"] <= RANK_TOLD and k in IDENTITY), None)  # one a team
    for i, (_, k, b) in enumerate(picked):
        hi = b["pct"] >= 50
        if k == ident:
            words = IDENTITY[k][0 if b["rank"] == 1 else 1]
        elif k in BOTTOM and not hi and b["rank"] >= 30:
            words = BOTTOM[k][0][0 if b["rank"] == 32 else 1]
        else:
            words = HABITS[k][0 if hi else 1]
        (downs if _is_bad(k, b) else ups).append((k, b, words, st.get(k, {}).get("hi" if hi else "lo")))
    figured = {k for k, b, w, f in ups + downs if f}

    # ranks only at the top end of a scale ("32nd in rough stuff" reads as a knock on a calm team), and each rides on its
    # own habit (Sora, 2026-10-30: a rank in a later sentence read as a repeat, cut off from what it was about)
    told = {k for k, b, w, _ in ups
            if b["rank"] <= RANK_TOLD and b["pct"] >= 50 and k not in figured and k != "depth" and not ("anyone" in w or "league’s" in w)}
    ups = [(k, b, w + (f" ({ordinal_word(b['rank'])} in the league)" if k in told else ""), f) for k, b, w, f in ups]

    def told_list(group):  # the figure behind a habit: a trailing clause on the last one, in brackets mid-list
        return and_list([w + ("" if not f else f", {f[0]} ({f[1]})" if i == len(group) - 1 else f" ({f[0]}, {f[1]})")
                         for i, (k, b, w, f) in enumerate(group)])
    mixed = bool(ups and downs) and any(_is_good(k, b) for k, b, _, _ in ups)
    order = ups + downs
    out = X + " " + (f"{told_list(ups)}, but {told_list(downs)}" if mixed else told_list(order))
    worst = next(((k, b) for k, b, _, _ in downs if k in FEW_WORSE and k not in figured and b["rank"] >= 30), None)
    if worst:  # "Only three teams average a lower chance of scoring per shot.", after its habit when that ends the list
        k, b = worst
        n = 32 - b["rank"]
        few = f"no team {FEW_WORSE[k][1]}" if n == 0 else f"only {num(n)} {'team' if n == 1 else 'teams'} {FEW_WORSE[k][1 if n == 1 else 0]}"
        last = (downs if mixed else order)[-1][0]
        out += f": {few}" if last == k else f". {cap(few)}"
    low = next(((k, b) for k, b, _, _ in ups + downs if k in BOTTOM and b["pct"] < 50 and b["rank"] >= 30), None)
    if low:  # "No team gives its bottom six less of the ice time."
        k, b = low
        n = 32 - b["rank"]
        out += f". No team {BOTTOM[k][1]}" if n == 0 else f". Only {num(n)} {'team' if n == 1 else 'teams'} {BOTTOM[k][1 if n == 1 else 2]}"
    return out


@lru_cache(maxsize=64)
def _scoring(season: int, day: str) -> dict[str, dict]:
    """Each club's goals for and against a game this season, in games before `day` (shootout deciders left out)."""
    from pipeline.export.stories import book
    G, _, _ = book()
    g = G.filter((pl.col("season") == season) & (pl.col("date") < day))
    so = pl.col("last_period") == "SO"  # the shootout winner gets a goal in the score; it was no goal
    g = g.with_columns(hs=pl.when(so & (pl.col("home_score") > pl.col("away_score"))).then(pl.col("home_score") - 1).otherwise(pl.col("home_score")),
                       as_=pl.when(so & (pl.col("away_score") > pl.col("home_score"))).then(pl.col("away_score") - 1).otherwise(pl.col("away_score")))
    rows = pl.concat([g.select(team=pl.col("home"), gf=pl.col("hs"), ga=pl.col("as_")), g.select(team=pl.col("away"), gf=pl.col("as_"), ga=pl.col("hs"))])
    agg = rows.group_by("team").agg(pl.len().alias("gp"), pl.col("gf").sum(), pl.col("ga").sum())
    out = {r["team"]: {"gp": r["gp"], "gfpg": r["gf"] / r["gp"], "gapg": r["ga"] / r["gp"]} for r in agg.iter_rows(named=True)}
    for k, best_high in (("gfpg", True), ("gapg", False)):
        vals = sorted((v[k] for v in out.values()), reverse=best_high)
        for v in out.values():
            v[f"{k}_rank"] = vals.index(v[k]) + 1
    return out


def scoring(t: str, season: int, day: str, W) -> str | None:
    """Goals for or against a game, when near either end of the league."""
    s = _scoring(season, day).get(t)
    if not s or s["gp"] < MIN_GAMES:
        return None
    n = len(_scoring(season, day))
    edge = lambda k: s[f"{k}_rank"] <= SCORING_EDGE or s[f"{k}_rank"] > n - SCORING_EDGE

    def rk(k):  # "the most", "third fewest": goals allowed rank 1 is the fewest
        r = s[f"{k}_rank"] if k == "gfpg" else n + 1 - s[f"{k}_rank"]
        word, q = ("most", r) if r <= n / 2 else ("fewest", n + 1 - r)
        return f"the {word}" if q == 1 else f"the {ordinal_word(q)} {word}"
    if edge("gfpg") and edge("gapg"):
        return f"{W.place(t)} has scored {s['gfpg']:.2f} goals a game, {rk('gfpg')} in the league, and allowed {s['gapg']:.2f}, {rk('gapg')}."
    if edge("gfpg"):
        return f"{W.place(t)} has scored {s['gfpg']:.2f} goals a game, {rk('gfpg')} in the league."
    if edge("gapg"):
        return f"{W.place(t)} has allowed {s['gapg']:.2f} goals a game, {rk('gapg')} in the league."
    return None


@lru_cache(maxsize=64)
def _team_stats(season: int, day: str) -> dict[str, dict]:
    """Each club's recorded shots for and against a game and power-play and penalty-kill rates this season, in games
    before `day`, with league ranks (1 = most shots, fewest against, best rates)."""
    from pipeline.config import TABLES
    from pipeline.export.stories import book
    G, _, _ = book()
    g = G.filter((pl.col("season") == season) & (pl.col("date") < day))
    if g.is_empty():
        return {}
    ids = set(g["game_id"].to_list())
    tg = pl.read_parquet(TABLES / str(season) / "team_game.parquet", columns=["game_id", "team_id", "sf", "sa"]).filter(pl.col("game_id").is_in(ids))
    abbr = {r["home_id"]: r["home"] for r in g.iter_rows(named=True)} | {r["away_id"]: r["away"] for r in g.iter_rows(named=True)}
    shots = tg.group_by("game_id", "team_id").agg(pl.col("sf").sum(), pl.col("sa").sum()).group_by("team_id").agg(
        pl.len().alias("gp"), pl.col("sf").mean(), pl.col("sa").mean())
    out = {abbr[r["team_id"]]: {"gp": r["gp"], "sf": r["sf"], "sa": r["sa"]} for r in shots.iter_rows(named=True) if r["team_id"] in abbr}
    gs = TABLES / str(season) / "game_stats.parquet"
    if gs.exists():
        x = pl.read_parquet(gs).filter(pl.col("game_id").is_in(ids))
        opp = x.join(x.select("game_id", pl.col("team").alias("opp"), pl.col("pp_goals").alias("ppga"), pl.col("pp_opps").alias("ppoa")), on="game_id").filter(pl.col("team") != pl.col("opp"))
        for r in opp.group_by("team").agg(pl.col("pp_goals").sum(), pl.col("pp_opps").sum(), pl.col("ppga").sum(), pl.col("ppoa").sum()).iter_rows(named=True):
            if r["team"] in out and r["pp_opps"] and r["ppoa"]:
                out[r["team"]]["pp"] = 100 * r["pp_goals"] / r["pp_opps"]
                out[r["team"]]["pk"] = 100 - 100 * r["ppga"] / r["ppoa"]
    for k, high in (("sf", True), ("sa", False), ("pp", True), ("pk", True)):
        vals = sorted((v[k] for v in out.values() if k in v), reverse=high)
        for v in out.values():
            if k in v:
                v[f"{k}_rank"] = vals.index(v[k]) + 1
    return out


STAT_EDGE = 8  # a recorded figure rides on a habit only when it is this close to the same end of the league


def stat_words(t: str, season: int, day: str) -> dict[str, dict]:
    """The recorded figures behind a habit, as a clause to hang on it when the two agree: suppression with shots
    against a game, volume with shots a game, the power play and the penalty kill with their rates."""
    all_ = _team_stats(season, day)
    s = all_.get(t)
    if not s or s["gp"] < MIN_GAMES:
        return {}
    n = len(all_)

    def where(r, most, fewest):  # rank 1 = the good end
        if r <= n / 2:
            return f"the {most}" if r == 1 else f"{ordinal_word(r)} {most}"
        q = n + 1 - r
        return f"the {fewest}" if q == 1 else f"{ordinal_word(q)} {fewest}"
    out = {}
    if s["sa_rank"] <= STAT_EDGE:
        out.setdefault("suppression", {})["hi"] = (f"allowing {s['sa']:.1f} shots a game", where(s['sa_rank'], 'fewest', 'most'))
    if s["sa_rank"] > n - STAT_EDGE:
        out.setdefault("suppression", {})["lo"] = (f"{s['sa']:.1f} a game", where(s['sa_rank'], 'fewest', 'most') + " in the league")
    if s["sf_rank"] <= STAT_EDGE:
        out.setdefault("volume", {})["hi"] = (f"{s['sf']:.1f} shots a game", where(s['sf_rank'], 'most', 'fewest'))
    for k, verb in (("pp", "scoring on {v:.1f}% of their chances"), ("pk", "killing {v:.1f}% of penalties")):
        if k in s and (s[f"{k}_rank"] <= STAT_EDGE or s[f"{k}_rank"] > n - STAT_EDGE):
            side = "hi" if s[f"{k}_rank"] <= STAT_EDGE else "lo"
            out.setdefault(k, {})[side] = (verb.format(v=s[k]), where(s[f'{k}_rank'], 'best', 'worst'))
    return out


# What one team's habit meets in the other's: (attacking trait, defending trait, the attack strong and the defense
# weak, both strong, the attack weak and the defense strong). {A}/{Ap} the attacking team, {B}/{Bp} the defending one.
MEETS = [
    ("pp", "pk", "{Ap} power play could do real damage when {B} are shorthanded",
     "{Ap} power play against {Bp} penalty killers is strength against strength", "{Ap} struggling power play gets a tough test from {Bp} penalty killers"),
    ("volume", "suppression", "{A} are likely to get plenty of pucks to the net against {B}",
     "something has to give when {Ap} shooting runs into {Bp} defense", "{A} may have a hard time getting pucks to the net against {B}"),
    ("quality", "qualityAllowed", "{A} could find some good looks against {B}, who give up plenty of them",
     "{Ap} good looks will be harder to come by against {B}", "good looks are likely to be hard to come by for {A}"),
    ("forecheck", "longShifts", "{Ap} forecheck could keep {B} pinned in their own end", None, None),
    ("shotsThrough", "blocking", None, "{Ap} knack for getting shots through meets a team that blocks a lot",
     "{B} are likely to get in the way of a lot of {Ap} shots"),
]
MEET_HI, MEET_LO = 70, 30


def meet_line(kind: str, metric: str, team: str, opp: str, W, gist: str = "") -> str | None:
    """A clash or duel call told as what the two habits mean together, in the same words as our own pairings."""
    if kind == "physical":
        return "Both teams like to hit, so expect plenty of it."
    if kind == "pace":
        return ("Both teams like to play fast, so expect an open game." if "slow" not in gist else
                "Both teams play slow, low-event hockey, so expect a tight one.")
    for ka, _kb, weak_d, both, _ in MEETS:
        text = (both if kind == "duel" else weak_d if kind == "clash" else None) if ka == metric else None
        if text:
            return cap(text.format(A=W.the(team), Ap=W.poss(team), B=W.the(opp), Bp=W.poss(opp))) + "."
    return None


def partners(traits: set) -> set:
    """The traits a pairing is about: a call on the power play is about the other side's penalty kill too."""
    out = set(traits)
    for ka, kb, *_ in MEETS:
        if ka in traits or kb in traits:
            out |= {ka, kb}
    return out


DEFEND = {"pk", "suppression", "qualityAllowed", "longShifts", "blocking", "powerKill"}  # the side a pairing's attack meets
SH_THREAT = 80  # a penalty kill this dangerous going the other way is worth naming beside the power play it faces
FACE_OFF = ("strength against strength", "has to give", "meets a team")  # two strengths meeting: a face-off, not a result


def consequences(away: str, home: str, fp: dict, W, covered: set) -> list[tuple[float, str, set]]:
    """What the two styles mean when they meet, strongest first: (strength, sentence, traits it is about). Traits a
    call already covers are left to the call's own line."""
    pct = lambda t, k: ((((fp.get(t) or {}).get("dims") or {}).get(k) or {}).get("blend") or {}).get("pct")
    out = []
    for a, b in ((away, home), (home, away)):
        w = {"A": W.the(a), "Ap": W.poss(a), "B": W.the(b), "Bp": W.poss(b)}
        for ka, kb, weak_d, both, weak_a in MEETS:
            pa, pb = pct(a, ka), pct(b, kb)
            if pa is None or pb is None or {ka, kb} & covered:
                continue
            text = (weak_d if pa >= MEET_HI and pb <= MEET_LO else both if pa >= MEET_HI and pb >= MEET_HI
                    else weak_a if pa <= MEET_LO and pb >= MEET_HI else None)
            if text:
                out.append((abs(pa - 50) + abs(pb - 50), text.format(**w), {ka, kb}))
    both = lambda k, test: all(pct(t, k) is not None and test(pct(t, k)) for t in (away, home))
    if "pace" not in covered and both("pace", lambda p: p >= MEET_HI):
        out.append((60, "both teams like to play fast, so expect an open game", {"pace"}))
    elif "pace" not in covered and both("pace", lambda p: p <= MEET_LO):
        out.append((60, "both teams play slow, low-event hockey, so expect a tight one", {"pace"}))
    heavy = [t for t in (away, home) if (pct(t, "physical") or 0) >= 85 and (pct(t, "rough") or 0) >= 70]
    if "physical" not in covered and (heavy or both("physical", lambda p: p >= MEET_HI)):
        out.append((70 if heavy else 50, "expect a chippy, heavy game" if heavy else "both teams like to hit, so expect plenty of it", {"physical"}))
    return sorted(out, key=lambda x: -x[0])


def _lower(s: str) -> str:
    """Lower the first word when it is not a name ("The Panthers’ power play ..." -> "the Panthers’ power play ...")."""
    first = s.split(" ", 1)[0]
    return s[0].lower() + s[1:] if first in ("The", "It’s", "Something", "Expect", "Both", "Good", "Neither") else s


TRAIT_CALLS = ("clash", "duel", "edge", "changed", "pace", "physical")  # calls about the two styles: their gist is a consequence


def _them(ln: str, t: str, W) -> str:
    """A line told right after a club's own habits names it as "they" ("so their forecheck could keep the Jets
    pinned", "against them", "when they are shorthanded")."""
    the, poss = W.the(t), W.poss(t)
    ln = ln.replace(f" {the} are ", " they’re ").replace(f" {the} aren’t ", " they aren’t ")
    ln = ln.replace(poss, "their").replace(f" {the}", " them")
    return ln


def how_they_play(away: str, home: str, fp: dict, season: int, day: str, W, gists: list = (), covered: set = frozenset(),
                  key=()) -> str | None:
    """Each club's habits, then what they mean when they meet: the trait calls' own lines first, then our pairings.
    A meeting line about a habit a club's own list tells goes with that habit, right after the list (Sora, 2026-10-30:
    the team against the league and the team against tonight's opponent are told in tandem when they agree); the rest
    close the paragraph, with "That means" only when the habits behind them were told."""
    from pipeline.export.stories import _pick
    sents = []
    picks = {t: _picks(t, fp) for t in (away, home)}
    side = lambda b: b["pct"] >= 50
    shared = [(k, b) for _, k, b in picks[away] if any(k == k2 and side(b) == side(b2) for _, k2, b2 in picks[home])]
    if shared:  # "Both teams get their defensemen shooting." once, not in each team's list
        sents.append(f"Both teams {and_list([HABITS[k][0 if side(b) else 1] for k, b in shared])}.")
        gone = {k for k, _ in shared}
        picks = {t: [x for x in v if x[1] not in gone] for t, v in picks.items()}
    lines = [(g, set()) if isinstance(g, str) else (g[0], set(g[1])) for g in gists if g and (g if isinstance(g, str) else g[0])]
    for _, text, ks in consequences(away, home, fp, W, set(covered)):
        if len(lines) >= 2:
            break
        lines.append((cap(text) + ".", ks))
    lines = lines[:2]
    # a power play that meets a shorthanded threat (Sora, 2026-10-30: the Jets create chances on the kill while the
    # Panthers' power play could do real damage, a strength against strength of its own): told together
    pct = lambda t, k: ((((fp.get(t) or {}).get("dims") or {}).get(k) or {}).get("blend") or {}).get("pct")
    as_pair = {}
    for i, (ln, ks) in enumerate(lines):
        if not ks & {"pp", "pk"}:
            continue
        for a, b in ((away, home), (home, away)):
            rest = ln.split(" power play ", 1)[-1]
            if (f"{W.poss(a)} power play".lower() in ln.lower() and rest.startswith("could")
                    and (pct(b, "powerKill") or 0) >= SH_THREAT):  # a weak kill that is dangerous the other way
                lines[i] = (ln[:-1] + f", though {W.poss(b)} penalty killers create chances of their own going the other way.",
                            ks | {"powerKill"})
                # told with the shorthanded threat: "that sets up a fight with the Panthers’ power play, which could ..."
                as_pair[lines[i][0]] = f"that sets up a fight with {W.poss(a)} power play, which {rest}"
                break
    # a habit both share, told as "Both teams ...", that one club has to a higher degree: said once, with that club named
    # (Sora, 2026-10-30: "The Flames play some of the league’s fastest games" and later "Both teams like to play fast")
    for i, (ln, ks) in enumerate(lines):
        if not (ln.lower().startswith("both teams") and len(ks) == 1 and ", so " in ln):
            continue
        k = next(iter(ks))
        high = "slow" not in ln and "tight" not in ln
        own = [(t, x) for t in (away, home) for x in picks[t] if x[1] == k and side(x[2]) == high]
        if len(own) == 1:
            t, x = own[0]
            picks[t] = [y for y in picks[t] if y is not x]
            lines[i] = (ln.replace(", so ", f", {W.the(t)} most of all, so ", 1), ks)
    # pair a line with the club whose list tells one of its habits (the later club when both do)
    paired = {}
    for i, (ln, ks) in enumerate(lines):
        if ln.startswith("Both"):
            continue
        # the side that meets the habit goes first (the kill that faces a power play), so the attack keeps its own list
        options = [(t, x) for t in (home, away) for x in picks[t] if x[1] in ks and t not in paired and len(picks[t]) > 1]
        options.sort(key=lambda o: o[1][1] not in DEFEND)
        if options:
            t, hit = options[0]
            paired[t] = (hit, ln)
            picks[t] = [x for x in picks[t] if x is not hit]
    used = {ln for _, ln in paired.values()}
    # one habit at opposite ends for the two clubs is told side by side (Sora, 2026-10-30: the Oilers lean on their stars
    # more than anyone, the Islanders spread the ice time): the second club's half follows the first club's list
    opposite = {}
    for _, k, b in list(picks[away]):
        o = next((x for x in picks[home] if x[1] == k and side(x[2]) != side(b)), None)
        if o and len(picks[home]) > 1:
            opposite[away] = (home, o)
            picks[home] = [x for x in picks[home] if x is not o]
            break
    told, told_traits = 0, set()
    for t in (away, home):
        told_traits |= {k for _, k, _ in picks[t]}
        s = style(t, picks[t], W, stat_words(t, season, day), second=bool(told), key=key)
        if s:
            sents.append(s + ".")
            told += 1
        if t in opposite:  # "The Islanders, by contrast, spread the ice time through the lineup."
            o_t, (_, k, b) = opposite[t]
            sents.append(f"{cap(W.the(o_t))}, by contrast, {HABITS[k][0 if side(b) else 1]}.")
            told_traits.add(k)
        if t in paired:  # "They also sit back rather than chase on the forecheck, so the Canadiens have the edge ..."
            (_, k, b), ln = paired[t]
            told_traits.add(k)
            if k == "powerKill" and ln in as_pair:
                ln, link = as_pair[ln], ", and "
            else:
                link = ", and " if any(x in ln for x in FACE_OFF) else ", so "
            words = (IDENTITY[k][0 if b["rank"] == 1 else 1] if k in IDENTITY and side(b) and b["rank"] <= RANK_TOLD
                     else HABITS[k][0 if side(b) else 1])
            # the other club's power play is the subject by then, so the shorthanded side is named again (Sora, 2026-10-30)
            ln = _lower(ln) if k == "powerKill" and link == ", and " and "sets up a fight" in ln else _them(_lower(ln), t, W)
            if k not in DEFEND and ln.startswith("their ") and " could " in ln:  # their own attack: "..., and that could ..."
                ln, link = "that could " + ln.split(" could ", 1)[1], ", and "
            sents.append(f"{cap(W.the(t)) if t in opposite else 'They'} also {words}{link}{ln}")
        sc = scoring(t, season, day, W)
        if sc:
            sents.append(sc)
    said = " ".join(sents)
    rest = [(ln, ks) for ln, ks in lines if ln not in used and ln not in said]
    # two lines make one sentence when the second is short (Sora, 2026-10-30: "So ... . And both teams ..." read as
    # fragments); a second line with its own comma stands as a sentence with a full lead-in
    if rest:
        ln, ks = rest[0]
        follows = not ks or ks & told_traits  # "That means" only when a habit behind it was just told
        joins = (ln.split(" ", 1)[0] in ("The", "It’s", "Something", "Expect", "Good")  # "So the Rangers aren’t ..." reads wrong
                 and not any(x in ln for x in FACE_OFF) and follows)
        lead = _pick(["Put that together and ", "That means "] if not ln.startswith("Expect") else ["Put that together and "], *key, "meet1") \
            if joins and sents else ""
        first = lead + _lower(ln) if lead else ln
        if len(rest) == 2 and "," not in rest[1][0] and "," not in first:
            first = first[:-1] + ", and " + _lower(rest[1][0])
        elif len(rest) == 2:
            two = rest[1][0]
            first += " " + (_pick(["On top of that, ", "Beyond that, "], *key, "meet2") + _lower(two)
                            if two.split(" ", 1)[0] in ("The", "It’s", "Something", "Both", "Good") else two)
        sents.append(first)
    return " ".join(sents) or None


def _team_lines(season: int, day: str, t: str) -> pl.DataFrame:
    from pipeline.export.stories import book
    _, P, _ = book()
    return P.filter((pl.col("season") == season) & (pl.col("team") == t) & (pl.col("date") < day))


LEAGUE_TOLD = 10  # a team's scoring leader also gets his NHL rank when he is this high (Sora, 2026-10-30: McDavid leads the NHL)


@lru_cache(maxsize=64)
def _league_ranks(season: int, day: str) -> dict[int, dict]:
    """Every skater's NHL rank in points and goals this season, in games before `day`: {player: {pts: (rank, tied),
    g: (rank, tied)}}, rank 1 the most."""
    from pipeline.export.stories import book
    _, P, _ = book()
    t = (P.filter((pl.col("season") == season) & (pl.col("date") < day)).group_by("player_id")
         .agg(pl.col("pts").sum(), pl.col("g").sum()))
    out = {}
    for k in ("pts", "g"):
        vals = sorted(t[k].to_list(), reverse=True)
        for pid, v in zip(t["player_id"].to_list(), t[k].to_list()):
            out.setdefault(pid, {})[k] = (vals.index(v) + 1, vals.count(v) > 1) if v else (None, False)
    return out


def _nhl_rank(pid: int, k: str, season: int, day: str) -> tuple[int, bool] | None:
    r = (_league_ranks(season, day).get(pid) or {}).get(k)
    return r if r and r[0] and r[0] <= LEAGUE_TOLD else None


CARRY_GAP, CARRY_SHARE = 5, 1.4  # points ahead of the team's next scorer, and times as many, to be "carrying" the offense
VS_RUN = 5  # a point in this many straight games against tonight's opponent is worth saying


def _vs(pid: int, opp: str, day: str, W) -> tuple[str, str] | None:
    """His record against tonight's opponent since 2023-24, when it stands out: (clause, the item tag it replaces)."""
    from pipeline.export.stories import book
    _, P, _ = book()
    g = P.filter((pl.col("player_id") == pid) & (pl.col("opp") == opp) & (pl.col("date") < day)).sort("date")
    if g.is_empty():
        return None
    pts = g["pts"].to_list()
    run = 0
    for x in reversed(pts):
        if not x:
            break
        run += 1
    goals, gp = int(g["g"].sum()), g.height
    if run >= VS_RUN:
        return f"a point in {num(run)} straight games against {W.place(opp)}", "Owns them"
    if gp >= 6 and goals / gp >= 0.6:
        return f"{num(goals)} goals in {num(gp) if gp <= 12 else gp} games against {W.place(opp)} since 2023-24", "Haunts them"
    return None


def _count(n: int, word: str) -> str:
    return f"{num(n)} {word if n == 1 else word + 's'}"


def scorers(t: str, opp: str, season: int, day: str, W, N: dict, gone: set, second: bool = False, key=()) -> tuple[str | None, set, set]:
    """Who carries the scoring: the points leader, the next scorer worth naming (the goal leader, or the second-best
    scorer), a hot run over the team's last five games and a record against tonight's opponent riding on whoever it
    belongs to. Players the page lists as out are left out. Returns (sentence, players named, item tags told)."""
    from pipeline.export.stories import _pick
    p = _team_lines(season, day, t)
    if p.is_empty():
        return None, set(), set()
    p = p.filter(~pl.col("player_id").is_in(list(gone)))
    tot = (p.group_by("player_id").agg(pl.col("g").sum().cast(pl.Int64), pl.col("pts").sum().cast(pl.Int64), pl.len().alias("gp"))
           .filter(pl.col("pts") > 0).sort(["pts", "g", "player_id"], descending=[True, True, False]))
    if tot.is_empty() or tot.row(0, named=True)["player_id"] not in N:
        return None, set(), set()
    lead = tot.row(0, named=True)
    games = p.select("game_id", "date").unique().sort("date", "game_id")["game_id"].to_list()[-5:]
    last5 = {}
    if len(games) == 5:
        last5 = dict(p.filter(pl.col("game_id").is_in(games)).group_by("player_id").agg(pl.col("pts").sum().cast(pl.Int64)).iter_rows())
    gl = tot.sort(["g", "pts", "player_id"], descending=[True, True, False]).row(0, named=True)
    other = None
    if gl["player_id"] != lead["player_id"] and gl["g"] >= max(3, lead["g"]) and gl["player_id"] in N:
        other = gl
    elif tot.height > 1 and tot.row(1, named=True)["player_id"] in N and tot.row(1, named=True)["pts"] >= max(5, lead["pts"] * 0.7):
        other = tot.row(1, named=True)
    told_tags, used = set(), {lead["player_id"]}
    vs_told = False

    def extras(r):  # the riders on one player: a hot run, then his record against tonight's opponent
        nonlocal vs_told
        out = []
        if last5.get(r["player_id"], 0) >= HOT_POINTS:
            out.append(f"{num(last5[r['player_id']])} points in his last five games")
        v = _vs(r["player_id"], opp, day, W) if not vs_told else None
        if v:
            out.append(v[0])
            told_tags.add(v[1])
            vs_told = True
        return out
    def line(r):  # the box-score line, in figures (Sora, 2026-10-30): "10 G / 4 A / 14 P in 13 GP"
        return f"{r['g']} G / {r['pts'] - r['g']} A / {r['pts']} P in {r['gp']} GP"
    # each name is followed straight away by his line, which the page draws as a tag and links the name before it
    # (Sora, 2026-10-30): "Kyle Connor (10 G / 4 A / 14 P in 13 GP) leads the Jets in scoring"
    ride = extras(lead)
    tied = bool(other) and not (other is gl and gl["g"] > lead["g"]) and other["pts"] == lead["pts"]
    who = f"{N[lead['player_id']]} ({line(lead)})"
    lr = _nhl_rank(lead["player_id"], "pts", season, day)
    tied = tied and not lr  # a league rank is told instead, and the other player follows as usual
    if lr and lr[0] == 1:  # "leads the Oilers and the whole NHL in scoring"
        first = f"{who} {'is tied for the NHL lead in scoring' if lr[1] else f'leads {W.the(t)} and the whole NHL in scoring'}"
    elif lr:  # "leads the Oilers in scoring and is fifth in the NHL"
        first = f"{who} leads {W.the(t)} in scoring and is {'tied for ' if lr[1] else ''}{ordinal_word(lr[0])} in the NHL"
    elif tied:  # two level at the top: "is tied for the Islanders’ scoring lead. Matthew Schaefer (...) shares it"
        first = f"{who} is tied for {W.poss(t)} scoring lead"
    elif second:  # a full sentence, never "For the Islanders, it’s Bo Horvat" (Sora: it's Bo Horvat what?)
        first = cap(_pick([f"For {W.the(t)}, {who} leads the way in scoring", f"{W.poss(t)} scoring runs through {who}"],
                          *key, t, "lead2"))
    else:
        nxt = tot.row(1, named=True)["pts"] if tot.height > 1 else 0
        # "carrying" only for a clear lead (Sora, 2026-10-30: strong for someone two points ahead of the next guy)
        clear = lead["pts"] - nxt >= CARRY_GAP and lead["pts"] >= CARRY_SHARE * nxt
        first = _pick([f"{who} leads {W.the(t)} in scoring"] + ([f"{who} is carrying {W.poss(t)} offense"] if clear else []),
                      *key, t, "lead1")
    if ride:  # "..., who has nine points in his last five games"
        first += f", who has {and_list(ride)}" if first.endswith(")") and (second or "carrying" in first) else f", with {and_list(ride)}"
    if not other:
        return first + ".", used, told_tags
    used.add(other["player_id"])
    owho = f"{N[other['player_id']]} ({line(other)})"
    o_ride = extras(other)
    if other is gl and gl["g"] > lead["g"]:
        gr = _nhl_rank(other["player_id"], "g", season, day)
        what = f"has a team-high {_count(other['g'], 'goal')}" + (
            "" if not gr else ", tied for the most in the NHL" if gr[0] == 1 and gr[1] else ", the most in the NHL" if gr[0] == 1
            else f", {'tied for ' if gr[1] else ''}{ordinal_word(gr[0])} in the NHL")
    elif tied:
        what = "shares it"
    else:
        what = _pick(["isn’t far behind", "is next in line"], *key, t, "next")
    if o_ride:  # "Brady Tkachuk (...) has a team-high eight goals, with seven points in his last five games"
        return f"{first}. {owho} {what}, with {and_list(o_ride)}.", used, told_tags
    if ride:
        return f"{first}. {owho} {what}.", used, told_tags
    if tied:
        return f"{first}. {owho} {what}.", used, told_tags
    return f"{first}, and {owho} {what}.", used, told_tags


def who_scores(away: str, home: str, season: int, day: str, W, N: dict, gone: set, key=()) -> tuple[str | None, set, set]:
    sents, used, tags = [], set(), {}
    for t, o in ((away, home), (home, away)):
        s, u, tg = scorers(t, o, season, day, W, N, gone, second=bool(sents), key=key)
        if s:
            sents.append(s)
            used |= u
            if tg:
                tags[t] = tg
    return (" ".join(sents) or None), used, tags


@lru_cache(maxsize=4)
def _goalie_games(season: int) -> pl.DataFrame:
    from pipeline.metrics import goalies
    return goalies.per_game(season).select("game_id", "goalie_id", "date", "sa", "ga", "started")


NET_STARTS, NET_OFF = 4, 0.020  # starts, and save percentage off the league's, for "has been terrific" / "has struggled"


def in_net(away: str, home: str, projected: dict, season: int, day: str, W, N: dict) -> str | None:
    """The projected starters with their season so far: record, save percentage, starts."""
    from pipeline.export import goalie_streaks
    if not projected:
        return None
    from pipeline.export.stories import _pick
    gg = _goalie_games(season).filter(pl.col("date") < day)
    dec = goalie_streaks.decisions(season)
    lsa = int(gg["sa"].sum()) if gg.height else 0
    lg = (lsa - int(gg["ga"].sum())) / lsa if lsa else None  # the league's save percentage so far
    parts, said = [], []
    for t in (away, home):
        x = projected.get(t)
        if not x or x["id"] not in N:
            continue
        pid = x["id"]
        mine = gg.filter(pl.col("goalie_id") == pid)
        starts = int(mine["started"].sum()) if mine.height else 0
        verb = "is expected to start" if x.get("from") == "nhl" else "will likely start"
        if not starts:
            parts.append(f"{N[pid]} {verb} for {W.place(t)}, his first start of the season")
            continue
        ds = [d.get(pid) for gid, d in dec.items() if pid in d and gid in set(mine["game_id"].to_list())]
        rec = f"{ds.count('W')}-{ds.count('L')}-{ds.count('O')}"
        sa, ga = int(mine["sa"].sum()), int(mine["ga"].sum())
        sv = f"{(sa - ga) / sa:.3f}".lstrip("0") if sa else None
        # "Stuart Skinner (3-3-3 / .899 SV% in 9 GS) will likely start for Winnipeg": the line is a tag on the page
        tag = f"{N[pid]} ({rec}{' / ' + sv + ' SV%' if sv else ''} in {starts} GS)"
        # a word on how his season has gone, earned by the numbers (Sora, 2026-10-30, liked "Jacob Fowler has been
        # terrific for Montréal" in a rewrite): save percentage .020 off the league's, over 4+ starts
        off = (sa - ga) / sa - lg if sa and lg else 0
        if starts >= NET_STARTS and off >= NET_OFF:
            how = _pick([f"has been terrific for {W.place(t)}", f"has been outstanding for {W.place(t)}",
                         f"has been a rock in net for {W.place(t)}"], pid, day, "net+")
            said.append((t, f"{tag} {how} and {verb}"))
        elif starts >= NET_STARTS and off <= -NET_OFF:
            how = _pick([f"has struggled for {W.place(t)} so far", f"has had a rough start to the season for {W.place(t)}"], pid, day, "net-")
            said.append((t, f"{tag} {how}, but {verb}"))
        else:
            parts.append(f"{tag} {verb} for {W.place(t)}")
    if said:  # a judged goalie gets a sentence of his own, in game order; a plain one beside it takes other words
        plain = {t: x for t, x in zip([t for t in (away, home) if t not in dict(said) and projected.get(t) and projected[t]["id"] in N], parts)}
        alt = {"will likely start for": "is in line to start for", "is expected to start for": "is expected to go for"}
        out = []
        for t in (away, home):
            if t in dict(said):
                out.append(dict(said)[t])
            elif t in plain:
                x = plain[t]
                out.append(next((x.replace(a, b, 1) for a, b in alt.items() if a in x), x))
        return " ".join(cap(x) + "." for x in out)
    if not parts:
        return None
    if len(parts) == 2:
        verb = next((v for v in ("is expected to start", "will likely start") if v in parts[0] and v in parts[1]), None)
        second = parts[1].replace(f" {verb}", "", 1) if verb else parts[1]
        return cap(f"{parts[0]}, and {second}.")
    return cap(parts[0]) + "."


def standing(away: str, home: str, season: int, day: str, W, said: str) -> str | None:
    """Where they stand going in: division places from the last saved standings before the game (league first or
    last when true), with the record unless the paragraph already gave it."""
    from pipeline.export.stories import _table_marks
    from pipeline.ingest import standings_daily as sd
    rows = sd.before(season, day)
    if not rows:
        return None
    m = _table_marks(rows)
    if any(t not in m or m[t]["gp"] < MIN_GAMES for t in (away, home)):
        return None
    rec = {r["teamAbbrev"]: f"{r['wins']}-{r['losses']}-{r['otLosses']}" for r in rows if "wins" in r}

    def where(t):
        x = m[t]
        if x["pts"] == 1 and x["pts_n"] == 1:
            return "first in the NHL"
        if x["pts_low"] == 1 and x["pct_low"] == 1:
            return "last in the NHL"
        return f"last in the {x['division']}" if x["div"] == x["div_n"] else f"{ordinal_word(x['div'])} in the {x['division']}"

    def who(t):
        r = rec.get(t)
        return W.the(t) + (f" ({r})" if r and r not in said else "")
    def nhl(t):  # the league place beside the division one (Sora, 2026-10-30: sixth in a weak Pacific is near the bottom)
        x = m[t]
        return f"{'tied for ' if x['pts_n'] > 1 else ''}{ordinal_word(x['pts'])} in the NHL"
    # each division's points percentage, for "the weakest of the four divisions so far"
    pct = {}
    for r in rows:
        d_ = r.get("divisionName")
        if d_ and r.get("gamesPlayed"):
            pct.setdefault(d_, []).append(r["points"] / (2 * r["gamesPlayed"]))
    order = sorted(pct, key=lambda d_: -sum(pct[d_]) / len(pct[d_]))
    a, h = where(away), where(home)
    div = m[away]["division"]
    if m[home]["division"] == div and " NHL" not in a + h:
        out = (f"This is {article(div)} Division matchup, with {who(away)} {a.split(' in the ')[0]} ({nhl(away)}) "
               f"and {who(home)} {h.split(' in the ')[0]} ({nhl(home)[: -len(' in the NHL')]}).")
        if len(order) == 4 and div in (order[0], order[-1]):
            out += f" The {div} has been the {'strongest' if div == order[0] else 'weakest'} of the four divisions so far."
        return out
    a_ = a if " NHL" in a else f"{a} ({nhl(away)})"
    h_ = h if " NHL" in h else f"{h} ({nhl(home)})" if " NHL" in a else f"{h} ({nhl(home)[: -len(' in the NHL')]})"  # "in the NHL" once
    return f"Going in, {who(away)} are {a_} and {who(home)} {h_}."


def paragraphs(gid: int, away: str, home: str, day: str, W, N: dict, fp: dict | None, projected: dict | None,
               gone: set, said: str, season: int = CURRENT_SEASON, gists: list[str] = (), covered: set = frozenset()) -> dict:
    """The fuller preview's extra parts: `standing` (a sentence for the opening paragraph), then `play` (habits and what
    they mean against each other, with the trait calls' `gists`), `scoring` and `net` paragraphs; `used` (players the
    scoring paragraph named) and `told` ({team: item tags its records against the opponent replace})."""
    out = {"standing": None, "play": None, "scoring": None, "net": None, "used": set(), "told": {}}
    key = (gid,)
    for k, f in (("standing", lambda: standing(away, home, season, day, W, said)),
                 ("play", lambda: how_they_play(away, home, fp or {}, season, day, W, gists, covered, key)),
                 ("net", lambda: in_net(away, home, projected or {}, season, day, W, N))):
        try:
            out[k] = f()
        except Exception as e:  # each part is optional; the preview stands without it
            print(f"preview {gid}: {k} failed: {e!r}")
    try:
        out["scoring"], out["used"], out["told"] = who_scores(away, home, season, day, W, N, gone, key)
    except Exception as e:
        print(f"preview {gid}: scoring failed: {e!r}")
    return out
