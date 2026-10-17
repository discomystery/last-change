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

PERIODS = {1: "first", 2: "second", 3: "third"}
WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
RUN_GAP = 300  # seconds between two goals by one team that read as one sentence
RELATIVE = 300  # a goal this soon after the last one told is "x:xx later"
LATE = 300  # the last five minutes of the third read "with x:xx left"
HOW = {"pp": " on the power play", "sh": " shorthanded", "ps": " on a penalty shot"}
SEASON_MARKS = (20, 30, 40, 50, 60, 70)  # a scorer's nth goal of the season worth a clause


def num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:] if t else t


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def clock(s: int) -> str:
    return ("1 second" if s == 1 else f"{s} seconds") if s < 60 else f"{s // 60}:{s % 60:02d}"


def ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth",
            10: "tenth"}.get(n, f"{n}th")


ORDINAL_WORDS = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "11th", "12th",
                 "13th", "14th", "15th", "16th", "17th", "18th", "19th", "20th"]


def nth_word(n: int) -> str:
    """"eighth" for small counts ("in his eighth NHL game"), "23rd" beyond."""
    return ORDINAL_WORDS[n] if 0 < n <= 20 else th(n)


def th(n: int) -> str:
    """1,000th, 302nd, 21st (never 11st)."""
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suf}"


def at(g: dict) -> int:
    return (g["period"] - 1) * 1200 + g["sec"]


def when(g: dict) -> str:
    """"4:10 in", "early in the second", "with 2:10 left" (the last five minutes of the third), "in overtime"."""
    p, s = g["period"], g["sec"]
    if p > 3:
        return f"{clock(s)} into overtime" if s < 180 else "in overtime"
    if p == 3 and s >= 1200 - LATE:
        return f"with {clock(1200 - s)} left"
    if p == 1 and s < 120:
        return f"{clock(s)} in"
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

    def side(self, g: dict) -> str:
        return self.home if g["home"] else self.away

    def score(self, g: dict, t: str) -> tuple[int, int]:
        """(mine, theirs) after the goal, from team t's side."""
        return (g["hs"], g["as"]) if t == self.home else (g["as"], g["hs"])


def paragraph(gm: Game) -> list[str]:
    """The chronology sentences, in order."""
    goals = gm.goals
    W = gm.W
    out: list[str] = []
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
    events.sort(key=lambda e: (e[0], e[1]))
    stood = {(x["period"], x["sec"]): x for x in gm.reviews if x["kind"] == "stood"}
    scored_before: dict[int, int] = {}  # scorer -> goals already told
    prev: dict | None = None  # the last goal told
    prev_team: str | None = None  # the team of the last sentence's goal (None after another kind of event)
    told_teams: list = []
    k = 0
    while k < len(events):
        e_at, _, kind, x = events[k]
        if kind != "goal":
            prev_team = None
            if kind == "wiped":
                t = gm.home if x["home"] else gm.away
                what = "what would have been the winner" if x["period"] > 3 else "a goal"
                out.append(f"{_cap(W.place(t))} had {what} taken off {x['why']} {when(x)}.")
            elif kind == "no_goal":
                t = gm.home if x["shooter_home"] else gm.away
                out.append(f"The league looked at a {W.nick.get(t, t)} chance {when(x)} on video and ruled no goal.")
            elif kind == "ps":
                if x["type"] == "shot-on-goal" and x.get("goalie"):
                    out.append(f"{gm.name(x['goalie'])} stopped {gm.name(x['p1'])} on a penalty shot {when(x)}.")
                else:
                    out.append(f"{gm.name(x['p1'])} missed the net on a penalty shot {when(x)}.")
            elif kind == "change":
                out.append(_change(gm, x))
            elif kind == "exit":
                out.append(_exit(gm, x))
            k += 1
            continue
        # a run of goals by one team, close together, with nothing between them
        run = [x]
        while (k + len(run) < len(events) and events[k + len(run)][2] == "goal"):
            j = events[k + len(run)][3]
            a, b = goals[run[-1]], goals[j]
            if (gm.side(a) != gm.side(b) or at(b) - at(a) > RUN_GAP or b["period"] > 3 or a["period"] > 3 or len(run) >= 3
                    or any(goals[i]["strength"] in ("ps", "en") for i in (run[-1], j)) or j in gm.attach or run[-1] in gm.attach
                    or (b["period"], b["sec"]) in stood or (a["period"], a["sec"]) in stood):
                break
            run.append(j)
        if len(run) >= 2:
            out.append(_run(gm, run, prev, prev_team, ahead_i, margin, down_most))
            for i in run:
                scored_before[goals[i]["p1"]] = scored_before.get(goals[i]["p1"], 0) + 1
            prev, prev_team = goals[run[-1]], gm.side(goals[run[-1]])
            told_teams.append(prev_team)
            k += len(run)
            continue
        i = x
        g = goals[i]
        out.append(_one(gm, i, prev, prev_team, scored_before, ahead_i, gw_i, margin, down_most, stood, ws, ls))
        scored_before[g["p1"]] = scored_before.get(g["p1"], 0) + 1
        prev, prev_team = g, gm.side(g)
        told_teams.append(prev_team)
        k += 1
    return out


def shootout_line(gm: Game) -> str:
    """Who scored in the shootout for the winner (every scorer; the decider's round when it went long)."""
    W = gm.W
    so_pid, so_how, scorers = gm.so
    if so_pid and len(scorers) <= 1:
        return f"{gm.name(so_pid)} scored {so_how} for {W.the(gm.winner)}."
    if so_pid:
        ending = f", and {gm.last(so_pid)}’s in the {so_how.rsplit(' ', 2)[-2]} round ended it" if "round" in so_how else ""
        return f"{_and([gm.name(p) for p in scorers])} scored in the shootout for {W.the(gm.winner)}{ending}."
    return f"{_cap(W.the(gm.winner))} won it in the shootout."


def _time(gm: Game, g: dict, prev: dict | None) -> str:
    """When a goal came, relative to the last goal told when that was close and in the same period."""
    if prev is not None and g["period"] == prev["period"] and g["period"] <= 3:
        gap = at(g) - at(prev)
        if 0 < gap <= RELATIVE and not (g["period"] == 3 and g["sec"] >= 1200 - LATE):
            return f"{clock(gap)} later"
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
        return name, "completed his hat trick"
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
    only = len(goals) == 1 and g["period"] <= 3
    # the beat: as a finite verb phrase, and as an infinitive for "scored his first NHL goal ... to tie it"
    if g["period"] > 3:
        fin, inf = f"won it{how}", "to win it"
        if again == "completed his hat trick":
            fin = f"completed his hat trick to win it{how}"
        elif scored_before.get(g["p1"], 0) == 1:
            fin = f"won it with his second goal of the night{how}"
    elif only:
        fin, inf = f"scored the only goal of the game{how}", "for the only goal of the game"
    elif i == 0:
        opened = gm.pk(["opened the scoring", "struck first", "got the scoring started"], "open")
        fin, inf = f"{opened} for {T}{how}", "to open the scoring" if pre and " for " in pre else f"to open the scoring for {T}"
    elif g["strength"] == "en":
        left = f" with {clock(1200 - g['sec'])} left" if g["period"] == 3 else ""
        if m - th_ == 2:
            fin = gm.pk([f"sealed it into the empty net{left}", f"added the empty-netter{left} to make it {m}-{th_}"], "en", i)
        else:
            fin = f"added an empty-net goal{left} to make it {m}-{th_}"
        inf = f"into the empty net{left}"
        tm_fin = ""  # the "with x:xx left" is in the beat
    elif m == th_:
        fin = gm.pk([f"tied it {m}-{m}", "tied it", f"made it {m}-{m}"], "tie", i) + how
        inf = f"to tie it {m}-{m}"
    elif m == th_ + 1 and (i == 0 or goals[i - 1]["hs"] == goals[i - 1]["as"]):
        if i == ahead_i and margin <= 2 and gm.end == "REG":
            fin, inf = f"put {T} ahead for good{how}", f"to put {T} ahead for good"
        elif t == gm.winner and down_most >= 2 and i == ahead_i:
            fin, inf = f"put {T} ahead{how} to cap the comeback", f"to put {T} ahead and cap the comeback"
        else:
            fin = gm.pk([f"put {T} ahead {m}-{th_}{how}", f"gave {T} the lead{how}", f"put {T} up {m}-{th_}{how}"], "lead", i)
            inf = f"to put {T} ahead {m}-{th_}"
    elif m > th_:
        for_ = f" for {T}" if team_needed else ""
        if _insurance(gm, i, m, th_):  # a one-goal lead made two late in the third that held (writers name the clincher, 2026-10-16)
            left = clock(1200 - g["sec"])
            fin = gm.pk([f"added insurance{how} with {left} left", f"made it {m}-{th_}{for_}{how} with {left} left, and that was that"], "ins", i)
            inf, tm_fin = f"to make it {m}-{th_}{for_}", ""  # the clock is in the beat
        else:
            fin = gm.pk([f"made it {m}-{th_}{for_}{how}", f"stretched the lead to {m}-{th_}{how}", f"made it {m}-{th_}{for_}{how}"], "more", i)
            inf = f"to make it {m}-{th_}{for_}"
    else:  # cutting into a lead
        if m == th_ - 1 and g["period"] >= 3:  # late, the gap matters more than the first goal
            fin = gm.pk([f"pulled {T} within one{how}", f"cut it to {th_}-{m}{how}", f"made it {th_}-{m}{how}"], "cut", i)
            inf = f"to pull {T} within one"
        elif m == 1 and th_ >= 2:
            fin, inf = f"got {T} on the board{how}", f"to get {T} on the board"
        elif m == th_ - 1:
            fin = gm.pk([f"pulled {T} within one{how}", f"cut it to {th_}-{m}{how}", f"made it {th_}-{m}{how}"], "cut", i)
            inf = f"to pull {T} within one"
        else:
            fin, inf = f"cut it to {th_}-{m}{how}", f"to cut it to {th_}-{m}"
        if g["own_extra"] and g["period"] == 3:  # a real 6-on-5, not a delayed penalty (stories._goals decides)
            fin += " with the goalie pulled"
            inf += " with the goalie pulled"
    if pre:
        if att.get("helper"):  # "scored his first NHL goal, set up by Nathan MacKinnon, on the power play ..."
            pre = f"{pre}, set up by {gm.name(att['helper'])},"
        body = f"{who} {pre}{how} {tm} {inf}"
    elif again and i != 0 and g["period"] <= 3:  # "Caufield struck again 3:10 later to put the Canadiens up 2-0"
        body = f"{who} {again}{how} {tm} {inf}"
    else:  # "tied it 1-1, 1:27 later": a comma keeps the score and the clock apart
        body = f"{who} {fin}{',' if fin[-1:].isdigit() and tm_fin[:1].isdigit() else ''} {tm_fin}"
    body = " ".join(body.split())
    tail = []
    if (g["period"], g["sec"]) in stood:
        x = stood[(g["period"], g["sec"])]
        tail.append(f"and the goal stood after {W.the(other)} challenged for {x['why']}")
    if att.get("tail"):
        tail.append(att["tail"])
    if gw_i == i and ahead_i != i and margin <= 2 and g["strength"] != "en" and g["period"] <= 3 and gm.end == "REG":
        tail.append("the eventual winner")
    # the last goal: a late one that made it close, with the winner holding on
    is_last = i == len(goals) - 1
    if is_last and t == gm.loser and gm.end == "REG" and margin == 1 and g["period"] == 3 and g["sec"] >= 1200 - 240:
        tail.append(f"but {W.the(gm.winner)} held on")
    elif is_last and t == gm.loser and gm.rally and gm.end == "REG":
        tail.append(f"and {W.the(gm.winner)} had to hang on")
    if tail:
        body += ", " + ", ".join(tail)
    return " ".join(_cap(body).replace(" ,", ",").split()) + "."


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
        did = f"to cut it to {ta}-{ma}"
    elif mb == tb and ma > ta:
        if run[-1] == ahead_i and margin <= 2 and gm.end == "REG":
            did = f"to put {T} ahead for good at {ma}-{ta}"
        elif run[0] == 0 and last_["period"] == 1 and last_["sec"] < 600 and ma >= 3:
            did = f"to put {T} up {ma}-{ta} before the first period was half over"
        else:
            did = f"to put {T} up {ma}-{ta}" if mb or run[0] else f"to put {T} up {ma}-{ta}"
    else:
        did = gm.pk([f"to make it {ma}-{ta}", f"to stretch the lead to {ma}-{ta}"], "run-more", run[0])
    if t == gm.winner and down_most >= 2 and run[-1] == ahead_i and ma > ta and gm.end == "REG" and margin <= 2:
        did = f"to put {T} ahead for good and cap the comeback"
    kinds = {g["strength"] for g in gs}
    how = HOW.get(kinds.pop(), "") if len(kinds) == 1 else ""
    names = [g["p1"] for g in gs]
    tm = "later in the period" if prev is not None and prev["period"] == first["period"] and when(prev) == when(first) else when(first)
    if len(set(names)) == 1:
        who = gm.name(names[0])
        return _cap(f"{who} scored {tm} and again {clock(span)} later{how} {did}.").replace("  ", " ")
    if len(gs) == 2:
        a, b = gm.name(names[0]), gm.name(names[1])
        return _cap(f"{a} and {b} scored{how.replace(' on the power play', ' power-play goals')} {clock(span)} apart {tm} {did}.").replace("  ", " ")
    if len(set(names)) < len(names):  # "Zibanejad scored twice and Panarin once in a 5:49 span"
        counts = {p: names.count(p) for p in dict.fromkeys(names)}
        first_p, first_c = next(iter(counts.items()))
        times = lambda c: "twice" if c == 2 else "once" if c == 1 else f"{num(c)} times"
        rest = _and([f"{gm.name(p)} {times(c)}" for p, c in list(counts.items())[1:]])
        return _cap(f"{gm.name(first_p)} scored {times(first_c)} and {rest} in a {clock(span)} span{how} {tm} {did}.").replace("  ", " ")
    listed = _and([gm.name(p) for p in names])
    return _cap(f"{listed} scored {num(len(gs))} goals in {clock(span)}{how} {tm} {did}.").replace("  ", " ")


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
    if pulled:
        return f"{_cap(W.place(t))} pulled {who(f)} after {goals_n(f['ga'])} and turned to {who(rl)}{per}."
    if came.get("how") == "mid":
        return f"{who(f)} left the game{per}, and {who(rl)} finished it in net for {W.place(t)}."
    return f"{who(rl)} took over from {who(f)} in net for {W.place(t)}{per}."


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
