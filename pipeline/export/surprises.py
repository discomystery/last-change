"""What the preview didn't see coming: the parts of a finished game furthest from how these two teams usually play.

Every candidate gets a score: how many night-to-night spreads (standard deviations, pooled within team-seasons over
2023-24 to 2025-26) tonight's figure sat from what the teams' usual play predicted. Event-type candidates (a goalie's
night, a new line, a result against the run of play) are put on the same footing. The page then shows at most one per
kind of thing, biggest first: up to four that clear a clear bar, topped up to two with smaller twists, and a plain
"true to form" note when nothing strayed at all.

Each one says why it was unusual:
  broke the pattern   tonight landed on the other side of even (or the league average) from the usual
  further than usual  the usual direction, much further
  closer than usual   the usual direction, but much nearer even
  out of character    a team that is usually about average was far from it
Shot counts are the most common way a game strays, so they need a bigger departure to make the list.
"""
import math
from functools import lru_cache
from statistics import NormalDist

import numpy as np
import polars as pl

from pipeline.config import FULL_SEASONS
from pipeline.export.previews import a_pct
from pipeline.metrics import adjust, goalies, team_style

BIG, SMALL = 1.75, 1.0  # scores for a surprise and for a smaller twist
SHOT_WEIGHT = 0.75  # shot share has to stray further (2.3 spreads) to count as a surprise
MAX_SHOWN, MIN_SHOWN = 4, 2
TAGS = {"break": "Broke the pattern", "further": "Further than usual", "muted": "Closer than usual",
        "unusual": "Out of character", "rare": "Out of the ordinary"}


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


def classify(usual: float, x: float, mid: float, sd: float) -> str:
    side_u = 0 if abs(usual - mid) < 0.25 * sd else (1 if usual > mid else -1)
    side_x = 1 if x > mid else -1
    if side_u == 0:
        return "unusual"
    if side_x != side_u and abs(x - mid) >= 0.25 * sd:
        return "break"
    return "further" if abs(x - mid) > abs(usual - mid) else "muted"


def _item(kind: str, score: float, cls: str, head: str, body: str, sig: tuple | None = None) -> dict:
    out = {"kind": kind, "score": round(score, 2), "tag": TAGS[cls], "head": head, "body": body}
    if sig:
        out["sig"] = sig  # (figure, team, went up): lets a surprise be matched against the preview's calls
    return out


def shot_share(rows: dict, a: str, h: str, fp: dict, places: dict) -> dict | None:
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
    cls = classify(e[t], x[t], 50, sd("share"))
    T, O = places[t], places[o]
    head = {"break": f"{T} flipped the shot count", "further": f"{T} buried {O} in shots",
            "muted": f"{T} held their own on shots", "unusual": f"{T} dominated the shot count"}[cls]
    expect = (f"From how these two usually play, {T} would expect about {e[t]:.0f}%." if cls != "unusual"
              else "These two usually split the shots about evenly.")
    body = f"{T} took {x[t]:.0f}% of the 5-on-5 shot attempts (adjusted for score and venue). {expect} {rarity('share', dev)}"
    return _item("shots", z * SHOT_WEIGHT, cls, head, body, ("shots", t, True))


def team_stat(kind: str, t: str, o: str, x: float | None, usual: float | None, league: float | None, places: dict) -> dict | None:
    if x is None or usual is None or league is None:
        return None
    z = z_of(kind, x - usual)
    cls = classify(usual, x, league, sd(kind))
    T, O = places[t], places[o]
    up = x > usual
    if kind == "quality":
        head = f"{T} got the good looks" if up else f"{T} couldn’t get good looks"
        body = (f"{T}’s shots at 5-on-5 were {'more' if up else 'less'} dangerous than usual: {a_pct(x)} chance of scoring on the average shot, "
                f"against their usual {100 * usual:.1f}% (league average {100 * league:.1f}%).")
        cat = "chances"
    elif kind == "pp":
        head = f"{T}’s power play came alive" if up else f"{O} shut down {T}’s power play"
        body = f"{T}’s power play created {x:.1f} expected goals per 60, against their usual {usual:.1f} and a league average of {league:.1f}."
        cat = "special"
    elif kind == "hits":
        xi, ui = 100 * x / league, 100 * usual / league
        head = f"{T} brought the hits" if up else f"{T} hardly hit anyone"
        body = f"{T}’s hit score in the close stretches of the game was {xi:.0f}, against their usual {ui:.0f} (100 is league average, adjusted for the arena’s scorer)."
        cat = "physical"
    else:  # depth
        head = f"{T} leaned on their bottom six" if up else f"{T} shortened their bench"
        body = f"{T}’s bottom-six forwards played {x:.0f}% of the forwards’ 5-on-5 time, against their usual {usual:.0f}% and a league average of {league:.0f}%."
        cat = "lineup"
    return _item(cat, abs(z), cls, head, f"{body} {rarity(kind, x - usual)}", (kind, t, up))


def candidates(rows: dict, a: str, h: str, score: dict, fp: dict, places: dict, glines: list, new_trio: list, replay: dict | None) -> list[dict]:
    out = [shot_share(rows, a, h, fp, places)]
    for t, o in ((a, h), (h, a)):
        d = fp.get(t, {}).get("dims", {})
        def usual(k, scale=1.0):
            b = d.get(k, {}).get("blend", {})
            if not b or b.get("v") is None or not b.get("index"):
                return None, None
            return b["v"] * scale, b["v"] * 100 / b["index"] * scale
        r = rows[t]
        u, lg = usual("quality")
        out.append(team_stat("quality", t, o, _quality(r), u, lg, places))
        u, lg = usual("pp")
        out.append(team_stat("pp", t, o, _pp(r), u, lg, places))
        u, lg = usual("physical")
        out.append(team_stat("hits", t, o, _hits(r), u, lg, places))
        u, lg = usual("depth")
        out.append(team_stat("depth", t, o, _depth(r), u, lg, places))
        g = score["pp_goals"][t]
        if g >= 2:
            out.append(_item("special", 1.5 + 0.5 * (g - 2), "rare", f"{places[t]}’s power play did damage",
                             f"{g} power-play goals on {score['pp_opps'][t]} chances.", ("pp", t, True)))
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
                             f"Played out 100 times with these chances, {places[l]} wins {round(100 * p)}. {places[w]} won anyway."))
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
