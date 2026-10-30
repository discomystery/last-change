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
    "discipline": ("draw more power plays than they give up", "give up more power plays than they draw"),
}
LABELS = {"volume": "shot volume", "quality": "shot quality", "rebounds": "second chances", "turnover": "offense off turnovers",
          "dShots": "shots from defensemen", "inClose": "shots from in close", "shotsThrough": "getting shots through",
          "suppression": "shot suppression", "qualityAllowed": "keeping dangerous shots away", "breakdowns": "avoiding breakdowns",
          "longShifts": "avoiding long shifts", "blocking": "blocking shots", "pointAllowed": "point shots allowed",
          "icing": "avoiding icings", "goalie": "goaltending", "pace": "pace", "forecheck": "forecheck pressure",
          "physical": "physicality", "rough": "rough stuff", "pp": "the power play", "pk": "the penalty kill",
          "powerKill": "shorthanded threat", "discipline": "discipline"}
NEUTRAL = {"pointAllowed", "dShots", "inClose", "pace", "volume", "rough", "physical", "forecheck", "turnover"}  # no "worst" wording
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
    picked = []
    for k, d in dims.items():
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
}
# A weakness near the bottom, said as how few teams are worse: (plural verb for "only N teams", singular for "no team").
FEW_WORSE = {
    "quality": ("average a lower chance of scoring per shot", "averages a lower chance of scoring per shot"),
    "suppression": ("give up more shots", "gives up more shots"),
    "qualityAllowed": ("give up more dangerous shots", "gives up more dangerous shots"),
    "pp": ("get less from their power play", "gets less from its power play"),
    "pk": ("have a leakier penalty kill", "has a leakier penalty kill"),
    "longShifts": ("get pinned in their own end more often", "gets pinned in its own end more often"),
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
        else:
            words = HABITS[k][0 if hi else 1]
        (downs if _is_bad(k, b) else ups).append((k, b, words, st.get(k, {}).get("hi" if hi else "lo")))
    figured = {k for k, b, w, f in ups + downs if f}

    def told_list(group):  # the figure behind a habit: a trailing clause on the last one, in brackets mid-list
        return and_list([w + ("" if not f else f", {f[0]} ({f[1]})" if i == len(group) - 1 else f" ({f[0]}, {f[1]})")
                         for i, (k, b, w, f) in enumerate(group)])
    mixed = bool(ups and downs) and any(_is_good(k, b) for k, b, _, _ in ups)
    out = X + " " + (f"{told_list(ups)}, but {told_list(downs)}" if mixed else told_list(ups + downs))
    # ranks only at the top end of a scale: "32nd in rough stuff" reads as a knock on a calm team
    told = [(b["rank"], k) for k, b, w, _ in ups
            if b["rank"] <= RANK_TOLD and b["pct"] >= 50 and k not in figured and not ("anyone" in w or "league’s" in w)]
    if mixed and told:  # one contrast a sentence: the ranks get their own
        r, k = told[0]
        out += (f". No team is better at {LABELS[k]}" if r == 1 and k not in NEUTRAL else f". They’re {ordinal_word(r)} in {LABELS[k]}")
    elif figured and told:  # a list that already carries a figure gets its ranks in a sentence of their own
        out += ". They’re " + and_list([f"{ordinal_word(r)} in {LABELS[k]}" for r, k in told])
    elif len(told) > 1 and len(told) == len(picked) and all(r == told[0][0] for r, _ in told):
        out += f", and they’re {ordinal_word(told[0][0])} in the league in {'both' if len(told) == 2 else 'all three'}"
    elif told:
        out += ", and they’re " + and_list([f"{ordinal_word(r)} in {LABELS[k]}" for r, k in told])
    worst = next(((k, b) for k, b, _, _ in downs if k in FEW_WORSE and k not in figured and b["rank"] >= 30), None)
    if worst:  # "Only three teams average a lower chance of scoring per shot."
        k, b = worst
        n = 32 - b["rank"]
        out += f". No team {FEW_WORSE[k][1]}" if n == 0 else f". Only {num(n)} {'team' if n == 1 else 'teams'} {FEW_WORSE[k][1 if n == 1 else 0]}"
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
    ("pp", "pk", "{Ap} power play could do real damage against {Bp} kill",
     "{Ap} power play against {Bp} kill is strength against strength", "{Ap} struggling power play gets a tough test from {Bp} kill"),
    ("volume", "suppression", "{A} should get plenty of pucks to the net against {B}",
     "something has to give when {Ap} shooting runs into {Bp} defense", "{A} may have a hard time getting pucks to the net against {B}"),
    ("quality", "qualityAllowed", "{A} could find some good looks against {B}, who give up plenty of them",
     "{Ap} good looks will be harder to come by against {B}", "good looks should be hard to come by for {A}"),
    ("forecheck", "longShifts", "{Ap} forecheck could keep {B} pinned in their own end", None, None),
    ("shotsThrough", "blocking", None, "{Ap} knack for getting shots through meets a team that blocks a lot",
     "{B} should get in the way of a lot of {Ap} shots"),
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
        out.append((60, "expect a fast, open game", {"pace"}))
    elif "pace" not in covered and both("pace", lambda p: p <= MEET_LO):
        out.append((60, "expect a tight, low-event game", {"pace"}))
    heavy = [t for t in (away, home) if (pct(t, "physical") or 0) >= 85 and (pct(t, "rough") or 0) >= 70]
    if "physical" not in covered and (heavy or both("physical", lambda p: p >= MEET_HI)):
        out.append((70 if heavy else 50, "expect a chippy, heavy game" if heavy else "both teams like to hit, so expect plenty of it", {"physical"}))
    return sorted(out, key=lambda x: -x[0])


def _lower(s: str) -> str:
    """Lower the first word when it is not a name ("The Panthers’ power play ..." -> "the Panthers’ power play ...")."""
    first = s.split(" ", 1)[0]
    return s[0].lower() + s[1:] if first in ("The", "It’s", "Something", "Expect", "Both", "Good", "Neither") else s


TRAIT_CALLS = ("clash", "duel", "edge", "changed", "pace", "physical")  # calls about the two styles: their gist is a consequence


def how_they_play(away: str, home: str, fp: dict, season: int, day: str, W, gists: list[str] = (), covered: set = frozenset(),
                  key=()) -> str | None:
    """Each club's habits, then what they mean when they meet: the trait calls' own lines first, then our pairings."""
    from pipeline.export.stories import _pick
    sents = []
    picks = {t: _picks(t, fp) for t in (away, home)}
    side = lambda b: b["pct"] >= 50
    shared = [(k, b) for _, k, b in picks[away] if any(k == k2 and side(b) == side(b2) for _, k2, b2 in picks[home])]
    if shared:  # "Both teams get their defensemen shooting." once, not in each team's list
        sents.append(f"Both teams {and_list([HABITS[k][0 if side(b) else 1] for k, b in shared])}.")
        gone = {k for k, _ in shared}
        picks = {t: [x for x in v if x[1] not in gone] for t, v in picks.items()}
    told = 0
    for t in (away, home):
        s = style(t, picks[t], W, stat_words(t, season, day), second=bool(told), key=key)
        if s:
            sents.append(s + ".")
            told += 1
        sc = scoring(t, season, day, W)
        if sc:
            sents.append(sc)
    said = " ".join(sents)
    lines = [g for g in gists if g]
    for _, text, _k in consequences(away, home, fp, W, set(covered)):
        if len(lines) >= 2:
            break
        if text not in said:
            lines.append(cap(text) + ".")
    # two lines make one sentence when the second is short (Sora, 2026-10-30: "So ... . And both teams ..." read as
    # fragments); a second line with its own comma stands as a sentence with a full lead-in
    lines = lines[:2]
    if lines:
        ln = lines[0]
        joins = (ln.split(" ", 1)[0] in ("The", "It’s", "Something", "Expect", "Both", "Good")  # "So the Rangers aren’t ..." reads wrong
                 and not any(x in ln for x in ("strength against strength", "has to give", "meets a team")))  # a face-off, not a result
        lead = _pick(["Put that together and ", "That means "] if not ln.startswith("Expect") else ["Put that together and "], *key, "meet1") \
            if joins and sents else ""
        first = lead + _lower(ln) if lead else ln
        if len(lines) == 2 and "," not in lines[1] and "," not in first:
            first = first[:-1] + ", and " + _lower(lines[1])
        elif len(lines) == 2:
            two = lines[1]
            first += " " + (_pick(["On top of that, ", "Beyond that, "], *key, "meet2") + _lower(two)
                            if two.split(" ", 1)[0] in ("The", "It’s", "Something", "Both", "Good") else two)
        sents.append(first)
    return " ".join(sents) or None


def _team_lines(season: int, day: str, t: str) -> pl.DataFrame:
    from pipeline.export.stories import book
    _, P, _ = book()
    return P.filter((pl.col("season") == season) & (pl.col("team") == t) & (pl.col("date") < day))


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
    ride = extras(lead)
    name, pts = N[lead["player_id"]], _count(lead["pts"], "point")
    if len(ride) == 1 and lead["g"] and lead["pts"] > lead["g"]:
        # "Matthew Tkachuk (4 G / 16 A / 20 P in 13 GP, nine points in his last five games)"
        who = f"{name} ({line(lead)}, {ride[0]})"
        first = (_pick([f"{W.poss(t)} scoring runs through {who}", f"For {W.the(t)}, it starts with {who}"], *key, t, "lead2") if second
                 else _pick([f"{who} leads {W.the(t)}", f"{W.poss(t)} offense runs through {who}"], *key, t, "lead1"))
        first = cap(first)
    else:
        stat = f"{pts} ({line(lead)})"
        if second:
            first = cap(_pick([f"For {W.the(t)}, it’s {name}, with {stat}", f"{W.poss(t)} scoring runs through {name}, who has {stat}"], *key, t, "lead2"))
        else:
            first = _pick([f"{name} leads {W.the(t)} with {stat}", f"{name} is carrying {W.poss(t)} offense with {stat}"], *key, t, "lead1")
        if ride:
            first += f", and he has {and_list(ride)}"
    if not other:
        return first + ".", used, told_tags
    used.add(other["player_id"])
    oname = N[other["player_id"]]
    o_ride = extras(other)
    if other is gl and gl["g"] > lead["g"]:
        what = f"a team-high {_count(other['g'], 'goal')} ({line(other)})"
    else:
        what = f"{_count(other['pts'], 'point')} ({line(other)})"
    if o_ride:  # "and Brady Tkachuk, who has 15 points and a point in five straight games against Winnipeg"
        return f"{first}. {oname}, who has {and_list(o_ride)}, has {what}.", used, told_tags
    return f"{first}, and {oname} has {what}.", used, told_tags


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


def in_net(away: str, home: str, projected: dict, season: int, day: str, W, N: dict) -> str | None:
    """The projected starters with their season so far: record, save percentage, starts."""
    from pipeline.export import goalie_streaks
    if not projected:
        return None
    gg = _goalie_games(season).filter(pl.col("date") < day)
    dec = goalie_streaks.decisions(season)
    parts = []
    for t in (away, home):
        x = projected.get(t)
        if not x or x["id"] not in N:
            continue
        pid = x["id"]
        mine = gg.filter(pl.col("goalie_id") == pid)
        starts = int(mine["started"].sum()) if mine.height else 0
        verb = "is expected to start" if x.get("from") == "nhl" else "should get the start"
        who = f"{N[pid]} {verb} for {W.place(t)}"
        if not starts:
            parts.append(f"{who}, his first start of the season")
            continue
        ds = [d.get(pid) for gid, d in dec.items() if pid in d and gid in set(mine["game_id"].to_list())]
        rec = f"{ds.count('W')}-{ds.count('L')}-{ds.count('O')}"
        sa, ga = int(mine["sa"].sum()), int(mine["ga"].sum())
        sv = f"{(sa - ga) / sa:.3f}".lstrip("0") if sa else None
        parts.append(f"{who} ({rec}{', ' + sv if sv else ''} in {num(starts)} {'start' if starts == 1 else 'starts'})")
    if not parts:
        return None
    if len(parts) == 2:
        verb = next((v for v in ("is expected to start", "should get the start") if v in parts[0] and v in parts[1]), None)
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
