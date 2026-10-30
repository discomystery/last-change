"""How a graded call reads on the post-game page, beside the preview's own card (Sora, 2026-10-12).

The grading side mirrors the call side: a short title beside the verdict pill, one plain sentence, then the technical
grade under a hairline with a small chart and, for shot calls, what was recorded. `recaps.measure` decides the verdict;
this module only words it and draws the numbers it compared.

The chart ("lane") is the chart guide's gap lane: an open ring where the team usually sits, a filled dot for tonight,
a bar between them, a tick for the league average and a dotted mark for any other reference the rule used. Positions
are worked out here (0 to 100, the better end on the right) so the page only places them.
"""
from pipeline.export import previews

LEVEL = {  # level calls (a strength meeting a soft spot): metric -> verdict -> (title, plain sentence)
    "volume": {"held": (["{Xc} piled up the shots", "{Xc} kept the shots coming", "Shots, as promised"], "At 5-on-5, {X} got to their usual volume of shots, as the call said."),
               "partly": ("More shots than most teams, but not {Xp} usual", "At 5-on-5, {X} outshot an average team’s pace, but not their own usual."),
               "missed": ("{Xc} never got the shots going at 5-on-5", "At 5-on-5, {X} took fewer shots than an average NHL team would have.")},
    "quality": {"held": ("{Xc} got their looks", "{Xp} shots were at least as dangerous as usual."),
                "partly": ("Decent looks, not their best", "{Xp} shots were more dangerous than an average team’s, but not up to their usual."),
                "missed": (["{Yc} took away the best looks", "Fewer good looks than usual for {X}", "{Yc} kept {X} to lesser chances"], "{Xp} shots were less dangerous than an average team’s.")},
    "turnover": {"held": ("{Xc} fed on mistakes", "{X} got their usual share of chances right after winning the puck."),
                 "partly": ("A few turnovers, not many", "{X} got more of their chances off turnovers than an average team, but fewer than they usually do."),
                 "missed": (["{Yc} took care of the puck", "{Yc} kept it clean", "No gifts from {Y}"], "{X} got fewer of their chances off turnovers than an average team.")},
    "pp": {"held": (["The power play delivered", "{Xp} power play did its job", "{Xp} power play got going"], "{Xp} power play created at least its usual chances."),
           "partly": ("A decent power play, not their best", "{Xp} power play beat the league average, but not its own usual."),
           "missed": (["{Yp} kill won that one", "{Yp} kill stood tall", "{Xp} power play went quiet"], "{Xp} power play created less than an average one would have.")},
}
DUEL = {  # lean -> verdict -> (title, plain)
    "offense": {"held": (["{Xc} got theirs", "{Xc} got through anyway"], "{X} beat the league average on {oname}, even against {Y}."),
                "missed": (["{Yc} held the line", "{Yc} won the standoff"], "{Y} kept {X} under the league average on {oname}.")},
    "defense": {"held": ("{Yc} held the line", "{Y} kept {X} under the league average on {oname}, as we leaned."),
                "missed": ("{Xc} broke through", "{X} beat the league average on {oname} anyway.")},
}
CHANGED = {"held": ("Still the new {Xn}", "On {name}, {X} looked like this season’s team, not the old one."),
           "missed": ("Back to the old {Xn}", "On {name}, {X} looked more like the team from last season.")}
EDGE = {"held": "{top} came out clearly ahead on {name}, as the gap between these two suggested.",
        "partly": "{top} came out ahead on {name}, but by less than the usual gap between these two.",
        "missed": "{bot} came out ahead on {name}, against the run of the season."}
# Edge titles name what was won (Sora, 2026-10-14: "Boston won that one instead" didn't say what "that one" was):
# trait -> (held, partly, missed), the team at the better end coming in as {top}, the other as {bot}.
EDGE_TITLES = {
    "volume": ("{topc} won the shot battle", "{topc} won the shot battle, barely", "{botc} won the shot battle instead"),
    "suppression": ("{topc} kept the shots down better", "{topc} allowed fewer shots, barely", "{botc} allowed fewer shots instead"),
    "quality": ("{topc} got the better looks", "{topc} got the better looks, barely", "{botc} got the better looks instead"),
    "qualityAllowed": ("{topc} allowed fewer dangerous shots", "{topc} allowed fewer dangerous shots, barely", "{botc} allowed fewer dangerous shots instead"),
    "point": ("{topc} leaned on point shots", "{topc} leaned on point shots, barely", "{botc} leaned on point shots more instead"),
    "dShots": ("{topp} defensemen did more of the shooting", "{topp} defensemen shot more, but only just", "{botp} defensemen did more of the shooting instead"),
    "inClose": ("{topc} got in closer", "{topc} got in closer, barely", "{botc} got in closer instead"),
    "forecheck": ("{topc} won the forecheck", "{topc} won the forecheck, barely", "{botc} won the forecheck instead"),
    "physical": ("{topc} won the hitting battle", "{topc} outhit them, barely", "{botc} won the hitting battle instead"),
    "depth": ("{topc} spread the minutes deeper", "{topc} spread the minutes deeper, barely", "{botc} spread the minutes deeper instead"),
    "pp": ("{topcp} power play made more chances", "{topcp} power play made more chances, barely", "{botcp} power play made more chances instead"),
    "pk": ("{topcp} kill gave up less", "{topcp} kill gave up less, barely", "{botcp} kill gave up less instead"),
}
PACE = {"above": {"held": ("A track meet, as billed", "The game ran faster than either team usually plays."),
                  "partly": ("Quick, but no track meet", "The game moved faster than an average NHL game, just not as fast as {bar} usually plays."),
                  "missed": ("It never took off", "The game ran slower than an average NHL game.")},
        "below": {"held": ("A grind, as billed", "The game ran slower than either team usually plays."),
                  "partly": ("Slow, but not that slow", "The game was slower than an average NHL game, but not as slow as {bar} usually plays."),
                  "missed": ("Livelier than expected", "The game ran faster than an average NHL game.")}}
PHYSICAL = {"held": ("Bodies flew", "The two teams hit at least as much as they usually do."),
            "partly": ("Physical, not punishing", "There was more hitting than in an average game, but less than these two usually bring."),
            "missed": ("Quieter than billed", "There was less hitting than in an average NHL game.")}
MATCHUP = {"held": ("{coach} got {his} matchup", "{Hc} sent {group} out against {Op} {line} for {v}% of their 5-on-5 time, far more than the {thr}% they’d get with no line matching."),
           "partly": ("{group} saw {Op} {line} some of the time", "{Hc} sent {group} out against {Op} {line} more than they would with no line matching, but not as often as the call needed."),
           "missed": ("{group} barely saw {Op} {line}", "{Hc} sent {group} out against {Op} {line} no more than they would with no line matching.")}
MATCHUP_DREW = " Instead, {coach} sent them out against {drew} ({sec} of {total} at 5-on-5)."
HISTORY = {"held": ("The usual {G}", "{G} played like his usual self, not like his record against {O}. Just a coincidence, as we said."),
           "missed": ("{G} struggled against {O} again", "{Gp} night looked more like his record against {O} than his usual. Three seasons of evidence say it’s still a coincidence.")}
REST = {"held": (["Fresh legs told", "The rested team showed it", "Tired legs showed"], "{R} got a bigger share of the chances than they usually do, against a tired {Tn} team."),
        "missed": ("{Tc} shrugged it off", "{T} held their own despite playing last night.")}
# "We said ...": the prediction in a fan's words, for wherever a graded call is shown in one line (share cards, Track
# record), so the grade beside it can't read as contradicting a headline that was only the call's stakes ("The
# Capitals can't afford to take penalties"). Sora, 2026-10-14. Lowercase start: it follows "We said".
SAID_CLASH = {
    "pp": "{Xp} power play would get its usual chances against {Yp} shaky kill",
    "quality": "{X} would get their usual good looks against a {Yn} team that gives them up",
    "volume": "{X} would fire away at a {Yn} team that gives up shots",
    "turnover": "{X} would feed on {Yp} turnovers",
}
DUEL_SIDES = {"volume": ("shooters", "defense"), "quality": ("shooters", "defense"), "pp": ("power play", "kill")}  # attack, defense
SAID_EDGE = {
    "volume": "{top} would outshoot {bot}", "suppression": "{top} would allow fewer shots than {bot}",
    "quality": "{top} would get better looks than {bot}", "qualityAllowed": "{top} would allow fewer dangerous shots than {bot}",
    "point": "{top} would lean on point shots more than {bot}", "dShots": "{topp} defensemen would do more of the shooting than {botp}",
    "inClose": "{top} would get more of their shots from in close than {bot}", "forecheck": "{top} would forecheck harder than {bot}",
    "physical": "{top} would outhit {bot}", "depth": "{top} would spread their ice time deeper than {bot}",
    "pp": "{topp} power play would make more chances than {botp}", "pk": "{topp} penalty kill would give up less than {botp}",
}
LINE = {"L1": "first line", "L2": "second line", "L3": "third line", "L4": "fourth line"}

# Each trait's scale ends, worse (or lower) on the left; `flip` puts lower figures on the right where lower is better.
ENDS = {**{k: (v[1].capitalize(), v[2].capitalize()) for k, v in previews.EDGE.items()},
        "turnover": ("Patient", "Opportunist"), "pace": ("Slog", "Track meet"), "physical": ("Finesse", "Bruising")}
LOWER_BETTER = {"suppression", "qualityAllowed", "pk"}


def _coach(S, gid: int, team: str) -> str | None:
    """Last name of tonight's head coach for `team`, or None when nothing is saved."""
    try:
        from pipeline.ingest import coaches
        c = coaches.for_game(S.season, gid, team, S.games[gid]["date"])
    except Exception:
        return None
    return c["coach"] if c else None


def said(S, gid: int, claim: dict, places: dict) -> str | None:
    """The prediction behind a call, in plain words, without numbers ("the Hurricanes’ power play would get its usual
    chances against the Capitals’ shaky kill"). None for a kind with no wording."""
    kind, m, chk = claim["kind"], claim.get("metric"), claim.get("check") or {}
    w = _words(claim, places)
    if kind == "clash" and m in SAID_CLASH and "Y" in w:
        return SAID_CLASH[m].format(**w)
    if kind == "duel" and m in DUEL_SIDES and "Y" in w:
        att, dfn = DUEL_SIDES[m]
        lean = claim.get("lean") or ("offense" if chk.get("direction") == "above" else "defense")
        a, d = f"{w['Xp']} {att}", f"{w['Yp']} {dfn}"
        return f"{a} would win the standoff with {d}" if lean == "offense" else f"{d} would win the standoff with {a}"
    if kind in ("edge", "contrast") and chk.get("team") and chk.get("opp"):
        k = chk.get("metric", m)
        tw = previews.team_words(chk["team"], places, "top") | previews.team_words(chk["opp"], places, "bot")
        if k in SAID_EDGE:
            return SAID_EDGE[k].format(**tw)
        name = previews.EDGE.get(k, (k,))[0]
        return f"{tw['top']} would come out ahead of {tw['bot']} on {name}"
    if kind == "changed" and "X" in w:
        name = previews.CHANGED.get(m, (m,))[0]
        return f"{w['X']} would look like this season’s team on {name}, not last season’s"
    if kind == "pace":
        return ("this one would be a track meet, faster than either team usually plays" if chk.get("direction") == "above"
                else "this one would be a grind, slower than either team usually plays")
    if kind == "physical":
        return "bodies would fly, with both teams hitting as much as they usually do"
    if kind == "matchup":
        H = previews.team_words(claim["team"], places, "H")
        O = previews.team_words(claim["opp"], places, "O")
        coach = _coach(S, gid, claim["team"])
        who = coach or H["H"]
        group = previews._and([S.name.get(i, "?").split(" ", 1)[-1] for i in chk.get("unit_ids", [])]) or f"their {chk.get('unit', 'line')}"
        return f"{who} would send {group} out against {O['Op']} {LINE.get(chk.get('opp_line'), 'line')} again"
    if kind == "history":
        G = previews._last(S.name.get(chk.get("goalie_id"), "") or "") or "their goalie"
        O = previews.team_words(claim["opp"], places, "O")
        if chk.get("record", 0) < chk.get("baseline", 0):
            return f"{previews._poss(G)} rough record against {O['O']} was a coincidence, and he’d look like himself"
        return f"{previews._poss(G)} hold on {O['O']} wouldn’t last"
    if kind == "rest" and claim.get("opp"):
        R = previews.team_words(claim["team"], places, "R")
        T = previews.team_words(claim["opp"], places, "T")
        return f"the rested {R['Rn']} would take advantage of {T['Tp']} tired legs"
    return None


def _fmt(t: str, **w) -> str:
    return previews._fmt(t, **w)


def _words(claim: dict, places: dict) -> dict:
    w = {}
    for key, pre in (("team", "X"), ("opp", "Y")):
        if claim.get(key):
            w |= previews.team_words(claim[key], places, pre)
    return w


def _short(m: str, scaled: bool):
    """How a figure reads beside a mark on the chart."""
    if scaled or m in ("physical", "forecheck"):
        return lambda v: f"{v:.0f}"
    if m in ("quality", "qualityAllowed"):
        return lambda v: f"{100 * v:.1f}%"
    if m == "turnover":
        return lambda v: f"{v:.1f}%"
    if m in ("turnover", "dShots", "inClose", "depth", "point"):
        return lambda v: f"{v:.0f}%"
    return lambda v: f"{v:.1f}"


def _signed(v: float, f) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + f(abs(v))


def lane(team: str | None, ends, center: float, avg_label: tuple, usual: tuple, now: tuple, ref: tuple | None = None,
         flip: bool = False, tip: str = "") -> dict:
    """Marks on a short scale centred on `center` (the league average, or even): each mark is (value, label)."""
    vals = [v for v, _ in (usual, now, *(r for r in (ref,) if r))]
    half = max(abs(v - center) for v in vals) * 1.18 or 1.0
    pos = lambda v: round(max(3.0, min(97.0, 50 + (-1 if flip else 1) * (v - center) / half * 50)), 1)
    out = {"team": team, "ends": list(ends) if ends else None, "avg": [50.0, *avg_label] if avg_label else None,
           "usual": [pos(usual[0]), usual[1]], "now": [pos(now[0]), now[1]], "tip": tip}
    if ref:
        out["ref"] = [pos(ref[0]), ref[1]]
    return out


def present(S, gid: int, claim: dict, res: dict, places: dict, fp: dict) -> dict:
    """`_present`, plus `said`: the prediction in plain words, for the "We said" line."""
    out = _present(S, gid, claim, res, places, fp)
    try:
        if (txt := said(S, gid, claim, places)):
            out["said"] = txt
    except (KeyError, TypeError, ValueError):  # an old snapshot missing a field: the line falls back to the headline
        pass
    return out


def _present(S, gid: int, claim: dict, res: dict, places: dict, fp: dict) -> dict:
    """Adds `title`, `plain`, `lane` and `saw` to a graded call (and rewrites `happened`, the technical grade, for
    calls quoted as scores). Calls that can't be judged keep their reason as the plain sentence."""
    v = res.pop("_v", None)
    chk_e = res.pop("_chk", None)
    gname = res.pop("_name", None)
    tb = res.pop("_tb", None)
    bar = res.pop("_bar", None)
    drew = res.pop("_drew", None)
    verdict = res.get("verdict")
    if verdict not in ("held", "partly", "missed") or v is None:
        return res
    kind, chk, m = claim["kind"], claim.get("check") or chk_e or {}, claim["metric"]
    scaled = chk.get("scale") == "score"
    w = _words(claim, places)
    base, league = chk.get("baseline"), chk.get("league")
    g = S.games[gid]
    row = lambda t: S.rows.get((gid, S.team_id.get((gid, t))))
    tot = lambda t: S.totals.get((gid, S.team_id.get((gid, t))), {})
    sc = (lambda x: 100 * x / league) if scaled and league else (lambda x: x)
    f = _short(m, scaled)
    title = plain = None
    out = dict(res)

    if kind in ("clash", "duel", "changed") and m in LEVEL | {"suppression": 0, "qualityAllowed": 0, "pk": 0, "physical": 0}:
        flip = m in LOWER_BETTER
        ends = ENDS.get(m)
        if kind == "clash":
            if not league:
                return res
            title, plain = LEVEL[m][verdict]
            out["lane"] = lane(claim["team"], ends, 100.0 if scaled else league, ("Avg", " 100") if scaled else ("Avg", f" {f(league)}"),
                               (sc(base), f"Usual {f(sc(base))}"), (sc(v), f"Tonight {f(sc(v))}"), flip=flip,
                               tip=f"{places[claim['team']]} tonight (the filled dot) against their usual (the ring). Reaching the ring holds the call; "
                                   "between the league-average tick and the ring is partly; short of the tick, it didn’t happen.")
            if scaled:
                name = previews.SCORES[m].capitalize()
                tv, ub = f(sc(v)), f(sc(base))  # compared as shown: "123, short of their usual 123" read as a bug
                out["happened"] = (f"{name} {tv}, against their usual {ub}." if verdict == "held" else
                                   (f"{name} {tv}, a hair under their usual" if tv == ub else f"{name} {tv}, short of their usual {ub}")
                                   + " but above the league average." if verdict == "partly" else
                                   f"{name} {tv}, a hair under the league average of 100 (their usual is {ub})." if tv == "100" else
                                   f"{name} {tv}, below the league average of 100 (their usual is {ub}).")
                out |= {"usual": f"score {f(sc(base))}", "tonight": f"score {f(sc(v))}", "league": "score 100"}
        elif kind == "duel":
            lean = claim.get("lean") or ("offense" if chk.get("direction") == "above" else "defense")
            oname = previews.DUELS[m][2] if m in previews.DUELS else m
            title, plain = DUEL[lean][verdict]
            w["oname"] = oname
            usual = chk.get("usual", base)
            out["lane"] = lane(claim["team"], ends, 100.0 if scaled else base, ("Avg", " 100") if scaled else ("Avg", f" {f(base)}"),
                               (sc(usual), f"Usual {f(sc(usual))}"), (sc(v), f"Tonight {f(sc(v))}"), flip=flip,
                               tip=f"{places[claim['team']]} tonight (the filled dot) against the league average (the tick). The call needed the dot "
                                   f"{'above' if chk.get('direction') == 'above' else 'below'} the average. The ring is where they usually sit.")
            if scaled:
                out["happened"] = f"{previews.SCORES[m].capitalize()} {f(sc(v))}, against a league average of 100. They came in at {f(sc(usual))}."
                out |= {"usual": f"score {f(sc(usual))}", "tonight": f"score {f(sc(v))}", "league": "score 100"}
        else:
            name = previews.CHANGED.get(m, (m,))[0]
            title, plain = CHANGED[verdict]
            w["name"] = name
            season = chk.get("season")
            idx = chk.get("index_traits") and league
            s2 = (lambda x: 100 * x / league) if (scaled or idx) and league else (lambda x: x)
            f2 = _short(m, scaled or bool(idx))
            out["lane"] = lane(claim["team"], ends, 100.0 if league and (scaled or idx) else (league or base),
                               ("Avg", " 100") if (scaled or idx) else ("Avg", f" {f2(league)}") if league else None,
                               (s2(base), f"Blended {f2(s2(base))}"), (s2(v), f"Tonight {f2(s2(v))}"),
                               ref=(s2(season), f"This season {f2(s2(season))}") if season is not None else None, flip=flip,
                               tip=f"{places[claim['team']]} tonight (the filled dot) against their blended level (the ring) and this season alone "
                                   "(the dotted mark). The call needed the dot past the ring, on this season’s side.")
            if scaled:
                out["happened"] = (f"{previews.SCORES[m].capitalize()} {f2(s2(v))}, against {f2(s2(base))} blended over this season and last "
                                   f"and {f2(s2(season))} this season alone.")
                out |= {"usual": f"score {f2(s2(base))}", "tonight": f"score {f2(s2(v))}"}
    elif kind in ("edge", "contrast"):
        top, bot = chk["team"], chk["opp"]
        tw, bw = previews.team_words(top, places, "top"), previews.team_words(bot, places, "bot")
        w = {**tw, **bw}
        k = chk.get("metric", m)
        name = previews.EDGE.get(k, (k,))[0]
        w["name"] = ("the " + name) if k in ("pp", "pk") else name
        w |= {"topcp": previews._poss(w["topc"]), "botcp": previews._poss(w["botc"])}
        title = EDGE_TITLES.get(k, ("{topc} came out ahead", "{topc} came out ahead, barely", "{botc} came out ahead instead"))[
            ("held", "partly", "missed").index(verdict)]
        plain = EDGE[verdict]
        gap = chk["gap"]
        idx = k in previews.INDEX_TRAITS
        unit = (lambda x: 100 * x / league) if scaled and league else (lambda x: x)
        fe = _short(k, scaled or idx)
        sv = lambda x: _signed(unit(x), fe)
        nick = previews.names(places)
        out["lane"] = lane(top, (f"{nick.get(bot, places[bot])} ahead", f"{nick.get(top, places[top])} ahead"), 0.0, ("Even", ""),
                           (unit(gap), f"Usual {sv(gap)}"), (unit(v), f"Tonight {sv(v)}"), ref=(unit(gap / 2), f"Needed {sv(gap / 2)}"),
                           tip=f"How far {places[top]} {'were' if places[top].startswith('NY ') else 'was'} ahead tonight (the filled dot) against their usual gap (the ring). Holding needed half "
                               "the usual gap (the dotted mark); ahead by less is partly; behind, it didn’t happen.")
        if scaled:
            name = previews.SCORES[k]
            d = round(unit(v))
            need = round(unit(gap / 2))
            better, worse = ("lower", "higher") if k == "suppression" else ("higher", "lower")
            out["happened"] = (f"{places[top]}’s {name} was {abs(d)} {'point' if abs(d) == 1 else 'points'} {better if d > 0 else worse} than {places[bot]}’s, "
                               + (f"more than the {need} the call needed." if verdict == "held" else f"short of the {need} the call needed." if verdict == "partly"
                                  else f"when the call needed it {better}."))
            bt, bb = chk["baseline"][top], chk["baseline"][bot]
            out |= {"usual": f"{top} {unit(bt):.0f} · {bot} {unit(bb):.0f}", "tonight": f"{top} {unit(tb[0]):.0f} · {bot} {unit(tb[1]):.0f}" if tb else res.get("tonight")}
            r_t, r_b = row(top), row(bot)
            if r_t and r_b:
                if k == "volume":
                    a_t, a_b = int(r_t["cf5"]), int(r_b["cf5"])
                    out["saw"] = f"{places[top]} {a_t} shot attempts at 5-on-5, {places[bot]} {a_b}."
                    flipped = (a_t > a_b) != (v > 0) and a_t != a_b
                else:
                    a_t, a_b = int(r_t["ca5"]), int(r_b["ca5"])
                    out["saw"] = f"{places[top]} allowed {a_t} shot attempts at 5-on-5, {places[bot]} {a_b}."
                    flipped = (a_t < a_b) != (v > 0) and a_t != a_b
                if flipped:
                    out["saw"] += f" Teams that are behind shoot more, so with the score allowed for, {places[top] if v > 0 else places[bot]} came out ahead."
    elif kind == "pace":
        bar_team = chk.get("bar_team")
        if not bar_team:  # saved before the call named the team: the one whose usual pace is the bar
            vals = {t: (fp.get(t, {}).get("dims", {}).get("pace", {}).get("blend") or {}).get("v") for t in (g["away"], g["home"])}
            bar_team = min(vals, key=lambda t: abs((vals[t] or 0) - base))
        title, plain = PACE[chk["direction"]][verdict]
        w["bar"] = places.get(bar_team, bar_team)
        out["lane"] = lane(bar_team, ENDS["pace"], 100.0 if scaled else league, ("Avg", " 100") if scaled else ("Avg", f" {f(league)}"),
                           (sc(base), f"Usual {f(sc(base))}"), (sc(v), f"Tonight {f(sc(v))}"),
                           tip=f"The game’s pace tonight (the filled dot) against {places.get(bar_team, bar_team)}’s usual (the ring). Past the ring holds the "
                               "call; between the league-average tick and the ring is partly; otherwise it didn’t happen.")
        if scaled:
            up = chk["direction"] == "above"
            tv, ub, B = f(sc(v)), f(sc(base)), places.get(bar_team, bar_team)
            if verdict == "held":
                rel = f"against {B}’s usual {ub}"
            elif tv == ub:  # compared as shown, never "100, below the league average of 100"
                rel = f"a hair {'under' if up else 'over'} {B}’s usual {ub}"
            else:
                rel = f"{'short of' if up else 'above'} {B}’s usual {ub}"
            out["happened"] = (f"Pace score {tv}, {rel}"
                               + ("." if verdict == "held" else f" but {'above' if up else 'below'} the league average." if verdict == "partly"
                                  else ", and right at the league average of 100." if tv == "100"
                                  else f", and {'below' if up else 'above'} the league average of 100."))
            out |= {"usual": f"score {f(sc(base))}", "tonight": f"score {f(sc(v))}", "league": "score 100"}
        rows = [row(g["away"]), row(g["home"])]
        if all(rows):
            shots = int(rows[0]["ff5"] + rows[0]["fa5"])
            expect = round(base * rows[0]["sec5"] / 3600)
            sog = int(tot(g["away"]).get("sf", 0) + tot(g["home"]).get("sf", 0))
            out["saw"] = (f"{shots} shots between them at 5-on-5, where about {expect} was expected · {sog} shots on goal · "
                          f"{_clock(rows[0]['sec5'])} of 5-on-5")
    elif kind == "physical":
        title, plain = PHYSICAL[verdict]
        out["lane"] = lane(None, ENDS["physical"], 100.0, ("Avg", " 100"), (base, f"Usual {base:.0f}"), (v, f"Tonight {v:.0f}"),
                           tip="The two teams’ combined hit score tonight (the filled dot) against their usual (the ring). Reaching the ring holds the "
                               "call; between the league-average tick and the ring is partly; short of the tick, it didn’t happen.")
    elif kind == "matchup":
        o = claim["opp"]
        w |= {**previews.team_words(claim["team"], places, "H"), **previews.team_words(o, places, "O")}
        w["line"] = LINE.get(chk.get("opp_line"), "line")
        coach = _coach(S, gid, claim["team"])
        w |= {"coach": previews._last(coach) if coach else w["Hc"], "his": "his" if coach else "their"}
        w["group"] = previews._and([S.name.get(i, "?").split(" ", 1)[-1] for i in chk.get("unit_ids", [])]) or f"their {chk.get('unit', 'line')}"
        title, plain = MATCHUP[verdict]
        thr = chk["threshold"]
        w |= {"v": v, "thr": thr}
        if drew and verdict == "missed":
            plain += MATCHUP_DREW.format(coach=w["coach"] if coach else "they", drew=drew[0], sec=_clock(drew[1]), total=_clock(drew[2]))
        bar = bar if bar is not None else round((base + thr) / 2)
        out["lane"] = lane(claim["team"], None, float(thr), ("No matching", f" {thr}%"), (bar, f"Call {bar}%"), (v, f"Tonight {v}%"), ref=(base, f"Home {base}%"),
                           tip="Share of this group’s 5-on-5 time against that line tonight (the filled dot). The ring is the call: halfway from what it "
                               "would get with no line matching (the tick) to its home habit (the dotted mark), since part of any habit is luck. Reaching "
                               "the ring holds the call; past the tick is partly; otherwise it didn’t happen.")
    elif kind == "history":
        G = previews._last(gname or "")
        w = {"G": G, "Gp": previews._poss(G), **previews.team_words(claim["opp"], places, "O")}
        title, plain = HISTORY[verdict]
        rec = chk["record"]
        if verdict == "missed" and rec > base:  # his good record held: he had their number again
            title = "{G} had {Op} number again"
        fs = lambda x: f"{x:.3f}".lstrip("0")
        out["lane"] = lane(claim["team"], None, base, None, (base, f"Usual {fs(base)}"), (v, f"Tonight {fs(v)}"), ref=(rec, f"Vs {places.get(claim['opp'], claim['opp'])} {fs(rec)}"),
                           tip="His save percentage tonight (the filled dot), his usual (the ring) and his record against this team (the dotted mark). "
                               "The call held if the dot landed closer to the ring.")
    elif kind == "rest":
        w |= previews.team_words(claim["opp"], places, "T") | previews.team_words(claim["team"], places, "R")
        title, plain = REST[verdict]
        out["lane"] = lane(claim["team"], None, 0.5, ("Even", " 50%"), (base, f"Usual {100 * base:.0f}%"), (v, f"Tonight {100 * v:.0f}%"),
                           tip="The rested team’s share of the 5-on-5 chances tonight (the filled dot) against their usual share (the ring). The call "
                               "needed the dot at or past the ring.")
    if kind in ("clash", "duel", "changed") and m in ("volume", "suppression"):
        t = claim["team"]
        r = row(t)
        if r:
            sec = r["sec5"]
            if m == "volume":
                n, sog = int(r["cf5"]), int(tot(t).get("sf", 0))
                lead = f"{n} shot attempts at 5-on-5"
                sog_txt = f"{sog} shots on goal"
            else:
                o = claim["opp"] if claim.get("opp") != t else None
                n = int(r["ca5"])
                lead = f"{n} shot attempts against at 5-on-5"
                sog_txt = f"{int(tot(o).get('sf', 0))} shots on goal against" if o else ""
            if kind == "clash":
                exp = f", where about {round(base * sec / 3600)} was expected"
            elif kind == "duel":
                exp = f", where an average team would have had about {round(base * sec / 3600)}"
            else:
                exp = f", where their blended level would mean about {round(base * sec / 3600)}"
            out["saw"] = lead + exp + (f" · {sog_txt}" if sog_txt else "")
    if title and m in ("pp", "pk") and kind in ("clash", "duel", "edge", "contrast"):
        title, plain, verdict = _pp_goals(S, gid, claim, chk, verdict, title, plain, w, places)
        out["verdict"] = verdict
    if verdict == "missed" and title and isinstance(base, (int, float)) and base and v is not None and abs(v - base) <= NEAR_MISS * abs(base) and kind in ("clash", "duel", "changed", "pace", "physical", "rest"):
        title = "Close, but not quite"  # a hair off the bar is told as a near miss, not a flat no (Sora's review, 2026-10-17)
        plain = (plain if isinstance(plain, str) else _fmt(plain, **w)) + " It was close, though: a hair off the mark."
    if title:
        if isinstance(title, list):  # a pool, picked the same way every run for this call, varied across games
            title = previews._pick(title, claim.get("id"), verdict)
        out["title"] = _fmt(title, **w).rstrip(".")
        out["plain"] = _fmt(plain, **w)
    return out


NEAR_MISS = 0.03  # within 3% of the bar: "close, but not quite"


def _record(team: str, g: int, n: int, places: dict) -> str:
    return f"{places.get(team, team)} {g} for {n}"


def _pp_goals(S, gid: int, claim: dict, chk: dict, verdict: str, title, plain: str, w: dict, places: dict):
    """Special-teams calls are graded on chances, the fair test, but the goals are what a fan saw (eye test,
    2026-10-13: "shut down" a power play that scored twice, "got theirs" after 0-for-3). Adds the goals to the plain
    sentence, and when they point the other way from the grade, says so and swaps a title that would deny them. Since
    2026-10-17 (Sora's review of 2025-26: "shut down" a power play that went 2 for 3) the grade itself is capped at
    "partly" when the goals contradict it, both ways. Returns (title, plain, verdict)."""
    kind, m = claim["kind"], claim["metric"]
    if kind in ("edge", "contrast"):
        top, bot = chk["team"], chk["opp"]
        if m == "pp":  # whose power play did better tonight
            (gt, nt), (gb, nb) = S.pp_record(gid, top), S.pp_record(gid, bot)
            facts = f"On the scoreboard: {_record(top, gt, nt, places)}, {_record(bot, gb, nb, places)} on the power play."
            better_top, better_bot = gt > gb, gb > gt
        else:  # whose kill did better: fewer goals allowed
            (gt, nt), (gb, nb) = S.pp_record(gid, bot), S.pp_record(gid, top)  # top's kill faced bot's power play
            facts = (f"On the scoreboard, {places.get(top, top)} killed {nt - gt} of {nt} penalties and {places.get(bot, bot)} {nb - gb} of {nb}."
                     if nt or nb else "")
            better_top, better_bot = gt < gb, gb < gt
        favoured_top = verdict in ("held", "partly")
        if (favoured_top and better_bot) or (not favoured_top and better_top):
            unit = "power play" if m == "pp" else "kill"
            win, lose = ("{topcp}", "{botcp}") if favoured_top else ("{botcp}", "{topcp}")
            title = f"{win} {unit} won the chances, {lose} won the goals"
            what = "power play" if m == "pp" else "penalty kill"
            won, lost = ("{top}", "{bot}") if favoured_top else ("{bot}", "{top}")
            plain = (f"{won} made the better chances on the {what}, but {lost} came out ahead where it counts." if m == "pp" else
                     f"{won} gave up less on the {what}, but {lost} allowed fewer goals.")
            return title, _fmt(plain, **w) + " " + facts, "partly"
        elif favoured_top and not better_top and (nt or nb):  # ahead on chances, level on goals
            title = "{topc} had the better chances" if m == "pp" else "{topc} gave up less"
            plain = (f"{{top}} made the better chances on the power play, as the gap between these two suggested, but the goals came out even."
                     if m == "pp" else f"{{top}} gave up less on the penalty kill, as the gap between these two suggested, but the goals came out even.")
            return title, _fmt(plain, **w) + " " + facts, verdict
        return title, (plain + " " + facts).strip(), verdict
    # clash, duel: one power play against one kill; does the verdict say the power play did well?
    pp_team = claim["team"]
    g, n = S.pp_record(gid, pp_team)
    lean = claim.get("lean") or ("offense" if chk.get("direction") == "above" else "defense")
    pp_did_well = verdict in ("held", "partly") if kind == "clash" or lean == "offense" else verdict == "missed"
    T = places.get(pp_team, pp_team)
    if pp_did_well and g == 0 and n:
        title = "Some chances, no goal" if verdict == "partly" else "The chances came, the goals didn’t"
        plain += f" They didn’t score on it, though: 0 for {n}."
        if verdict == "held":
            verdict = "partly"
    elif not pp_did_well and g:
        times = {1: "once", 2: "twice"}.get(g, f"{g} times")
        title = f"Not much of a power play, but it scored {times}"
        plain += f" {T} still went {g} for {n} on the power play."
        if g >= 2 or (n and g / n >= 0.5):  # the goals say the power play did fine
            verdict = "partly"
    elif n:
        plain += f" {T} went {g} for {n} on the power play."
    return title, plain, verdict


def _clock(sec: float) -> str:
    s = round(sec)
    return f"{s // 60}:{s % 60:02d}"
