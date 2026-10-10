"""Preview snapshots: the "What to watch" calls for every game in the next week.

Each call is a structured, checkable claim (metric, team, baseline, direction, threshold) so the post-game page can
grade it by rule. A game's file is rewritten on every run until puck drop, then frozen: once the game has started the
file is never touched again, so the recap grades exactly what the preview said.

Reads the site JSON the other exports have just written (fingerprints, lines), so it must run after them.
"""
import json
import zlib
from datetime import datetime, timedelta, timezone

from pipeline.config import SITE_DATA

DAYS_AHEAD = 7
# (offense trait, defense trait it runs into, what the offense does, what the defense does)
CLASHES = [
    ("volume", "suppression", "shot volume", "shot suppression"),
    ("quality", "qualityAllowed", "shot quality", "keeping shots to the outside"),
    ("turnover", "breakdowns", "chances off turnovers", "avoiding breakdowns"),
    ("pp", "pk", "power play", "penalty kill"),
]
STRONG, WEAK = 70, 35  # league percentiles (higher is better) for a "meets a soft spot" call
# What the recap will measure for each trait, in the game itself.
MEASURE = {
    "volume": "5-on-5 shot attempts per 60, adjusted for score and venue",
    "quality": "expected goals per unblocked shot",
    "turnover": "share of 5-on-5 expected goals within 5 seconds of winning the puck",
    "pp": "expected goals per 60 on the power play",
}
# How each trait's value reads in a sentence, and whether its name takes a plural verb.
SAY = {
    "volume": lambda v: f"{v:.1f} shot attempts per 60 at 5-on-5",
    "quality": lambda v: f"one expected goal for every {1 / v:.1f} unblocked shots",
    "turnover": lambda v: f"{v:.1f}% of its chances come right after winning the puck",
    "pp": lambda v: f"{v:.1f} expected goals per 60",
}
# Several ways to say each call, so a week of previews doesn't read like one sentence on repeat. The wording is picked
# from the game and the call (not at random), so it stays put between runs. {X} is the attacking team, {Y} the one
# defending; {xr}/{yr} their league ranks, {xv} the attacking team's figure in words. A leading * marks wording that
# claims the attacking team is among the league's best, used only when it ranks in the top five.
CLASH_WORDS = {
    "volume": (
        ["{X} should get plenty of shots away", "Expect {X} to fire from everywhere", "{Y} gives up shots, and {X} takes them",
         "{X}’s shot volume meets a soft spot"],
        ["{X} ranks {xr} of 32 for shot volume ({xv}); {Y} ranks {yr} for shot suppression.",
         "*Few teams shoot as often as {X}: {xv}, {xr} in the league. {Y} sits {yr} of 32 at keeping shots down.",
         "*{Y} is {yr} of 32 at shot suppression, and {X} is one of the league’s busiest shooting teams ({xr}: {xv})."],
    ),
    "quality": (
        ["{X} gets to the dangerous areas", "{Y} lets teams in close, and {X} goes there", "Look for {X} to find the slot",
         "Quality over quantity for {X}"],
        ["{X} ranks {xr} of 32 for shot quality ({xv}); {Y} ranks {yr} for keeping shots to the outside.",
         "*{X}’s shots are among the league’s most dangerous ({xr} of 32: {xv}). {Y} ranks {yr} at keeping opponents to the outside.",
         "{Y} has struggled to keep shots to the perimeter ({yr} of 32), and {X} makes its shots count: {xv}, {xr} in the league."],
    ),
    "turnover": (
        ["{X} pounces on mistakes, and {Y} makes them", "Watch {Y}’s puck management", "Turnovers could swing this one",
         "{X} feeds on loose pucks"],
        ["{X} ranks {xr} of 32 for chances off turnovers ({xv}); {Y} ranks {yr} for avoiding breakdowns.",
         "*{X} turns takeaways into chances as well as almost anyone ({xr} of 32: {xv}). {Y} ranks {yr} at avoiding breakdowns.",
         "{Y} gives up more dangerous chances after losing the puck than most ({yr} of 32 at avoiding breakdowns). {X} ranks {xr} for chances off turnovers."],
    ),
    "pp": (
        ["{X}’s power play has a target", "Penalties could cost {Y}", "Special teams tilt toward {X}", "{Y} can’t afford to take penalties"],
        ["{X} ranks {xr} of 32 on the power play ({xv}); {Y} ranks {yr} on the penalty kill.",
         "{X}’s power play creates {xv}, {xr} in the league. {Y}’s penalty kill ranks {yr}.",
         "{Y}’s penalty kill sits {yr} of 32, and {X}’s power play is {xr} ({xv})."],
    ),
}
MATCHUP_HEADS = ["{H} will try to get {mine} out against {theirs}", "Watch for {mine} against {theirs}",
                 "Last change: {mine} against {theirs}", "{H} likes this matchup at home"]
CONTRAST_HEADS = {  # {top} is the team at the high end of the scale, {bot} the low end
    "volume": ["{top} shoots far more than {bot}", "Volume against patience"],
    "quality": ["{top} hunts better looks than {bot}", "Point-blank against perimeter"],
    "suppression": ["{top} locks it down; {bot} doesn’t", "One stingy defense, one porous one"],
    "qualityAllowed": ["{top} keeps shots outside; {bot} lets them in", "Sheltered against exposed"],
    "pace": ["Different speeds", "{top} plays fast; {bot} slows it down"],
    "point": ["{top} shoots from the point; {bot} works down low", "Different shooting spots"],
    "forecheck": ["{top} hounds the puck; {bot} sits back", "Forecheck against patience"],
    "physical": ["Bruisers against finesse", "{top} hits; {bot} mostly doesn’t"],
    "depth": ["{top} rolls four lines; {bot} leans on its top six", "Depth against star power"],
    "pp": ["One power play bites, one doesn’t", "{top}’s power play outclasses {bot}’s"],
    "pk": ["{top} kills penalties far better than {bot}", "Airtight against leaky on the kill"],
}
CONTRAST_BODIES = ["{top} ranks {rt} of 32 for {name}, toward the {hi} end; {bot} ranks {rb}, toward the {lo} end.",
                   "On {name}, {top} sits {rt} of 32, at the {hi} end of the scale. {bot} is {rb}, at the {lo} end."]
PACE_WORDS = {
    "fast": (["Expect a track meet", "Shots should fly both ways", "Two of the league’s busiest games meet"],
             "{A}’s games average {av} ({ar} of 32 for pace); {H}’s average {hv} ({hr})."),
    "slow": (["Expect a grind", "Don’t expect many shots", "A low-event night is likely"],
             "{A}’s games average only {av} ({ar} of 32 for pace); {H}’s average {hv} ({hr})."),
}
PHYSICAL_HEADS = ["A physical night ahead", "Bodies will fly", "Two of the league’s heaviest-hitting teams"]
TOP = 5  # "among the best" wording needs a top-five rank
BOTH = 70  # both teams at or above this percentile (or both at or below 100 - BOTH) for a pace or physical call


def _pick(pool: list[str], *key) -> str:
    return pool[zlib.crc32("-".join(map(str, key)).encode()) % len(pool)]
MIN_HOME_GAMES, MIN_MINUTES = 3, 30  # before a home matching habit becomes a call
ORD = ["first", "second", "third", "fourth"]


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _last(name: str) -> str:
    return name.split(" ", 1)[-1]


def clash_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    out = []
    for off, dfn, _, _ in CLASHES:
        for x, y in ((away, home), (home, away)):
            o, d = fp[x]["dims"][off]["blend"], fp[y]["dims"][dfn]["blend"]
            if o["pct"] >= STRONG and d["pct"] <= WEAK:
                words = {"X": places[x], "Y": places[y], "xr": _ordinal(o["rank"]), "yr": _ordinal(d["rank"]), "xv": SAY[off](o["v"])}
                league = o["v"] * 100 / o["index"] if o.get("index") else None
                out.append({
                    "kind": "clash", "gap": o["pct"] - d["pct"], "metric": off, "team": x, "opp": y,
                    "head": _pick(CLASH_WORDS[off][0], gid, off, x).format(**words),
                    "body": _pick([b.lstrip("*") for b in CLASH_WORDS[off][1] if o["rank"] <= TOP or not b.startswith("*")], gid, off, x, "body").format(**words),
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


def matchup_claim(gid: int, away: str, home: str, lines: dict, places: dict) -> dict | None:
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
    mine = ", ".join(_last(n) for n in unit["players"])
    theirs = ", ".join(_last(n) for n in opp["players"])
    return {
        "kind": "matchup", "metric": "matchup_share", "team": home, "opp": away,
        "head": _pick(MATCHUP_HEADS, gid, "matchup").format(H=places[home], mine=mine, theirs=theirs),
        "body": f"At home, {places[home]}’s {r['label']} ({mine}) has spent {v}% of its 5-on-5 time against opponents’ {ORD[j]} lines. "
                f"With no line matching it would be about {r['expected'][j]}%. Tonight {places[away]}’s usual {ORD[j]} line is {theirs}.",
        "cite": f"Who plays against whom · {places[home]} home games this season · {mu['games']} games, {r['minutes']:.0f} minutes for this group at 5-on-5",
        "check": {"metric": "matchup_share", "measure": f"share of {places[home]} {r['label']}'s 5-on-5 time against {places[away]} L{j + 1}",
                  "team": home, "unit": r["label"], "unit_ids": unit["ids"], "opp_line": f"L{j + 1}", "opp_ids": opp["ids"],
                  "opp_lines": {u["label"]: u["ids"] for u in a["units"] if u["label"][0] == "L"},
                  "baseline": v, "threshold": r["expected"][j], "direction": "above",
                  "rule": "held if the share is at or above the home habit; partly if above the no-matching level; otherwise didn't happen"},
    }


CONTRAST = {  # trait: (name, low end, high end) for the ungraded "biggest contrast" note
    "volume": ("shot volume", "selective", "relentless"), "quality": ("shot quality", "perimeter", "point-blank"),
    "suppression": ("shot suppression", "porous", "stingy"), "qualityAllowed": ("quality allowed", "exposed", "sheltered"),
    "pace": ("pace", "slow", "fast"), "point": ("point-shot reliance", "down low", "point-heavy"),
    "forecheck": ("forecheck pressure", "passive", "hounding"), "physical": ("physicality", "finesse", "bruising"),
    "depth": ("depth", "top-heavy", "deep"), "pp": ("power play", "harmless", "lethal"), "pk": ("penalty kill", "leaky", "airtight"),
}


def contrast_claim(gid: int, away: str, home: str, fp: dict, places: dict, skip: set) -> dict:
    """A trait where the two teams sit far apart. Informational: nothing to grade."""
    k = max((k for k in CONTRAST if k not in skip), key=lambda k: abs(fp[away]["dims"][k]["blend"]["pct"] - fp[home]["dims"][k]["blend"]["pct"]))
    name, lo, hi = CONTRAST[k]
    a, h = fp[away]["dims"][k]["blend"], fp[home]["dims"][k]["blend"]
    top, bot = (away, home) if a["pct"] >= h["pct"] else (home, away)
    rt, rb = (a, h) if top == away else (h, a)
    w = {"name": name, "top": places[top], "bot": places[bot], "rt": _ordinal(rt["rank"]), "rb": _ordinal(rb["rank"]), "hi": hi, "lo": lo}
    return {
        "kind": "contrast", "metric": k, "team": top, "opp": bot,
        "head": _cap(_pick(CONTRAST_HEADS[k], gid, k).format(**w)),
        "body": _pick(CONTRAST_BODIES, gid, k, "body").format(**w),
        "cite": f"Style fingerprint · this season blended with last · {fp[away]['games']} and {fp[home]['games']} games this season",
        "check": None,
    }


def _cap(t: str) -> str:
    return t[0].upper() + t[1:]


def tempo_claims(gid: int, away: str, home: str, fp: dict, places: dict) -> list[dict]:
    """Calls about the game as a whole: pace (both teams fast or both slow) and physicality (both heavy hitters)."""
    out = []
    cite = f"Style fingerprint · this season blended with last · {fp[away]['games']} and {fp[home]['games']} games this season"
    a, h = fp[away]["dims"]["pace"]["blend"], fp[home]["dims"]["pace"]["blend"]
    for kind, both, direction in (("fast", a["pct"] >= BOTH and h["pct"] >= BOTH, "above"), ("slow", a["pct"] <= 100 - BOTH and h["pct"] <= 100 - BOTH, "below")):
        if both:
            heads, body = PACE_WORDS[kind]
            league = a["v"] * 100 / a["index"]
            out.append({
                "kind": "pace", "metric": "pace", "team": away, "opp": home,
                "head": _pick(heads, gid, "pace"),
                "body": body.format(A=places[away], H=places[home], av=f"{a['v']:.1f} unblocked shots per 60 at 5-on-5, both teams combined",
                                    hv=f"{h['v']:.1f}", ar=_ordinal(a["rank"]), hr=_ordinal(h["rank"])),
                "cite": cite,
                "check": {"metric": "pace", "measure": "unblocked shot attempts per 60 at 5-on-5, both teams combined, adjusted for score and venue",
                          "team": None, "baseline": round((a["v"] + h["v"]) / 2, 2), "league": round(league, 2), "direction": direction,
                          "rule": f"held if tonight's pace is {direction} the two teams' average; partly if {direction} the league average; otherwise didn't happen"},
            })
    a, h = fp[away]["dims"]["physical"]["blend"], fp[home]["dims"]["physical"]["blend"]
    if a["pct"] >= BOTH and h["pct"] >= BOTH:
        out.append({
            "kind": "physical", "metric": "physical", "team": away, "opp": home,
            "head": _pick(PHYSICAL_HEADS, gid, "physical"),
            "body": f"{places[away]}’s hit score is {a['index']} ({_ordinal(a['rank'])} of 32) and {places[home]}’s is {h['index']} ({_ordinal(h['rank'])}). "
                    f"100 is league average, after adjusting for each arena’s scorer.",
            "cite": cite,
            "check": {"metric": "physical", "measure": "hits per 60 in close games, both teams, as a score against league average (arena-adjusted)",
                      "team": None, "baseline": round((a["index"] + h["index"]) / 2, 1), "league": 100, "direction": "above",
                      "rule": "held if tonight's combined hit score is at or above the two teams' average; partly if above 100; otherwise didn't happen"},
        })
    return out


def calls(gid: int, away: str, home: str, fp: dict, lines: dict, places: dict) -> list[dict]:
    """The two to four "What to watch" calls for one game, from fingerprints and lines as they stood before it."""
    claims = clash_claims(gid, away, home, fp, places)
    claims += tempo_claims(gid, away, home, fp, places)
    mc = matchup_claim(gid, away, home, lines, places)
    if mc:
        claims.append(mc)
    claims = claims[:4]
    while len(claims) < 2:
        claims.append(contrast_claim(gid, away, home, fp, places, {c["metric"] for c in claims}))
    for i, c in enumerate(claims):
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


def snapshot(g: dict, site: dict, places: dict, sched: list[dict], now: datetime, **extra) -> dict:
    away, home = g["away"], g["home"]
    claims = calls(g["id"], away, home, site["fingerprints"]["teams"], site["lines"]["teams"], places)
    return {"game_id": g["id"], "start": g["start"], "away": away, "home": home, "venue": g["venue"],
            "snapshot_at": now.isoformat(timespec="seconds"), "data_as_of": site["fingerprints"]["generated_at"], **extra,
            "claims": claims, "pregame": bundle(away, home, site, records(sched, g["start"]))}


def write_index(out_dir) -> int:
    index = []
    for p in sorted(q for q in out_dir.glob("*.json") if q.name != "index.json"):
        s = json.loads(p.read_text())
        index.append({"id": s["game_id"], "start": s["start"], "away": s["away"], "home": s["home"], "claims": len(s["claims"]),
                      "rebuilt": bool(s.get("rebuilt"))})
    (out_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return len(index)


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    site = site_files(SITE_DATA)
    out_dir = SITE_DATA / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = frozen = 0
    for g in sched:
        start = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        path = out_dir / f"{g['id']}.json"
        if start <= now:
            frozen += path.exists()
            continue  # started or finished: an existing snapshot stays exactly as it was at puck drop
        if g["final"] or start > now + timedelta(days=DAYS_AHEAD):
            continue
        if g["away"] not in site["fingerprints"]["teams"] or g["home"] not in site["fingerprints"]["teams"]:
            continue
        path.write_text(json.dumps(snapshot(g, site, places, sched, now), separators=(",", ":"), ensure_ascii=False))
        written += 1
    return {"written": written, "frozen": frozen, "indexed": write_index(out_dir)}
