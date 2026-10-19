"""The post-game story's first paragraph, told in the order the game happened.

Before this (2026-10-17, Sora's review of 2025-26 against NHL.com and AP), the paragraph was built by KIND of sentence
(the opener, "added two goals", the hat trick, the shorthanded goals, the comeback, the winner...), so a game read out
of order, a goal could be told twice (once as a shorthanded goal and once as the winner) and a go-ahead goal could
vanish inside "added two goals". Here every goal, goal taken off, review, stopped penalty shot, goalie change and
exit is one event on ONE timeline, and the paragraph walks it from the opening goal to the final horn.

What a sentence says about a goal is its BEAT: what the goal did to the score (opened it, tied it, put a team ahead,
stretched the lead, cut it, sealed it into an empty net, won it in overtime). Firsts and milestones ride on the goal
they came on ("scored his first NHL goal to tie it 2-2"); a hat trick or a big night is told first, as a fan would
lead with it, and the chronology then uses the player's surname ("Marchenko struck again", "completed his hat trick").
Two or three goals by one team within five minutes of each other are one sentence ("Hughes and Hischier scored 1:12
apart to turn a 2-1 deficit into a 3-2 lead"). Times are relative when the last goal told was close ("48 seconds later")
and clock-based otherwise; the last five minutes read "with 2:10 left".

Never why a player left, never a reason for a goalie change: the data can't say (the rules in stories.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pipeline.export import grammar
from pipeline.export.grammar import and_list as _and, cap as _cap, num

PERIODS = {1: "first", 2: "second", 3: "third"}
RUN_GAP = 300  # seconds between two goals by one team that read as one sentence
RELATIVE = 300  # a goal this soon after the last one told is "x:xx later"
LATE = 300  # the last five minutes of the third read "with x:xx left"
HOW = {"pp": " on the power play", "sh": " shorthanded", "ps": " on a penalty shot", "dp": " on a delayed penalty"}
PERIOD_END = 30  # a goal this close to the end of the first or second is told by the clock ("with 12 seconds left in the first")
SEASON_MARKS = (20, 30, 40, 50, 60, 70)  # a scorer's nth goal of the season worth a clause


clock = grammar.seconds_left  # "48 seconds", "2:13"
span = grammar.span  # "just 17 seconds", "less than two minutes" (Sora's review, 2026-10-19: times in words)
left_ = grammar.time_left  # "just 4 seconds", "just under a minute", "two and a half minutes"
th = grammar.ordinal  # "1,000th", "302nd", "21st"


def ordinal(n: int) -> str:
    return grammar.ordinal_word(n)


def nth_word(n: int) -> str:
    """"eighth" for small counts ("in his eighth NHL game"), "23rd" beyond."""
    return grammar.ordinal_word(n)


def at(g: dict) -> int:
    return (g["period"] - 1) * 1200 + g["sec"]


def when(g: dict) -> str:
    """"just 23 seconds into the game", "early in the second", "with 12 seconds left in the first", "with two and a half
    minutes left" (the last five minutes of the third), "in overtime"."""
    p, s = g["period"], g["sec"]
    if p > 3:
        return f"{span(s)} into overtime" if s < 180 else "in overtime"
    if p == 3 and s >= 1200 - LATE:
        return f"with {left_(1200 - s)} left"
    if p <= 2 and s >= 1200 - PERIOD_END:  # "Kent Johnson made it 2-0 with 12 seconds left in the first" (Sora)
        return f"with {left_(1200 - s)} left in the {PERIODS[p]}"
    if s < 60:  # a goal in the first minute gets told as one (Sora: give quick starts some panache)
        return f"just {s} seconds into the game" if p == 1 else f"just {s} seconds into the {PERIODS[p]}"
    if p == 1 and s < 120:
        return f"{span(s)} in"
    part = "early in" if s < 400 else "midway through" if s < 800 else "late in"
    return f"{part} the {PERIODS[p]}"


@dataclass
class Game:
    goals: list[dict]
    winner: str
    loser: str
    home: str
    away: str
    end: str
    W: object  # stories.Words
    name: Callable[[int], str]  # full name once, surname after
    last: Callable[[int], str]
    said: set
    N: dict
    pk: Callable  # pk(pool, *key) -> a stable pick from a pool
    attach: dict = field(default_factory=dict)  # goal index -> {"pre": ..., "tail": ...}
    wiped: list = field(default_factory=list)  # stories._overturned rows
    reviews: list = field(default_factory=list)  # storylines.reviews rows
    ps_stopped: list = field(default_factory=list)
    changes: list = field(default_factory=list)  # (team, first goalie, relief goalie, pulled) from recap_story
    exits: list = field(default_factory=list)  # recaps' `left` rows
    so: tuple = (None, "", [])  # shootout decider, how to tell it, every scorer for the winner
    rally: dict | None = None
    hat_ids: set = field(default_factory=set)
    fights: list = field(default_factory=list)  # (game second, a function giving the sentence), from penalties.fight_events
    relief: dict = field(default_factory=dict)  # goal index -> how to say a goal taken off didn't cost them
    relieved: set = field(default_factory=set)  # teams whose goal taken off the chronology already answered
    so_relief: bool = False  # the winners' goal taken off is answered on the shootout line
    cuts: int = 0  # goals told so far that cut a lead, to vary how the next one is said
    used: dict = field(default_factory=dict)  # pool -> the verb last picked from it
    broke: bool = False  # something other than a goal was told since the last goal, so "x later" would mislead
    hat3: set = field(default_factory=set)  # indexes of goals that completed a hat trick
    naturals: set = field(default_factory=set)  # scorers of a natural hat trick (three straight goals of the game)

    def side(self, g: dict) -> str:
        return self.home if g["home"] else self.away

    def score(self, g: dict, t: str) -> tuple[int, int]:
        """(mine, theirs) after the goal, from team t's side."""
        return (g["hs"], g["as"]) if t == self.home else (g["as"], g["hs"])


def paragraph(gm: Game) -> list[str]:
    """The chronology sentences, in order."""
    goals = gm.goals
    W = gm.W
    out: list[tuple[str, dict]] = []  # (sentence, what it tells: kind, team, words) for the joining pass
    ws, ls = (max(goals[-1]["hs"], goals[-1]["as"]), min(goals[-1]["hs"], goals[-1]["as"])) if goals else (0, 0)
    margin = ws - ls
    # the goal that put the winner ahead for good (after the last tie), and the official winner
    level = [i for i, g in enumerate(goals) if g["hs"] == g["as"]]
    ahead_i = level[-1] + 1 if level and level[-1] + 1 < len(goals) else (0 if goals else None)
    if ahead_i is not None and gm.side(goals[ahead_i]) != gm.winner:
        ahead_i = None
    gw_i = next((i for i, g in enumerate(goals) if gm.side(g) == gm.winner and gm.score(g, gm.winner)[0] == ls + 1), None)
    down_most = 0  # how far the winner trailed
    for g in goals:
        m, t = gm.score(g, gm.winner)
        down_most = max(down_most, t - m)
    # A goal taken off is answered in its own sentence ("..., but it didn’t cost them in the end"): "No harm done" two
    # or three sentences later read as if it belonged to the goal beside it (Sora's review, round 2, 2026-10-19).
    gm.relief, gm.so_relief = {}, False
    seen: dict[int, int] = {}
    for i, g in enumerate(goals):
        seen[g["p1"]] = seen.get(g["p1"], 0) + 1
        if seen[g["p1"]] == 3:
            gm.hat3.add(i)
    gm.naturals = {goals[i]["p1"] for i in range(len(goals) - 2) if goals[i]["p1"] == goals[i + 1]["p1"] == goals[i + 2]["p1"]}
    # events on one clock
    events: list[tuple[int, int, str, object]] = []  # (at, order, kind, payload)
    for i, g in enumerate(goals):
        events.append((at(g), 1, "goal", i))
    for x in gm.wiped:
        events.append((at(x), 0, "wiped", x))
    for x in gm.reviews:
        if x["kind"] == "no_goal" and x.get("shooter_home") is not None:
            events.append((at(x), 0, "no_goal", x))
    for x in gm.ps_stopped:
        events.append((at(x), 0, "ps", x))
    for c in gm.changes:
        sec = ((c[2].get("came_in") or {}).get("sec"))
        if sec is not None:
            events.append((sec, 2, "change", c))
    for x in gm.exits:
        events.append((x["sec"], 3, "exit", x))
    for sec, say in gm.fights:  # every fight, in its place among the goals (Sora's review, 2026-10-19)
        events.append((sec, 4, "fight", say))
    events.sort(key=lambda e: (e[0], e[1]))
    stood = {(x["period"], x["sec"]): x for x in gm.reviews if x["kind"] == "stood"}
    scored_before: dict[int, int] = {}  # scorer -> goals already told
    prev: dict | None = None  # the last goal told
    prev_team: str | None = None  # the team of the last sentence's goal (None after another kind of event)
    got_back: int | None = None  # the opening goal, when the same team had the first one taken off
    summ, busy = _flurries(gm)
    taken: set[int] = set()  # goals told inside a period summary
    period_now = 0
    k = 0
    while k < len(events):
        e_at, _, kind, x = events[k]
        p_now = min(e_at // 1200 + 1, 4) if kind != "goal" else goals[x]["period"]
        if p_now != period_now:
            period_now = p_now
            if p_now in busy and not (out and out[-1][1]["kind"] == "wiped"):  # "It was a busy second period." (Sora's own line for CAR at CHI)
                n_ = busy[p_now]
                out.append((gm.pk([f"It was a busy {PERIODS[p_now]} period.", f"The {PERIODS[p_now]} period was a wild one."] if n_ == 4 else
                                  [f"The {PERIODS[p_now]} period brought {num(n_)} goals.", f"There were {num(n_)} goals in the {PERIODS[p_now]} period alone."],
                                  "busy", p_now), {"kind": "other"}))
        if kind == "goal" and x in taken:
            k += 1
            continue
        if kind == "goal" and goals[x]["period"] in summ and gm.side(goals[x]) == summ[goals[x]["period"]][0]:
            mine = summ[goals[x]["period"]][1]
            out.append((_summary(gm, mine, scored_before), {"kind": "summary", "team": gm.side(goals[x])}))
            for i in mine:
                scored_before[goals[i]["p1"]] = scored_before.get(goals[i]["p1"], 0) + 1
                taken.add(i)
            prev, prev_team = goals[mine[-1]], gm.side(goals[mine[-1]])
            gm.broke = True  # what follows is told by the clock, not "x later"
            k += 1
            continue
        if kind != "goal":
            prev_team = None
            if kind == "wiped":
                t = gm.home if x["home"] else gm.away
                if prev is None and got_back is None and goals and gm.side(goals[0]) == t and not gm.attach.get(0) and x["period"] <= 3:
                    # "The first goal of the game would have been Montréal’s, but it was taken off for offside 1:19 in.
                    # They got it back early in the second, when Nick Suzuki officially opened the scoring" (Sora)
                    out.append((f"The first goal of the game would have been {grammar.possessive(W.place(t))}, but it was taken off {x['why']} {when(x)}.",
                                {"kind": "wiped"}))
                    got_back = 0
                else:
                    what = "what would have been the winner" if x["period"] > 3 else \
                        "another goal" if any((y["home"] == x["home"]) and at(y) < at(x) for y in gm.wiped) else "a goal"
                    cost = ""
                    if t == gm.winner and not any((y["home"] == x["home"]) and at(y) > at(x) for y in gm.wiped):
                        cost = gm.pk([", but it didn’t cost them in the end", ", though it didn’t end up costing them"], "nocost", at(x))
                    out.append((f"{_cap(W.place(t))} had {what} taken off {x['why']} {when(x)}{cost}.", {"kind": "wiped"}))
            elif kind == "no_goal":
                t = gm.home if x["shooter_home"] else gm.away
                out.append((f"The league looked at a {W.nick.get(t, t)} chance {when(x)} on video and ruled no goal.", {"kind": "other"}))
            elif kind == "ps":
                if x["type"] == "shot-on-goal" and x.get("goalie"):
                    out.append((f"{gm.name(x['goalie'])} stopped {gm.name(x['p1'])} on a penalty shot {when(x)}.", {"kind": "other"}))
                else:
                    out.append((f"{gm.name(x['p1'])} missed the net on a penalty shot {when(x)}.", {"kind": "other"}))
            elif kind == "change":
                out.append((_change(gm, x), {"kind": "other"}))
            elif kind == "exit":
                out.append((_exit(gm, x), {"kind": "other"}))
            elif kind == "fight":
                # "Johnston and Olivier opened the second period with a fight" when it came in the period's first
                # minute with nothing before it (Sora's review, round 2)
                p_ = e_at // 1200 + 1
                first = e_at % 1200 < 60 and p_ <= 3 and not any(e[0] // 1200 + 1 == p_ for e in events[:k])
                opened = ("the game" if p_ == 1 else f"the {PERIODS[p_]} period") if first else None
                out.append((x(opened=opened), {"kind": "fight", "period": p_}))
            gm.broke = True
            k += 1
            continue
        # one scorer's two empty-net goals in a row: one sentence (Sora: Edström's two in the last three minutes)
        nxt = events[k + 1] if k + 1 < len(events) else None
        if (nxt and nxt[2] == "goal" and goals[x]["strength"] == "en" == goals[nxt[3]]["strength"] and goals[x]["p1"] == goals[nxt[3]]["p1"]
                and goals[x]["period"] == 3 == goals[nxt[3]]["period"]):
            g, h = goals[x], goals[nxt[3]]
            mins = -(-(1200 - g["sec"]) // 60)
            pre = (gm.attach.get(x) or {}).get("pre") or ""
            first = f", the first of them his {pre.split(' his ', 1)[1]}" if " his " in pre and pre.startswith("scored his") else ""
            m_, th_ = gm.score(h, gm.side(h))
            out.append((f"{gm.name(g['p1'])} then scored into the empty net twice in the last {num(mins)} minutes{first}, and it finished {m_}-{th_}.",
                        {"kind": "goal", "team": gm.side(h), "fixed": True, "period": 3}))
            scored_before[g["p1"]] = scored_before.get(g["p1"], 0) + 2
            prev, prev_team = h, gm.side(h)
            gm.broke = False
            k += 2
            continue
        # a run of goals by one team, close together in one period, with nothing between them
        run = [x]
        while (k + len(run) < len(events) and events[k + len(run)][2] == "goal"):
            j = events[k + len(run)][3]
            a, b = goals[run[-1]], goals[j]
            if (gm.side(a) != gm.side(b) or at(b) - at(a) > RUN_GAP or b["period"] != a["period"] or b["period"] > 3 or len(run) >= 3
                    or any(goals[i]["strength"] in ("ps", "en") for i in (run[-1], j)) or j in gm.attach or run[-1] in gm.attach
                    or j in gm.hat3 or x in gm.hat3
                    or j in gm.relief or run[-1] in gm.relief or got_back in (run[-1], j)
                    or (b["period"], b["sec"]) in stood or (a["period"], a["sec"]) in stood):
                break
            run.append(j)
        if len(run) >= 2:
            out.append((_run(gm, run, prev, prev_team, ahead_i, margin, down_most), {"kind": "run", "team": gm.side(goals[run[0]])}))
            for i in run:
                scored_before[goals[i]["p1"]] = scored_before.get(goals[i]["p1"], 0) + 1
            prev, prev_team = goals[run[-1]], gm.side(goals[run[-1]])
            gm.broke = False
            k += len(run)
            continue
        i = x
        g = goals[i]
        if got_back == i:
            who, _ = _who(gm, g["p1"], 0)
            if out and out[-1][1]["kind"] == "wiped":
                text = f"They got it back {_time(gm, g, prev)}, when {who} officially opened the scoring{HOW.get(g['strength'], '')}."
            else:  # something came between, so "they" would be unclear
                text = f"{who} officially opened the scoring for {W.the(gm.side(g))}{HOW.get(g['strength'], '')} {_time(gm, g, prev)}."
        else:
            text = _one(gm, i, prev, prev_team, scored_before, ahead_i, gw_i, margin, down_most, stood, ws, ls)
        m_, th_ = gm.score(g, gm.side(g))
        out.append((text, {"kind": "goal", "team": gm.side(g), "cut": m_ <= th_, "fixed": i in gm.relief or got_back == i, "period": g["period"],
                           "scorer": g["p1"], "last": gm.last(g["p1"])}))
        scored_before[g["p1"]] = scored_before.get(g["p1"], 0) + 1
        prev, prev_team = g, gm.side(g)
        gm.broke = False
        k += 1
    return _periods(_join(out))


SUMMARY_GOALS = 3  # a team's goals in one period, with a lead of SUMMARY_LEAD or more all period, told as one sentence
SUMMARY_LEAD = 2
SUMMARY_GAME = 6  # ...in a game with at least this many goals
BUSY = 4  # goals in one period that earn a lead-in ("It was a busy second period.")


def _flurries(gm: Game) -> tuple[dict, dict]:
    """Periods to tell differently because of how many goals came in them (Sora's review, round 2: "Our system doesn't
    handle scoring bursts super well"): {period: (team, goal indexes)} for a period one team ran away with (SUMMARY_GOALS
    of its goals, a lead of SUMMARY_LEAD+ from the period's start to its end, and the other side's goals in it only
    after them, so the order holds), told as one sentence; and {period: goals} for the other busy periods."""
    goals = gm.goals
    summ, busy = {}, {}
    if not goals:
        return summ, busy
    for p in (1, 2, 3):
        idx = [i for i, g in enumerate(goals) if g["period"] == p]
        if not idx:
            continue
        for t in (gm.home, gm.away):
            mine = [i for i in idx if gm.side(goals[i]) == t]
            start = gm.score(goals[idx[0] - 1], t) if idx[0] > 0 else (0, 0)
            leads = [start[0] - start[1]] + [gm.score(goals[i], t)[0] - gm.score(goals[i], t)[1] for i in idx]
            theirs = [i for i in idx if i not in mine]
            if (len(mine) >= SUMMARY_GOALS and len(goals) >= SUMMARY_GAME and min(leads) >= SUMMARY_LEAD
                    and all(i > mine[-1] for i in theirs) and not any(goals[i]["strength"] in ("en", "ps") for i in mine)):
                summ[p] = (t, mine)
        if p not in summ and len(idx) >= BUSY:
            busy[p] = len(idx)
    return summ, busy


def _summary(gm: Game, mine: list[int], scored_before: dict) -> str:
    """"The third period belonged to the Blues: Suter and Neighbours each scored their second, McTavish added his
    second on the power play, and Adam Jiříček, in his sixth NHL game, scored his first NHL goal, set up by Suter, to
    make it 7-1." """
    goals, W = gm.goals, gm.W
    t = gm.side(goals[mine[0]])
    p = goals[mine[0]]["period"]
    count = dict(scored_before)
    clauses: list[tuple[str, str, int]] = []  # (verb phrase, how, scorer) to merge alike neighbours
    for i in mine:
        g = goals[i]
        pid = g["p1"]
        n = count.get(pid, 0)
        count[pid] = n + 1
        how = HOW.get(g["strength"], "")
        att = gm.attach.get(i) or {}
        if att.get("pre"):
            pre = att["pre"].lstrip(", ")
            if att.get("helper"):
                pre = f"{pre}, set up by {gm.name(att['helper'])},"
            verb = (", " + pre) if att["pre"].startswith(",") else " " + pre
        elif n == 2:
            verb = " completed a natural hat trick" if pid in gm.naturals else " completed his hat trick"
        elif n >= 1:
            verb = f" scored his {ordinal(n + 1)} of the night"
        else:
            verb = " scored"
        if att.get("tail") and att["tail"].startswith("his "):
            how += f" for {att['tail']}"
        clauses.append((verb, how, pid))
    parts = []
    j = 0
    while j < len(clauses):
        verb, how, pid = clauses[j]
        same = [clauses[j]]
        while (j + len(same) < len(clauses) and clauses[j + len(same)][0] == verb and clauses[j + len(same)][1] == how
               and verb.startswith(" scored his") and not how):
            same.append(clauses[j + len(same)])
        if len(same) > 1:  # "Suter and Neighbours each scored their second"
            parts.append(f"{_and([gm.name(c[2]) for c in same])} each {verb.strip().replace(' his ', ' their ')}")
        else:
            parts.append(f"{gm.name(pid)}{verb}{how}".replace(",,", ","))
        j += len(same)
    m, th_ = gm.score(goals[mine[-1]], t)
    lead = gm.pk([f"The {PERIODS[p]} period belonged to {W.the(t)}", f"{_cap(W.the(t))} took over in the {PERIODS[p]}",
                  f"The {PERIODS[p]} was all {W.nick.get(t, t)}"], "summ", mine[0])
    body = _and(parts) if len(parts) <= 2 else ", ".join(parts[:-1]) + ", and " + parts[-1]
    return " ".join(f"{lead}: {body} to make it {m}-{th_}.".replace(",,", ",").split())


JOIN_WORDS = 16  # sentences this short or shorter can be joined in pairs
JOIN_MAX = 30  # ...when the joined sentence stays this short


def _join(items: list[tuple[str, dict]]) -> list[str]:
    """Pairs of short goal sentences become one ("Mark Jankowski made it 4-0 early in the second, but Jake DeBrusk got
    the Canucks on the board 43 seconds later"), never two joins in a row, so the paragraph mixes long sentences with
    short ones instead of reading as a list of clipped lines (Sora's review, 2026-10-19: her biggest priority)."""
    out: list[str] = []
    just = False
    i = 0
    while i < len(items):
        a, ma = items[i]
        same_scorer = i + 1 < len(items) and ma.get("scorer") and ma.get("scorer") == items[i + 1][1].get("scorer")
        if i + 1 < len(items) and (not just or same_scorer):
            b, mb = items[i + 1]
            na, nb = len(a.split()), len(b.split())
            plain = lambda t: not any(w in t for w in (" but ", ", and ", ": ", "; ", "No harm done", " while "))
            fits = (ma["kind"] in ("goal", "fight") and mb["kind"] in ("goal", "fight") and na <= JOIN_WORDS and nb <= JOIN_WORDS
                    and na + nb <= JOIN_MAX and plain(a) and plain(b) and not ma.get("fixed") and not mb.get("fixed")
                    and not b.startswith("They ") and max(ma.get("period", 9), mb.get("period", 9)) <= 3
                    and (ma["kind"] == mb["kind"] or ma.get("period") == mb.get("period")))
            if fits:
                answer = mb["kind"] == "goal" and ma["kind"] == "goal" and mb["team"] != ma["team"] and mb.get("cut")
                word = "but" if answer else "and"
                second = b if b[:1].isupper() and not b.startswith(("The ", "It ")) else b[:1].lower() + b[1:]
                if ma.get("scorer") and ma.get("scorer") == mb.get("scorer") and b.startswith(mb.get("last", "\0") + " "):
                    second = "he " + b[len(mb["last"]) + 1:]  # "Lehkonen got the Avalanche on the board, and he struck again"
                out.append(f"{a[:-1]}, {word} {second}")
                just = True
                i += 2
                continue
        out.append(a)
        just = False
        i += 1
    return out


_PERIOD_AT = __import__("re").compile(r"\b(early in|midway through|late in) the (first|second|third)( period)?\b")
_NAMED = __import__("re").compile(r"\b(?:(first|second|third) period|(?:into|left in) the (first|second|third))\b")
_INTO = __import__("re").compile(r"\b(into|left in) the (first|second|third)\b(?! period)")


def _periods(sentences: list[str]) -> list[str]:
    """"the period" for a period the last sentence already named ("early in the first ... late in the period"), and
    "later in the period" when the last sentence said the same part of it ("late in the period" three times running
    read like a list; Sora's review, round 2)."""
    out, last, part = [], (None, -9), (None, -9)
    for n, s in enumerate(sentences):
        def swap(m):
            nonlocal last, part
            p, w = m.group(2), m.group(1)
            same = last[0] == p and n - last[1] <= 1
            again = same and part[0] == (w, p) and n - part[1] == 1 and w != "early in"
            last, part = (p, n), ((w, p), n)
            return "later in the period" if again else f"{w} the period" if same else m.group(0)
        s2 = _PERIOD_AT.sub(swap, s)
        if last[0] and n - last[1] <= 1:  # "32 seconds into the second ... 1:10 into the period"
            s2 = _INTO.sub(lambda m: f"{m.group(1)} the period" if m.group(2) == last[0] else m.group(0), s2)
        named = [a or b for a, b in _NAMED.findall(s)]
        if named and not _PERIOD_AT.search(s):
            last = (named[-1], n)
        if "later in the period" in s2 and s2.count("later in the period") > 1:
            s2 = s2.replace("later in the period", "late in the period", 1)
        out.append(s2)
    return out


def _relief(gm: Game, ahead_i: int | None) -> None:
    """A goal the winners had taken off is answered where it stopped mattering: "No harm done: Matthews won it for
    them in overtime", not a loose "It didn’t matter in the end." pages later (Sora's review, 2026-10-19)."""
    gm.relief = {}
    for x in gm.wiped:
        t = gm.home if x["home"] else gm.away
        if t != gm.winner:
            continue
        later = [i for i, g in enumerate(gm.goals) if gm.side(g) == t and at(g) > at(x)]
        if gm.end == "SO":
            if not any(gm.goals[i]["period"] > 3 for i in later):
                gm.so_relief = True
            continue
        pick = next((i for i in later if gm.goals[i]["period"] > 3 or (i == ahead_i and gm.end == "REG")), None)
        if pick is None and later and gm.end == "REG":
            pick = later[-1]
        if pick is not None:
            gm.relief[pick] = "en" if gm.goals[pick]["strength"] == "en" else "plain"
            gm.relieved.add(t)


def shootout_line(gm: Game) -> str:
    """Who scored in the shootout for the winner (every scorer; the decider's round when it went long)."""
    W = gm.W
    so_pid, so_how, scorers = gm.so
    if so_pid and len(scorers) <= 1:
        return f"{gm.name(so_pid)} scored {so_how} for {W.the(gm.winner)}."
    if so_pid and "round" in so_how:
        # a long one is told as a long one: "The shootout went seven rounds, and Hintz won it for the Stars."
        return (f"The shootout went {nth_round(so_how)} rounds, and {gm.name(so_pid)} won it for {W.the(gm.winner)}"
                + (f" after {_and([gm.name(p) for p in scorers if p != so_pid])} had scored earlier" if len(scorers) > 1 else "") + ".")
    if so_pid:
        return f"{_and([gm.name(p) for p in scorers])} scored in the shootout for {W.the(gm.winner)}."
    return f"{_cap(W.the(gm.winner))} won it in the shootout."


def nth_round(so_how: str) -> str:
    """"the shootout winner in the seventh round" -> "seven"."""
    word = so_how.rsplit(" ", 2)[-2]
    return {"fourth": "four", "fifth": "five", "sixth": "six", "seventh": "seven", "eighth": "eight", "ninth": "nine",
            "tenth": "ten"}.get(word, word.rstrip("thsnd"))


def shootout_tail(gm: Game) -> str:
    """The shootout as the end of a sentence already about the game going there: ", and the Stars won it when Roope
    Hintz scored in the fourth round." (Sora's review, round 2: one sentence, not two.)"""
    W = gm.W
    so_pid, so_how, scorers = gm.so
    if not so_pid:
        return f", and {W.the(gm.winner)} won it."
    if "round" in so_how:
        return f", and {W.the(gm.winner)} won it when {gm.name(so_pid)} scored in the {so_how.rsplit(' ', 2)[-2]} round."
    if so_how.startswith("the only goal"):
        return f", and {gm.name(so_pid)} scored the only goal of the shootout to win it for {W.the(gm.winner)}."
    return f", and {W.the(gm.winner)} won it when {gm.name(so_pid)} scored the deciding goal."


def _time(gm: Game, g: dict, prev: dict | None) -> str:
    """When a goal came, relative to the last goal told when that was close and in the same period, unless something
    else was told in between (Sora: "4:39 later" after a fight read as 4:39 after the fight) or the clock says more
    ("with 12 seconds left in the first")."""
    w = when(g)
    if " left" in w or " into the " in w:
        return w
    if prev is not None and g["period"] == prev["period"] and g["period"] <= 3 and not gm.broke:
        gap = at(g) - at(prev)
        if 0 < gap <= RELATIVE:
            return f"{span(gap)} later"
        if when(g) == when(prev):
            return "later in the period"
    return when(g)


def _who(gm: Game, pid: int, count: int) -> tuple[str, str | None]:
    """The scorer's name and, for a repeat scorer, the verb that says so ("struck again", "completed his hat trick")."""
    name = gm.name(pid)
    if count == 0:
        return name, None
    if count == 1:
        return name, gm.pk(["struck again", "scored his second of the night", "scored again"], "again", pid)
    if count == 2:
        return name, "completed a natural hat trick" if pid in gm.naturals else "completed his hat trick"
    return name, f"added his {ordinal(count + 1)}"


def _team_needed(gm: Game, t: str, prev_team: str | None) -> bool:
    return prev_team != t


def _one(gm: Game, i: int, prev: dict | None, prev_team: str | None, scored_before: dict, ahead_i: int | None, gw_i: int | None,
         margin: int, down_most: int, stood: dict, ws: int, ls: int) -> str:
    goals, W = gm.goals, gm.W
    g = goals[i]
    t = gm.side(g)
    other = gm.away if t == gm.home else gm.home
    m, th_ = gm.score(g, t)
    how = HOW.get(g["strength"], "")
    tm = _time(gm, g, prev)
    who, again = _who(gm, g["p1"], scored_before.get(g["p1"], 0))
    att = gm.attach.get(i) or {}
    pre = att.get("pre")  # "scored his first NHL goal": replaces the beat's verb, the beat follows as "to ..."
    team_needed = _team_needed(gm, t, prev_team)
    tm_fin = tm  # the clock after the finite beat (blank when the beat carries it)
    T, Tp = W.the(t), W.the(t)
    cut_to = lambda a, b: _cut(gm, other, a, b)
    relief = gm.relief.get(i)
    pulled = False
    dent = ""
    only = len(goals) == 1 and g["period"] <= 3
    # the beat: as a finite verb phrase, and as an infinitive for "scored his first NHL goal ... to tie it"
    if g["period"] > 3:
        fin, inf = (f"won it for them{how}" if relief else f"won it{how}"), "to win it"
        if again and "hat trick" in again:
            fin = f"{again} to win it{how}"
        elif scored_before.get(g["p1"], 0) == 1:
            fin = f"won it{' for them' if relief else ''} with his second goal of the night{how}"
    elif only:
        fin, inf = f"scored the only goal of the game{how}", "for the only goal of the game"
    elif i == 0:
        opened = gm.pk(["opened the scoring", "struck first", "got the scoring started"], "open")
        fin, inf = f"{opened} for {T}{how}", "to open the scoring" if pre and " for " in pre else f"to open the scoring for {T}"
    elif g["strength"] == "en":
        left = f" with {left_(1200 - g['sec'])} left" if g["period"] == 3 else ""
        prev_g = goals[i - 1] if i else None
        if m - th_ == 2 and prev_g is not None and gm.side(prev_g) != t and t == gm.winner:
            # the loser had just made it one: "Nazar ended any hope of a comeback with an empty-netter with just 4
            # seconds left" (Sora's review, round 2)
            fin = gm.pk([f"ended any hope of a comeback with an empty-netter{left}", f"shut the door with an empty-netter{left}"], "en2", i)
        elif m - th_ == 2:
            fin = gm.pk([f"sealed it into the empty net{left}", f"added the empty-netter{left} to make it {m}-{th_}"], "en", i)
        else:
            fin = f"added an empty-net goal{left} to make it {m}-{th_}"
        inf = f"into the empty net{left}"
        tm_fin = ""  # the "with x:xx left" is in the beat
    elif m == th_:
        for_ = f" for {W.place(t)}" if team_needed and i else ""  # "tied it up for Detroit 32 seconds later" (Sora)
        fin = gm.pk([f"tied it {m}-{m}{for_}", f"tied it up{for_}", f"made it {m}-{m}{for_}"], "tie", i) + how
        inf = f"to tie it {m}-{m}"
    elif m == th_ + 1 and (i == 0 or goals[i - 1]["hs"] == goals[i - 1]["as"]):
        if i == ahead_i and margin <= 2 and gm.end == "REG":
            fin, inf = f"put {T} ahead for good{how}", f"to put {T} ahead for good"
        elif t == gm.winner and down_most >= 2 and i == ahead_i:
            fin, inf = f"put {T} ahead{how} to complete the comeback", f"to put {T} ahead and complete the comeback"
        else:
            fin = gm.pk([f"put {T} ahead {m}-{th_}{how}", f"gave {T} the lead{how}", f"put {T} up {m}-{th_}{how}"], "lead", i)
            inf = f"to put {T} ahead {m}-{th_}"
    elif m > th_:
        for_ = f" for {T}" if team_needed else ""
        if _insurance(gm, i, m, th_):  # a one-goal lead made two late in the third that held (writers name the clincher, 2026-10-16)
            left = left_(1200 - g["sec"])
            fin = gm.pk([f"added insurance{how} with {left} left", f"made it {m}-{th_}{for_}{how} with {left} left, and that was that"], "ins", i)
            inf, tm_fin = f"to make it {m}-{th_}{for_}", ""  # the clock is in the beat
        else:
            fin = _vary(gm, "more", [f"made it {m}-{th_}{for_}{how}", f"stretched the lead to {m}-{th_}{how}", f"made it {m}-{th_}{for_}{how}"], i)
            inf = f"to make it {m}-{th_}{for_}"
    else:  # cutting into a lead
        if m == th_ - 1 and g["period"] >= 3:  # late, the gap matters more than the first goal
            fin = gm.pk([f"pulled {T} within one{how}", f"{cut_to(th_, m)}{how}"], "cut", i)
            inf = f"to pull {T} within one"
        elif m == 1 and th_ >= 2:
            fin, inf = f"got {T} on the board{how}", f"to get {T} on the board"
        elif m == th_ - 1:
            fin = gm.pk([f"pulled {T} within one{how}", f"{cut_to(th_, m)}{how}"], "cut", i)
            inf = f"to pull {T} within one"
        elif th_ - m == 3 and i == len(goals) - 1 and t == gm.loser:  # "Oliver Moore made a dent in Carolina’s lead late in the period, bringing it to 5-2" (Sora)
            fin, inf = f"made a dent in {grammar.possessive(W.place(other))} lead{how}", f"to make it {th_}-{m}"
            dent = f"bringing it to {th_}-{m}"
        else:
            c_ = cut_to(th_, m)
            fin, inf = f"{c_}{how}", f"to {c_.replace('made', 'make', 1).replace('cut', 'cut', 1)}"
        # a real 6-on-5, not a delayed penalty (stories._goals decides), said first: "With the goalie pulled, Connor
        # McMichael scored ..." (Sora's review, 2026-10-19: tacked on at the end it read as an afterthought)
        pulled = g["own_extra"] and g["period"] == 3
    if g["period"] > 3 and len(goals) == 1:  # "Kent Johnson won it in overtime" alone read too thin
        fin = f"scored the only goal of the game{how}" if gm.end == "OT" else fin
    if g["strength"] == "en" and g["period"] == 3:  # "with 1:09 left" is in the beat already ("into the empty net with 1:09 left")
        tm = ""
    pulled_at = ""
    if pulled and g["sec"] >= 1200 - LATE:  # "With the goalie pulled and just 39 seconds left, McMichael scored ..." (Sora)
        pulled_at = f" and {left_(1200 - g['sec'])} left"
        tm = tm_fin = ""
    if pre:
        if att.get("helper"):  # "scored his first NHL goal, set up by Nathan MacKinnon, on the power play ..."
            pre = f"{pre}, set up by {gm.name(att['helper'])},"
        body = f"{who} {pre}{how} {tm} {inf}"
    elif again and i != 0 and g["period"] <= 3:  # "Caufield struck again 3:10 later to put the Canadiens up 2-0"
        body = f"{who} {again}{how} {tm} {inf}"
    else:  # "tied it 1-1, 1:27 later": a comma keeps the score and the clock apart
        body = f"{who} {fin}{',' if fin[-1:].isdigit() and tm_fin[:1].isdigit() else ''} {tm_fin}"
    body = " ".join(body.split())
    tail = [dent] if dent else []
    if (g["period"], g["sec"]) in stood:
        x = stood[(g["period"], g["sec"])]
        tail.append(f"and the goal stood after {W.the(other)} challenged for {x['why']}")
    if att.get("tail"):
        tail.append(att["tail"])
    if gw_i == i and ahead_i != i and margin <= 2 and g["strength"] != "en" and g["period"] <= 3 and gm.end == "REG":
        # "the eventual winner" hung off the verb (Sora: it should modify a goal), so the goal is named
        tail.append(gm.pk(["and that one stood up as the winner", "and that goal turned out to be the winner"], "gw", i))
    # the last goal: a late one that made it close, with the winner holding on
    is_last = i == len(goals) - 1
    if is_last and t == gm.loser and gm.end == "REG" and margin == 1 and g["period"] == 3 and g["sec"] >= 1200 - 240:
        tail.append(f"but {W.the(gm.winner)} held on")
    elif is_last and t == gm.loser and gm.rally and gm.end == "REG":
        tail.append(f"and {W.the(gm.winner)} had to hang on")
    if not tail and t == gm.loser and g["period"] == 3 and th_ - m >= 4:  # a goal that changed nothing (DeBrusk's, down 7-2)
        tail.append(gm.pk(["but by then the game was long gone", "far too late to matter"], "late", i))
    if not tail and not is_last and _last_word(gm, i):
        tail.append(_last_word(gm, i))
    if g["strength"] == "en" and any(gm.side(h) != t for h in goals[i + 1:]):  # the scoring wasn't over (Sora, CHI 4-2 STL)
        tail.append(f"but that didn’t stop {W.place(other)}")
    if tail:
        body += ", " + ", ".join(tail)
    if pulled and not relief:
        body = f"With the goalie pulled{pulled_at}, " + body
    if relief == "en":
        body = f"No harm done, {W.the(t)} won anyway, and " + body
    elif relief:
        body = "No harm done: " + body
    return " ".join(_cap(body).replace(" ,", ",").split()) + "."


def _last_word(gm: Game, i: int) -> str:
    """The loser's last goal of a regulation game, when it got them level, ahead or within one and they never scored
    again: "but that’s where Colorado’s luck ran out", "but that was as close as the Penguins got" (Sora's review,
    round 2: say when a team's scoring stopped)."""
    goals, W = gm.goals, gm.W
    g = goals[i]
    t = gm.side(g)
    rest = goals[i + 1:]
    if gm.end != "REG" or t != gm.loser or any(gm.side(h) == t for h in rest) or not rest or all(h["strength"] == "en" for h in rest):
        return ""  # an empty-netter that ends it says so itself ("ended any hope of a comeback")
    m, th_ = gm.score(g, t)
    if m >= th_:
        return gm.pk([f"but that’s where {grammar.possessive(W.place(t))} luck ran out", "but they didn’t score again"], "lastw", i)
    if m == th_ - 1:
        return gm.pk(["but that was as close as they got", "but they got no closer"], "lastw", i)
    return ""


INSURANCE_SEC = 900  # third-period seconds after which a goal that made a one-goal lead two reads as insurance


def _insurance(gm: Game, i: int, m: int, th_: int) -> bool:
    """The winners' goal late in the third that made a one-goal lead two, when the lead never got back to one."""
    g = gm.goals[i]
    if gm.end != "REG" or gm.side(g) != gm.winner or g["strength"] == "en" or g["period"] != 3 or g["sec"] < INSURANCE_SEC or m - th_ != 2 or i == 0:
        return False
    lead = lambda x: (x["hs"] - x["as"]) * (1 if g["home"] else -1)
    return lead(gm.goals[i - 1]) == 1 and all(lead(x) >= 2 for x in gm.goals[i:])


def _run(gm: Game, run: list[int], prev: dict | None, prev_team: str | None, ahead_i: int | None, margin: int, down_most: int) -> str:
    """Two or three goals by one team within five minutes of each other, as one sentence."""
    goals, W = gm.goals, gm.W
    gs = [goals[i] for i in run]
    t = gm.side(gs[0])
    T = W.the(t)
    first, last_ = gs[0], gs[-1]
    before = gm.score(goals[run[0] - 1], t) if run[0] > 0 else (0, 0)
    after = gm.score(last_, t)
    mb, tb = before
    ma, ta = after
    span = at(last_) - at(first)
    # what the run did to the score
    if mb < tb and ma == ta:
        did = f"to tie it {ma}-{ma}"
    elif mb < tb and ma > ta:
        did = f"to turn a {tb}-{mb} deficit into a {ma}-{ta} lead"
    elif mb < tb:
        did = "to " + _cut(gm, gm.away if t == gm.home else gm.home, ta, ma).replace("made", "make", 1)
    elif mb == tb and ma > ta:
        if run[-1] == ahead_i and margin <= 2 and gm.end == "REG":
            did = f"to put {T} ahead for good at {ma}-{ta}"
        elif run[0] == 0 and last_["period"] == 1 and last_["sec"] < 600 and ma >= 3:
            did = f"to put {T} up {ma}-{ta} before the first period was half over"
        else:
            did = f"to put {T} up {ma}-{ta}" if mb or run[0] else f"to put {T} up {ma}-{ta}"
    else:
        did = "to " + _vary(gm, "more", [f"make it {ma}-{ta}", f"stretch the lead to {ma}-{ta}"], run[0])
    if t == gm.winner and down_most >= 2 and run[-1] == ahead_i and ma > ta and gm.end == "REG" and margin <= 2:
        did = f"to put {T} ahead for good and complete the comeback"
    kinds = {g["strength"] for g in gs}
    how = HOW.get(kinds.pop(), "") if len(kinds) == 1 else ""
    names = [g["p1"] for g in gs]
    tm = "later in the period" if prev is not None and prev["period"] == first["period"] and when(prev) == when(first) else when(first)
    if tm.endswith(" in") or " into the " in tm:  # "scored 2:56 apart 1:06 in" reads badly
        tm = f"early in the {PERIODS[first['period']]}"
    gap = grammar.span(span)
    # what came after: the other side never answered (Sora: "The Sabres didn't have an answer"), or this was the
    # loser's last push
    other = gm.away if t == gm.home else gm.home
    after_ = goals[run[-1] + 1:]
    tail = ""
    if t == gm.winner and not any(gm.side(h) == other for h in after_) and ma - ta >= 3:
        tail = gm.pk([f", and {W.the(other)} never had an answer", f", and {W.the(other)} had no answer"], "noans", run[0])
    elif t == gm.loser and not after_ and gm.end == "REG" and ta - ma == 1:
        tail = gm.pk([f", but {W.the(t)} couldn’t find another", f", but {W.the(gm.winner)} held on"], "runheld", run[0])
    elif t == gm.loser and after_ and gm.end == "REG":
        lw = _last_word(gm, run[-1])
        tail = f", {lw}" if lw else ""
    if len(set(names)) == 1:
        who = gm.name(names[0])
        return _cap(f"{who} scored {tm} and again {gap} later{how} {did}{tail}.").replace("  ", " ")
    if len(gs) == 2:
        a, b = gm.name(names[0]), gm.name(names[1])
        return _cap(f"{a} and {b} scored{how.replace(' on the power play', ' power-play goals')} {gap} apart {tm} {did}{tail}.").replace("  ", " ")
    if len(set(names)) < len(names):  # "Zibanejad scored twice and Panarin once in less than six minutes"
        counts = {p: names.count(p) for p in dict.fromkeys(names)}
        first_p, first_c = next(iter(counts.items()))
        times = lambda c: "twice" if c == 2 else "once" if c == 1 else f"{num(c)} times"
        rest = _and([f"{gm.name(p)} {times(c)}" for p, c in list(counts.items())[1:]])
        return _cap(f"{gm.name(first_p)} scored {times(first_c)} and {rest} in {gap}{how} {tm} {did}{tail}.").replace("  ", " ")
    listed = _and([gm.name(p) for p in names])
    return _cap(f"{listed} scored {num(len(gs))} goals in {gap}{how} {tm} {did}{tail}.").replace("  ", " ")


def _vary(gm: Game, key: str, pool: list[str], i: int) -> str:
    """The pool's pick for this goal, unless it opens with the same verb as the last pick from the pool ("stretched the
    lead to 4-1 ... stretched the lead to 5-1"), then the next option that doesn't."""
    pick = gm.pk(pool, key, i)
    verb = lambda t: t.split()[0].replace("make", "made").replace("stretch", "stretched")
    if gm.used.get(key) == verb(pick):
        pick = next((x for x in pool if verb(x) != gm.used[key]), pick)
    gm.used[key] = verb(pick)
    return pick


def _cut(gm: Game, other: str, a: int, b: int) -> str:
    """A goal that cut a lead, varied through the game ("cut Edmonton’s lead to 8-5" first, as Sora preferred it to
    "cut it", then "made it", then "cut it"), so four in one paragraph don't all read alike."""
    n = gm.cuts
    gm.cuts += 1
    return [f"cut {grammar.possessive(gm.W.place(other))} lead to {a}-{b}", f"made it {a}-{b}", f"cut it to {a}-{b}"][n % 3]


def _change(gm: Game, c: tuple) -> str:
    t, f, rl, pulled = c
    W = gm.W

    def who(x):
        return gm.name(x["id"]) or x["name"]

    def goals_n(n):
        return f"{num(n)} goal{'' if n == 1 else 's'}"
    came = rl.get("came_in") or {}
    sec = came.get("sec")
    per = ""
    if sec is not None:
        p = sec // 1200 + 1
        per = " in " + ({1: "the first", 2: "the second", 3: "the third"}.get(p, "overtime")) if sec % 1200 else \
            {1200: " to start the second", 2400: " to start the third"}.get(sec, " for overtime")
    if came.get("back"):
        return f"{who(rl)} filled in for {who(f)} for {rl['toi']}{per} before {W.place(t)} went back to {who(f)}."
    rest = per.replace(" in ", " for the rest of ", 1) if per.startswith(" in ") else per  # both played in that period
    if pulled:
        return f"{_cap(W.place(t))} pulled {who(f)} after {goals_n(f['ga'])} and turned to {who(rl)}{rest}."
    if came.get("how") == "mid":
        return f"{who(f)} left the game{per}, and {who(rl)} finished it in net for {W.place(t)}."
    return f"{who(rl)} took over from {who(f)} in net for {W.place(t)}{rest}."


def _exit(gm: Game, x: dict) -> str:
    from pipeline.export import surprises
    W = gm.W
    who_ = f"{_cap(W.poss(x['team']))} {gm.name(x['id'])}" if x["id"] not in gm.said else gm.name(x["id"])
    if x.get("confirmed", True):
        return " ".join(f"{who_} left {surprises.when_left(x['sec'])} and didn’t return.".split())
    return " ".join(f"{who_} didn’t play after {surprises.clock_at(x['sec'])}.".split())


def attachments(gm: Game, found: list[dict], season_goals: dict[int, int]) -> tuple[dict, set]:
    """Firsts and milestones that ride on a goal: {goal index: {"pre": "scored his first NHL goal", "tail": "..."}},
    and the players whose first the chronology now tells (so nothing else repeats it)."""
    goals = gm.goals
    att: dict[int, dict] = {}
    told: set[int] = set()
    first_goal_of = {}
    for i, g in enumerate(goals):
        first_goal_of.setdefault(g["p1"], i)
    for x in found:
        pid, k = x["pid"], x["kind"]
        i = first_goal_of.get(pid)
        if k == "first_goal" and i is not None:
            # "T.J. Hughes, undrafted and in his 8th NHL game, scored his first NHL goal ..., set up by Nathan MacKinnon"
            # (outside coverage told all three, 2026-10-16)
            debut = " in his NHL debut" if x.get("debut") else ""
            bits = (["undrafted"] if x.get("undrafted") else []) + ([f"in his {nth_word(x['nth'])} NHL game"] if not x.get("debut") and (x.get("nth") or 0) >= 2 else [])
            lead_in = f", {' and '.join(bits)}, " if bits else ""
            att.setdefault(i, {})["pre"] = f"{lead_in}scored his first NHL goal{debut}"
            helper = goals[i].get("p2")
            if helper and gm.N.get(helper):
                att[i]["helper"] = helper  # named when the sentence is written, so his first mention is his full name
            told.add(pid)
        elif k == "club_goal" and i is not None:
            att.setdefault(i, {})["pre"] = f"scored his first goal for {gm.W.place(x['team'])}"
            told.add(pid)
        elif k == "milestone" and x["what"] in ("goal", "point", "assist") and x.get("have") is not None:
            n = x["mark"] - x["have"]  # his nth of tonight reached the mark
            if x["what"] == "goal":
                mine = [j for j, g in enumerate(goals) if g["p1"] == pid]
            elif x["what"] == "point":
                mine = [j for j, g in enumerate(goals) if pid in (g["p1"], g["p2"], g["p3"])]
            else:
                mine = [j for j, g in enumerate(goals) if pid in (g["p2"], g["p3"])]
            if not mine or n < 1:
                continue
            j = mine[min(n, len(mine)) - 1]
            if x["what"] == "goal":
                att.setdefault(j, {})["pre"] = f"scored his {th(x['mark'])} NHL goal"
            elif goals[j]["p1"] == pid:
                att.setdefault(j, {})["tail"] = f"his {th(x['mark'])} NHL {x['what']}"
            else:
                att.setdefault(j, {})["tail"] = f"with {gm.name(pid)} picking up his {th(x['mark'])} NHL {x['what']} on the assist"
            told.add(pid)
    for i, g in enumerate(goals):  # his 30th of the season
        pid = g["p1"]
        if i in att or not gm.N.get(pid):
            continue
        n = season_goals.get(pid, 0) + sum(1 for h in goals[:i] if h["p1"] == pid) + 1
        if n in SEASON_MARKS:
            att.setdefault(i, {})["pre"] = f"scored his {th(n)} of the season"
    return att, told


def so_winner(tries: list[dict], winner_id: int) -> tuple[int | None, str, list[int]]:
    """(decider, how, scorers): the shooter whose shootout goal decided it (the winner's (L+1)th goal, where L is how
    many the loser scored; before 2026-10-17 the winner's LAST goal was named, wrong in 21 of 119 shootouts of
    2025-26), how to tell it ("the only goal of the shootout", "the shootout winner in the seventh round" past the
    usual three, or "the shootout winner"), and every shooter who scored for the winner, in order."""
    goals = [t for t in tries if t["scored"]]
    mine = [t for t in goals if t["tid"] == winner_id]
    if not mine:
        return None, "", []
    theirs = len(goals) - len(mine)
    decider = mine[min(theirs, len(mine) - 1)]
    scorers = [t["pid"] for t in mine]
    if len(goals) == 1:
        return decider["pid"], "the only goal of the shootout", scorers
    round_ = sum(1 for t in tries[:tries.index(decider) + 1] if t["tid"] == winner_id)
    if round_ > 3:
        return decider["pid"], f"the shootout winner in the {ordinal(round_)} round", scorers
    return decider["pid"], "the shootout winner", scorers
