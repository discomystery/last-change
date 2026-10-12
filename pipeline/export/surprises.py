"""What the preview didn't see coming: the parts of a finished game furthest from how these two teams usually play.

Every candidate gets a score: how many night-to-night spreads (standard deviations, pooled within team-seasons over
2023-24 to 2025-26) tonight's figure sat from what the teams' usual play predicted. Event-type candidates (a goalie's
night, a new line, a result against the run of play) are put on the same footing. The page then shows at most one per
kind of thing, biggest first: up to four that clear a clear bar, topped up to two with smaller twists, and a plain
"true to form" note when nothing strayed at all.

What "usual" means takes in both teams and what they are known for (Sora, 2026-10-12, after Carolina blanking
Philadelphia's power play read "out of character": Philadelphia had been last in the league at converting power plays in
four of the last five seasons and Carolina's kill top two in four). How rare tonight was is measured against what the
matchup predicted (a power play against tonight's kill, shot quality against tonight's defense). Where the teams stand
is a "standing": their league percentile this season and last, and for special teams also where they ranked in real
goal conversion over the last five seasons (`history.summaries`), the opponent counted against it.

Each one says why it was unusual:
  broke the pattern   tonight landed on the other side of the league average from where the matchup stood
  bad to worse        a team known for a weakness (or facing a known strength) was worse even than that
  good to better      the same for a strength
  further than usual  the usual direction, much further, from a team that is not at an extreme
  closer than usual   the usual direction, but much nearer average
  out of character    a matchup that usually lands about average was far from it
Shot counts are the most common way a game strays, so they need a bigger departure to make the list.
"""
import math
from functools import lru_cache
from statistics import NormalDist

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS
from pipeline.export.previews import a_pct
from pipeline.ingest import history
from pipeline.metrics import adjust, goalies, team_style

BIG, SMALL = 1.75, 1.0  # scores for a surprise and for a smaller twist
KNOWN_BONUS = 0.25  # "bad to worse" and "good to better" are a story of their own (Sora, 2026-10-12)
SHOT_WEIGHT = 0.75  # shot share has to stray further (2.3 spreads) to count as a surprise
MAX_SHOWN, MIN_SHOWN = 4, 2
TAGS = {"break": "Broke the pattern", "further": "Further than usual", "muted": "Closer than usual",
        "unusual": "Out of character", "rare": "Out of the ordinary", "worse": "Bad to worse", "better": "Good to better"}
SIDE, EXTREME = 0.5, 0.9  # standings (bell-curve units): leaning one way, and known for it
REP_WEIGHTS = (0.4, 0.55, 0.7, 0.85, 1.0)  # oldest to latest finished season


def _share(r: dict) -> float | None:
    t = r["cf_adj"] + r["ca_adj"]
    return 100 * r["cf_adj"] / t if t and r["sec5"] >= 1200 else None


def _quality(r: dict) -> float | None:
    return r["xgf_adj"] / r["ff_adj"] if r["ff_adj"] >= 15 and r["xgf_adj"] > 0 else None


def _pp(r: dict) -> float | None:
    return 3600 * r["pp_xgf"] / r["pp_sec"] if r["pp_sec"] >= 180 else None


def _hits(r: dict) -> float | None:
    return 3600 * r["hits_adj"] / r["close_sec"] if r["close_sec"] >= 900 else None


def _depth(r: dict) -> float | None:
    return 100 * r["bottom6_sec"] / r["fwd_sec"] if r["fwd_sec"] else None


STATS = {"share": _share, "quality": _quality, "pp": _pp, "hits": _hits, "depth": _depth}


@lru_cache(maxsize=1)
def spreads() -> dict[str, np.ndarray]:
    """Night-to-night departures of each figure from the team's own season level (and a starting goalie's goals saved
    above expected in one game), sorted. Hits and power-play rates have long tails, so how rare a departure is comes
    from these real departures, not from a bell curve."""
    w = adjust.weights()
    dev = {k: [] for k in STATS}
    for season in FULL_SEASONS:
        for (_,), g in team_style.per_game(season, w).group_by("team_id"):
            rows = g.to_dicts()
            for k, f in STATS.items():
                v = np.array([x for x in map(f, rows) if x is not None])
                if len(v) > 5:
                    dev[k] += list(v - v.mean())
    out = {k: np.sort(np.array(v)) for k, v in dev.items()}
    gsax = []
    for season in FULL_SEASONS:
        st = goalies.per_game(season).filter(pl.col("started") & (pl.col("sec") >= 2400))
        gsax += (st["xga"] - st["ga"]).to_list()
    out["gsax"] = np.sort(np.array(gsax) - np.mean(gsax))
    return out


def sd(k: str) -> float:
    return float(np.std(spreads()[k]))


def tail(k: str, dev: float) -> float:
    """Share of past team-games that strayed at least this far in the same direction."""
    d = spreads()[k]
    n = (d >= dev).sum() if dev > 0 else (d <= dev).sum()
    return max(float(n), 0.5) / len(d)


def z_of(k: str, dev: float) -> float:
    """A departure on a common footing: the bell-curve distance with the same rarity as this one really has."""
    return float(np.sign(dev) * NormalDist().inv_cdf(1 - min(0.5, tail(k, dev))))


def every(k: str, dev: float) -> int:
    """About how many team-games go by between departures this big in this direction, rounded to a friendly number."""
    n = 1 / tail(k, dev)
    for step in (1, 5, 10, 25, 50, 100):
        if n < step * 10:
            return max(2, int(round(n / step) * step))
    return int(round(n, -2))


def rarity(k: str, dev: float) -> str:
    n = every(k, dev)
    return f"A team strays this far about once every {n} games." if n < 1000 else \
        "A team strays this far less than once in a thousand games."


def classify(usual: float, x: float, mid: float, sd: float, standing: float | None = None, own: float = 0.0, opp: float = 0.0) -> str:
    """`usual` is what the matchup predicted, `x` tonight, `mid` the league average, `sd` the night-to-night spread.
    `standing` says where the matchup leans in bell-curve units (positive = this figure runs high); `own` and `opp` are
    the two halves of it, this team's and the opponent's (already turned to this figure's direction). Without a
    standing, the old rule: a usual within a quarter spread of the average counts as average."""
    margin = 0.25 * sd
    if standing is None:
        side_u = 0 if abs(usual - mid) < margin else (1 if usual > mid else -1)
    else:
        side_u = 0 if abs(standing) < SIDE else (1 if standing > 0 else -1)
    if side_u == 0:
        return "unusual"
    if (x - mid) * side_u < 0 and abs(x - mid) >= margin:
        return "break"
    if (x - usual) * side_u <= 0:
        return "muted"
    if abs(own) >= EXTREME and own * side_u > 0:
        return "better" if own > 0 else "worse"  # a strength of theirs got stronger, or a weakness weaker
    if abs(opp) >= EXTREME and opp * side_u > 0:
        return "worse" if opp > 0 else "better"  # the opponent's weakness was exposed further, or its strength showed more
    return "further"


def _item(kind: str, score: float, cls: str, head: str, body: str, sig: tuple | None = None) -> dict:
    if cls in ("worse", "better"):
        score += KNOWN_BONUS
    out = {"kind": kind, "score": round(score, 2), "tag": TAGS[cls], "head": head, "body": body}
    if sig:
        out["sig"] = sig  # (figure, team, went up): lets a surprise be matched against the preview's calls
    return out


NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


@lru_cache(maxsize=4)
def reputation(season: int = CURRENT_SEASON) -> dict[tuple[str, str], list[int]]:
    """(team, "pp" or "pk") -> league ranks in real goal conversion over the finished seasons before this one, oldest
    first (1 = best). Empty when the summaries were never fetched."""
    df = history.summaries(season + 1)
    out = {}
    if df.is_empty():
        return out
    for (abbr,), g in df.sort("year").group_by("abbrev", maintain_order=True):
        for k in ("pp", "pk"):
            out[(abbr, k)] = g[f"{k}_rank"].to_list()
    return out


def _z_pct(pct: float) -> float:
    return NormalDist().inv_cdf(min(98, max(2, pct)) / 100)


def _rep_z(ranks: list[int]) -> float:
    w = REP_WEIGHTS[-len(ranks):]
    return sum(wi * NormalDist().inv_cdf((32.5 - r) / 32) for wi, r in zip(w, ranks)) / sum(w)


def _rep_words(ranks: list[int]) -> tuple[str, str] | None:
    """("last in the league", "in four of the last five seasons"), or None when the record is not that clear."""
    n = len(ranks)
    if n < 3:
        return None
    for lim, need, lo, hi in ((1, 3, "last in the league", "first in the league"), (3, 3, "in the bottom three", "in the top three"),
                              (5, 3, "in the bottom five", "in the top five"), (10, 4, "in the bottom ten", "in the top ten")):
        for word, count in ((lo, sum(r > 32 - lim for r in ranks)), (hi, sum(r <= lim for r in ranks))):
            if count >= need:
                return word, (f"in each of the last {NUM.get(n, n)} seasons" if count == n else f"in {NUM.get(count, count)} of the last {NUM.get(n, n)} seasons")
    return None


def _pct(fp: dict, t: str, dim: str) -> float | None:
    b = fp.get(t, {}).get("dims", {}).get(dim, {}).get("blend") or {}
    return b.get("pct")


# How each figure leans: (this team's trait, the opponent's trait that pushes against it), and the words for a team known
# for a trait (top or bottom 15% this season and last). Teams are "they".
LEANS = {"pp": ("pp", "pk"), "quality": ("quality", "qualityAllowed"), "hits": ("physical", None), "depth": ("depth", None)}
KNOWN = {"pp": ("have one of the league’s most dangerous power plays", "have one of the league’s least dangerous power plays"),
         "pk": ("have one of the league’s stingiest penalty kills", "have one of the league’s leakiest penalty kills"),
         "quality": ("usually get some of the league’s most dangerous shots", "usually get some of the league’s least dangerous shots"),
         "qualityAllowed": ("are one of the league’s best at preventing dangerous shots", "usually give up some of the league’s most dangerous shots"),
         "physical": ("are one of the league’s heaviest-hitting teams", "are one of the league’s lightest-hitting teams"),
         "depth": ("lean on their bottom six more than most teams", "ride their top six more than most teams"),
         "volume": ("throw more pucks at the net than almost anyone", "take fewer shots than almost anyone"),
         "suppression": ("allow fewer shots than almost anyone", "allow more shots than almost anyone")}
MILD = {"pp": ("have a better power play than most", "have a weaker power play than most"),
        "pk": ("kill penalties better than most", "have a weaker penalty kill than most"),
        "quality": ("usually get better looks than most teams", "usually get worse looks than most teams"),
        "qualityAllowed": ("usually allow fewer dangerous shots than most teams", "usually allow more dangerous shots than most teams"),
        "physical": ("usually hit more than most teams", "usually hit less than most teams"),
        "depth": ("usually use their bottom six more than most teams", "usually use their bottom six less than most teams"),
        "volume": ("usually shoot more than most teams", "usually shoot less than most teams"),
        "suppression": ("usually allow fewer shots than most teams", "usually allow more shots than most teams")}
NEUTRAL = {"hits", "depth"}  # style, not quality: never "good to better" or "bad to worse"
REP = {"pp": ("power play", " in conversion"), "pk": ("penalty kill", "")}


def _trait(fp: dict, t: str, dim: str, season: int, places: dict) -> tuple[float, str | None, bool]:
    """Where a team stands on one trait in bell-curve units (positive = more of it, or better at it) and a clause
    saying what they are known for, if anything, and whether they are known for it (top or bottom 15% this season and
    last, or a clear five-season record in real goal conversion). Special teams lean half on that record."""
    pct = _pct(fp, t, dim)
    z = _z_pct(pct) if pct is not None else 0.0
    T = places[t]
    words = None
    known = pct is not None and (pct >= 85 or pct <= 15)
    if known:
        words = f"{T} {KNOWN[dim][0 if pct >= 85 else 1]}"
    elif pct is not None and (pct >= 70 or pct <= 30):
        words = f"{T} {MILD[dim][0 if pct >= 70 else 1]}"
    ranks = reputation(season).get((t, dim)) if dim in REP else None
    if ranks:
        rz = _rep_z(ranks)
        z = 0.5 * z + 0.5 * rz if pct is not None else rz
        rw = _rep_words(ranks)
        if rw and (rz > 0) == (z > 0) and abs(rz) >= EXTREME:
            what, how = REP[dim]
            words = f"{T}’s {what} has ranked {rw[0]}{how} {rw[1]}"
            known = True
        elif known and (rz > 0) != (z > 0):
            known = False  # this season and last say one thing, five seasons of goals the other
    return z, words, known


def standing(kind: str, t: str, o: str, fp: dict, places: dict, season: int = CURRENT_SEASON) -> tuple[float, float, float, list[str]]:
    """How this matchup leans for team t's figure: (total, t's half, the opponent's half turned to t's direction, the
    clauses that explain the lean, t's first). A half a team is known for counts as at least EXTREME."""
    mine, theirs = LEANS[kind]
    own, w1, k1 = _trait(fp, t, mine, season, places)
    opp, w2, k2 = (0.0, None, False)
    if theirs:
        oz, w2, k2 = _trait(fp, o, theirs, season, places)
        opp = -oz  # a strong kill or defense pushes this team's figure down
    total = own + opp
    why = [w for w, z in ((w1, own), (w2, opp)) if w and abs(z) >= SIDE and z * total > 0]
    lead = lambda z, k: math.copysign(max(abs(z), EXTREME), z) if k else math.copysign(min(abs(z), 0.99 * EXTREME), z)
    return total, lead(own, k1), lead(opp, k2), why


def _and(xs: list[str]) -> str:
    if len(xs) == 1:
        return xs[0]
    a, b = xs
    for tail in (" of the last five seasons", " of the last four seasons", " of the last three seasons"):
        if a.endswith(tail) and b.endswith(tail) and not b.startswith("in each"):
            b = b[: -len(tail)] + " of them"
    return f"{a}, and {b}"


def _cap(x: str) -> str:
    return x[:1].upper() + x[1:]


def compose(cls: str, why: list[str], fig: str, short: str, rare: str, bridge: str, expect: str = "", who: str = "") -> str:
    """The card's body: what the teams are known for (when it matters to the twist), what happened, how rare it was.
    `bridge` names what happened in a few words ("a dry night"); `short` is the figure without the team's name, used
    only when every clause before it is about that team (`who`), so "their usual" cannot point at the opponent."""
    if why and not all(w.startswith(who) for w in why):
        short = fig
    if cls == "break" and why:
        return f"{_and(why)}. Not tonight: {short} {rare}"
    if cls in ("worse", "better", "further") and why:
        return f"{_and(why)}. Even by those standards, this was {bridge}: {short} {rare}"
    if cls == "muted" and why:
        return f"{_and(why)}, but it hardly showed tonight: {short} {rare}"
    return " ".join(x for x in (fig, expect, rare) if x)


def _friendly(n: float) -> int:
    for step in (1, 5, 10, 25, 50, 100):
        if n < step * 10:
            return max(2, int(round(n / step) * step))
    return int(round(n, -2))


BRIDGE = {"quality": ("a night of good looks", "a night of harmless shots"), "pp": ("a big night", "a dry night"),
          "hits": ("a lot of hitting", "a quiet night on the body"), "depth": ("heavy use of the bottom six", "a short bench")}


def shot_share(rows: dict, a: str, h: str, fp: dict, places: dict, season: int = CURRENT_SEASON) -> dict | None:
    """Share of 5-on-5 shot attempts against what the two teams' habits predict for this matchup."""
    try:
        vol = {t: fp[t]["dims"]["volume"]["blend"] for t in (a, h)}
        sup = {t: fp[t]["dims"]["suppression"]["blend"]["v"] for t in (a, h)}
        league = vol[a]["v"] * 100 / vol[a]["index"]
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    x = {t: _share(rows[t]) for t in (a, h)}
    if x[a] is None:
        return None
    cf = {a: vol[a]["v"] * sup[h] / league, h: vol[h]["v"] * sup[a] / league}
    e = {t: 100 * cf[t] / (cf[a] + cf[h]) for t in (a, h)}
    t = a if x[a] - e[a] > 0 else h  # told from the side of the team that beat expectations
    o = h if t == a else a
    dev = x[t] - e[t]
    z = z_of("share", dev)
    zs = {k: {d: _z_pct(_pct(fp, k, d) or 50) for d in ("volume", "suppression")} for k in (a, h)}
    own = (zs[t]["volume"] + zs[t]["suppression"]) / 2
    opp = -(zs[o]["volume"] + zs[o]["suppression"]) / 2
    cls = classify(e[t], x[t], 50, sd("share"), own + opp, own, opp)
    T, O = places[t], places[o]
    why = []
    for k, sign in ((t, 1), (o, -1)):  # t's strengths and the opponent's weaknesses lean t's way
        ps = {d: _pct(fp, k, d) for d in ("volume", "suppression")}
        hits = [d for d, p in ps.items() if p is not None and (p >= 85 or p <= 15) and sign * _z_pct(p) * (own + opp) > 0]
        if len(hits) == 2:
            words = "control the shot count like almost no one else" if ps["volume"] >= 85 else "get outshot more than almost anyone"
            why.append((abs(_z_pct(ps["volume"])) + abs(_z_pct(ps["suppression"])), f"{places[k]} {words}"))
        elif hits:
            why.append((abs(_z_pct(ps[hits[0]])), f"{places[k]} {KNOWN[hits[0]][0 if ps[hits[0]] >= 85 else 1]}"))
    why = [w for _, w in sorted(why, reverse=True)[:2]]
    head = {"break": f"{T} flipped the shot count", "further": f"{T} buried {O} in shots", "better": f"{T} buried {O} in shots",
            "worse": f"{T} buried {O} in shots", "muted": f"{T} held their own on shots", "unusual": f"{T} dominated the shot count"}[cls]
    expect = (f"From how these two usually play, {T} would expect about {e[t]:.0f}%." if cls != "unusual"
              else "These two usually split the shots about evenly.")
    # The real count reads better than a weighted share here: the weighting barely moves a share (Sora, 2026-10-12).
    took, all_ = int(rows[t]["cf5"]), int(rows[t]["cf5"] + rows[o]["cf5"])
    fig = f"{T} took {took} of the {all_} shot attempts at 5-on-5."
    if why:
        short = f"{T} took {took} of the {all_} shot attempts at 5-on-5. {expect}"
        body = compose(cls, why, f"{fig} {expect}", f"{short}", rarity('share', dev), "lopsided", who=T)
    else:
        body = f"{fig} {expect} {rarity('share', dev)}"
    return _item("shots", z * SHOT_WEIGHT, cls, head, body, ("shots", t, True))


HEADS = {  # kind -> (went up, went down): class -> headline, {T} the team whose figure it is, {O} the opponent
    "quality": ({"default": "{T} got the good looks", "break": "{T} found the good looks for once", "better": "{T} got even better looks than usual",
                 "worse": "{T} picked {O}’s defense apart"},
                {"default": "{T} couldn’t get good looks", "break": "{T} couldn’t get good looks", "worse": "{T}’s shots went from harmless to more harmless",
                 "better": "{O} took away the good looks"}),
    "pp": ({"default": "{T}’s power play came alive", "break": "{T}’s power play came alive", "better": "{T}’s power play was at its best",
            "worse": "{T}’s power play feasted on {O}’s kill"},
           {"default": "{O} shut down {T}’s power play", "break": "{O} shut down {T}’s power play", "worse": "{T}’s power play hit a new low",
            "better": "{O}’s kill smothered {T}’s power play"}),
    "hits": ({"default": "{T} brought the hits", "better": "{T} brought even more hits than usual"},
             {"default": "{T} hardly hit anyone", "worse": "{T} barely touched anyone"}),
    "depth": ({"default": "{T} leaned on their bottom six"}, {"default": "{T} shortened their bench"}),
}


def team_stat(kind: str, t: str, o: str, x: float | None, usual: float | None, league: float | None, places: dict,
              expected: float | None = None, fp: dict | None = None, season: int = CURRENT_SEASON) -> dict | None:
    """`usual` is the team's own level, `expected` what the matchup predicted (their level against tonight's
    opponent; the same as usual when the opponent does not matter)."""
    if x is None or usual is None or league is None:
        return None
    e = usual if expected is None else expected
    z = z_of(kind, x - e)
    s, own, opp, why = standing(kind, t, o, fp, places, season) if fp else (None, 0.0, 0.0, [])
    cls = classify(e, x, league, sd(kind), s, own, opp)
    if kind in NEUTRAL and cls in ("worse", "better"):
        cls = "further"
    T, O = places[t], places[o]
    up = x > e
    heads = HEADS[kind][0 if up else 1]
    head = heads.get(cls, heads["default"]).format(T=T, O=O)
    vs = f" (about {e:.1f} against {O})" if kind == "pp" and abs(e - usual) >= 0.3 else ""
    # `fig` tells it in full; `short` starts from the number, for when the sentence before has named the team
    if kind == "quality":
        vs = f" (about {100 * e:.1f}% against {O})" if abs(e - usual) >= 0.002 else ""
        rest = f"against their usual {100 * usual:.1f}%{vs} and a league average of {100 * league:.1f}%."
        fig = f"{T}’s shots at 5-on-5 had {a_pct(x)} chance of scoring on the average shot, {rest}"
        short = f"{a_pct(x)} chance of scoring on the average shot at 5-on-5, {rest}"
        cat = "chances"
    elif kind == "pp":
        rest = f"against their usual {usual:.1f}{vs} and a league average of {league:.1f}."
        fig = f"{T}’s power play created {x:.1f} expected goals per 60, {rest}"
        short = f"{x:.1f} expected goals per 60 on the power play, {rest}"
        cat = "special"
    elif kind == "hits":
        xi, ui = 100 * x / league, 100 * usual / league
        rest = f"against their usual {ui:.0f} (100 is league average, adjusted for the arena’s scorer)."
        fig = f"{T}’s hit score in the close stretches of the game was {xi:.0f}, {rest}"
        short = f"a hit score of {xi:.0f} in the close stretches of the game, {rest}"
        cat = "physical"
    else:  # depth
        rest = f"against their usual {usual:.0f}% and a league average of {league:.0f}%."
        fig = f"{T}’s bottom-six forwards played {x:.0f}% of the forwards’ 5-on-5 time, {rest}"
        short = f"the bottom six played {x:.0f}% of the forwards’ 5-on-5 time, {rest}"
        cat = "lineup"
    body = compose(cls, why, fig, short, rarity(kind, x - e), BRIDGE[kind][0 if up else 1], who=T)
    return _item(cat, abs(z), cls, head, body, (kind, t, up))


def _conversion(t: str, o: str, season: int) -> tuple[float, float] | None:
    """(this power play's expected conversion against this kill, league average), from the last five seasons' real
    goal conversion, each team's edge halved toward average."""
    df = history.summaries(season + 1)
    if df.is_empty():
        return None
    last = df.filter(pl.col("year") == df["year"].max())
    lg_pp, lg_pk = float(last["pp"].mean()), float(last["pk"].mean())
    def mean(team, k):
        r = df.filter(pl.col("abbrev") == team).sort("year")[k].to_list()
        if not r:
            return None
        w = REP_WEIGHTS[-len(r):]
        return sum(a * b for a, b in zip(w, r)) / sum(w)
    pp, pk = mean(t, "pp"), mean(o, "pk")
    f_pp = 1 + 0.5 * ((pp / lg_pp if pp else 1) - 1)
    f_pk = 1 + 0.5 * (((1 - pk) / (1 - lg_pk) if pk else 1) - 1)
    return lg_pp * f_pp * f_pk, lg_pp


def pp_goals(t: str, o: str, g: int, n: int, fp: dict, places: dict, season: int = CURRENT_SEASON) -> dict | None:
    """Two or more power-play goals, scored by how rare that many is for this power play against this kill."""
    if g < 2 or not n:
        return None
    conv = _conversion(t, o, season) or (0.2, 0.2)
    lam = n * conv[0]
    p = 1 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(g))
    z = NormalDist().inv_cdf(1 - max(p, 1e-6))
    s, own, opp, why = standing("pp", t, o, fp, places, season)
    cls = classify(conv[0], g / n, conv[1], 0.1, s, own, opp)
    if cls == "unusual":
        cls = "rare"
    head = {"break": "{T}’s power play came alive", "worse": "{T}’s power play feasted on {O}’s kill"}.get(cls, "{T}’s power play did damage")
    fig = f"{g} power-play goals on {n} {'chance' if n == 1 else 'chances'}."
    short = fig
    rare = f"A power play like theirs against a kill like {places[o]}’s scores {g} or more about once every {_friendly(1 / max(p, 1e-3))} games."
    return _item("special", z, cls, head.format(T=places[t], O=places[o]), compose(cls, why, fig, short, rare, "a big night", who=places[t]), ("pp", t, True))


def candidates(rows: dict, a: str, h: str, score: dict, fp: dict, places: dict, glines: list, new_trio: list, replay: dict | None,
               season: int = CURRENT_SEASON) -> list[dict]:
    out = [shot_share(rows, a, h, fp, places, season)]
    for t, o in ((a, h), (h, a)):
        def level(team, k, scale=1.0):
            b = fp.get(team, {}).get("dims", {}).get(k, {}).get("blend", {})
            if not b or b.get("v") is None or not b.get("index"):
                return None, None
            return b["v"] * scale, b["v"] * 100 / b["index"] * scale
        def against(u, lg, k2):
            ov, olg = level(o, k2)
            return u * ov / olg if u is not None and ov and olg else u
        r = rows[t]
        u, lg = level(t, "quality")
        out.append(team_stat("quality", t, o, _quality(r), u, lg, places, against(u, lg, "qualityAllowed"), fp, season))
        u, lg = level(t, "pp")
        out.append(team_stat("pp", t, o, _pp(r), u, lg, places, against(u, lg, "pk"), fp, season))
        u, lg = level(t, "physical")
        out.append(team_stat("hits", t, o, _hits(r), u, lg, places, None, fp, season))
        u, lg = level(t, "depth")
        out.append(team_stat("depth", t, o, _depth(r), u, lg, places, None, fp, season))
        out.append(pp_goals(t, o, score["pp_goals"][t], score["pp_opps"][t], fp, places, season))
    won = {a: score[a] > score[h], h: score[h] > score[a]}
    for gl in glines:
        if not gl["started"] or gl["shots"] < 15:
            continue
        z = z_of("gsax", gl["gsax"])
        t = gl["team"]
        if z > 0:
            head = f"{gl['name']} stole one" if won[t] and z >= 1.5 else f"A big night for {gl['name']}"
            body = f"{gl['name']} allowed {gl['ga']} {'goal' if gl['ga'] == 1 else 'goals'} on shots worth {gl['xga']:.1f} expected goals, {gl['gsax']:.1f} fewer than expected."
        else:
            head = f"A rough night for {gl['name']}"
            body = f"{gl['name']} allowed {gl['ga']} {'goal' if gl['ga'] == 1 else 'goals'} on shots worth {gl['xga']:.1f} expected goals, {-gl['gsax']:.1f} more than expected."
        n = every("gsax", gl["gsax"])
        body += f" Starting goalies had a night this {'good' if z > 0 else 'rough'} about once every {n} starts over the last three seasons."
        out.append(_item("goalie", abs(z), "rare", head, body))
    for t, names, sec in new_trio:
        if sec < 360:
            continue
        out.append(_item("lineup", min(2.5, 1.0 + (sec - 360) / 360 * 0.75), "break", f"{places[t]} tried a different line",
                         f"{names} played {sec // 60}:{sec % 60:02d} together at 5-on-5, a trio that isn’t one of {places[t]}’s usual lines."))
    if replay:
        w, l = (a, h) if won[a] else (h, a)
        p = replay["share"][l]
        if p >= 0.6:
            out.append(_item("result", 1.0 + (p - 0.6) * 7.5, "rare", f"{places[w]} won against the run of play",
                             f"With chances like these, {places[l]} would win about {round(100 * p)} games in 100. {places[w]} won anyway."))
    return [c for c in out if c is not None]


# What each kind of preview call predicted, in the surprises' own terms: (figure, team, went up). The shot share is
# told from the side of the team that beat expectations, so it is matched both ways round.
def call_sigs(claim: dict) -> list[tuple]:
    k, m, chk = claim.get("kind"), claim.get("metric"), claim.get("check") or {}
    t, o = claim.get("team"), claim.get("opp")
    if k == "clash":
        f = {"volume": "shots", "quality": "quality", "pp": "pp"}.get(m)
        return [(f, t, True)] if f else []
    if k in ("edge", "contrast"):
        top, bot = chk.get("team", t), chk.get("opp", o)
        # the team at the better end goes up, the other goes down: a stingy defense or a strong kill means the
        # opponent's figure goes down, which is the same as this team's going up
        f = {"volume": "shots", "suppression": "shots", "quality": "quality", "qualityAllowed": "quality",
             "pp": "pp", "pk": "pp", "physical": "hits", "depth": "depth"}.get(m)
        if not f:
            return []
        return [(f, top, True), (f, bot, False)]
    if k == "physical":
        return [("hits", t, True), ("hits", o, True)]
    return []


def _matches(sig: tuple, call_sig: tuple) -> bool | None:
    """True if the call predicted the same thing the surprise reports, False if the opposite, None if unrelated."""
    f, t, up = sig
    cf, ct, cup = call_sig
    if f != cf:
        return None
    if f == "shots" and ct != t:  # a share: the other team going down is this team going up
        ct, cup = t, not cup
    if ct != t:
        return None
    return up == cup


def against_calls(cands: list[dict], calls: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Square the surprises with the preview's calls. A call that held and went much further than expected is not a
    surprise: it leaves the list and the call gets an "and then some" note instead. A call that went the other way stays
    a surprise but names the call it flipped. `calls` are the snapshot's claims, each with its graded `verdict`."""
    kept, beyond = [], {}
    for c in cands:
        sig = c.get("sig")
        hit = None
        for call in calls if sig else []:
            for cs in call_sigs(call):
                same = _matches(sig, cs)
                if same is True and call.get("verdict") == "held":
                    hit = ("beyond", call)
                elif same is False and call.get("verdict") == "missed" and hit is None:
                    hit = ("flipped", call)
            if hit and hit[0] == "beyond":
                break
        if hit and hit[0] == "beyond":
            if c["score"] >= SMALL and (hit[1]["id"] not in beyond or beyond[hit[1]["id"]]["score"] < c["score"]):
                beyond[hit[1]["id"]] = c
            continue
        if hit:
            c = {**c, "flipped": hit[1]["head"]}
        kept.append(c)
    return kept, {k: {"head": v["head"], "body": v["body"], "score": v["score"]} for k, v in beyond.items()}


def pick(cands: list[dict]) -> list[dict]:
    """At most one per kind, biggest first: up to four surprises, topped up to two with smaller twists."""
    best = {}
    for c in sorted(cands, key=lambda c: -c["score"]):
        best.setdefault(c["kind"], c)
    ranked = sorted(best.values(), key=lambda c: -c["score"])
    big = [c for c in ranked if c["score"] >= BIG][:MAX_SHOWN]
    small = [{**c, "mild": True} for c in ranked if SMALL <= c["score"] < BIG][:max(0, MIN_SHOWN - len(big))]
    shown = [{k: v for k, v in c.items() if k != "sig"} for c in big + small]
    if not shown:
        return [{"kind": "form", "score": 0, "tag": "True to form", "mild": True, "head": "Both teams played to type",
                 "body": "Nothing in this game strayed far from how these two usually play: the shot share, the quality of chances, "
                         "special teams, hitting, ice time and goaltending all landed inside their normal night-to-night range."}]
    return shown
