"""Every graded preview call kind, replayed over whole seasons, against what chance alone gives.

For each regular-season game, the morning's team fingerprints are rebuilt from earlier games only (bench/fingerprints)
and handed to the real `previews.calls`, which picks the two to four calls a preview would make; each is graded by the
real `recaps.measure` from that game's numbers. So a change to a threshold, a priority or a grading rule shows up
here exactly as it would on the site.

"By chance" makes the same check with the reason taken away, through the same code with its bars opened:
- clash: any team, at least its usual figure tonight (whatever the opponent's defense)
- duel: the same lean (the 2023-25 regression) in any game, not only strength against strength
- edge and control: the team ranked higher on the trait comes out ahead by half the usual gap, in any game
- pace: any game runs faster than the faster team's usual (slower than the slower team's)
- physical: any game reaches the two teams' usual hit score
- changed: any team with enough games lands on this season's side of its blended level, changed or not
- rest: any team beats its usual share of the chances, rested or not

Not on the bench: matchup calls (they need each day's lines) and goalie history (called a coincidence on purpose);
their live record this season is listed from the post-game pages.

Partly counts as half a hold. Arena factors, score weights and the chance model are fitted on 2023-24 to 2025-26, so
those seasons are not fully unseen (a small effect, noted in each study since October).
"""
import contextlib
import copy
import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone

from pipeline.config import CURRENT_SEASON, SITE_DATA
from pipeline.export import previews, recaps
from pipeline.bench.fingerprints import Replay, fidelity

SEASONS = (CURRENT_SEASON - 2, CURRENT_SEASON - 1, CURRENT_SEASON)
VERDICTS = ("held", "partly", "missed", "na")


@contextlib.contextmanager
def opened(**bars):
    """Set previews' module-level bars for a moment (the by-chance versions of each rule)."""
    old = {k: getattr(previews, k) for k in bars}
    for k, v in bars.items():
        setattr(previews, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(previews, k, v)


def label(c: dict) -> str:
    """What a call is about, finer than its kind: the trait, or for pace which way."""
    if c["kind"] == "pace":
        return "fast" if c["check"]["direction"] == "above" else "slow"
    if c["kind"] == "duel":
        return f"{c['metric']} {c.get('lean', '')}".strip()
    return c["metric"]


def _open_changed(fp: dict) -> dict:
    """The fingerprints with every team's season-only likely range moved out of the way, so the "changed" rule fires
    whenever a team has played enough games, not only when it has really changed."""
    out = copy.deepcopy(fp)
    for e in out.values():
        for k in previews.CHANGED:
            s = e["dims"].get(k, {}).get("season")
            if s and s.get("v") is not None:
                s["lo"] = s["hi"] = -1
    return out


def by_chance(gid: int, away: str, home: str, fp: dict, fp_open: dict, places: dict, stab: dict, shares: dict) -> list[dict]:
    out = []
    for x in (away, home):
        for m in ("volume", "quality", "pp"):
            o = fp[x]["dims"][m]["blend"]
            if o.get("v") is None:
                continue
            out.append({"kind": "clash", "metric": m, "team": x, "opp": home if x == away else away,
                        "check": {"metric": m, "team": x, "baseline": o["v"], "league": o["v"] * 100 / o["index"] if o.get("index") else None,
                                  "direction": "above"}})
        if x in shares:
            out.append({"kind": "rest", "metric": "rest", "team": x,
                        "check": {"metric": "rest", "team": x, "baseline": shares[x], "league": 0.5, "direction": "above"}})
    with opened(DUEL_BAR=0):
        out += previews.duel_claims(gid, away, home, fp, places)
    for k in previews.EDGE:
        if k == "point":
            continue
        c = previews.edge_claim(gid, away, home, fp, places, set(previews.EDGE) - {k})
        if c:
            out.append(c)
    with opened(EDGE_GAP=0, EDGE_END=50):
        c = previews.control_claim(gid, away, home, places, shares)
        if c:
            out.append(c)
    with opened(BOTH=0):
        out += previews.tempo_claims(gid, away, home, fp, places)
    with opened(CHANGED_GAP=0, CHANGED_MOVE=0):
        out += previews.changed_claims(gid, away, home, fp_open, places, stab)
    return out


class Tally:
    def __init__(self):
        self.n = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))  # [kind][label][season or "all"] -> verdict counts

    def add(self, season: int, c: dict, verdict: str | None):
        v = verdict if verdict in VERDICTS else "na"
        for lab in ("all", label(c)):
            for s in (str(season), "all"):
                self.n[c["kind"]][lab][s][v] += 1
                self.n[c["kind"]][lab][s]["calls"] += 1

    def out(self) -> dict:
        return {k: {lab: {s: _rate(dict(v)) for s, v in seasons.items()} for lab, seasons in labs.items()} for k, labs in self.n.items()}


def _rate(v: dict) -> dict:
    graded = v.get("held", 0) + v.get("partly", 0) + v.get("missed", 0)
    return {**{k: v.get(k, 0) for k in ("calls", *VERDICTS)}, "graded": graded,
            "rate": round((v.get("held", 0) + 0.5 * v.get("partly", 0)) / graded, 4) if graded else None}


def season_run(season: int, places: dict, pub: Tally, cand: Tally, chance: Tally, per_game: list, progress: bool = False) -> dict:
    t0 = time.time()
    R = Replay(season)
    S = recaps.Season(season)
    sched = sorted(({"id": g["game_id"], "date": g["date"], "home": g["home"], "away": g["away"]} for g in S.games.values()), key=lambda g: (g["date"], g["id"]))
    rest_days = previews.rest_days
    by_day = defaultdict(list)
    for g in sched:
        by_day[g["date"]].append(g)
    stab = R.k
    games = 0
    for day in sorted(by_day):
        fp = R.on(day)
        fp_open = None
        shares = previews.league_shares(day, season)
        for g in by_day[day]:
            gid, away, home = g["id"], g["away"], g["home"]
            if away not in fp or home not in fp or away not in places or home not in places:
                continue
            if (gid, S.games[gid]["away_id"]) not in S.rows:
                continue  # numbers not built for this game
            rest = rest_days(sched, g)
            rested = [t for t in (away, home) if rest.get(t, {}).get("days", 0) >= 1 and any(rest.get(o, {}).get("days") == 0 for o in (away, home) if o != t)]
            extra = {"stab": stab, "shares": shares, "rest": rest, "rest_usual": shares.get(rested[0]) if rested else None}
            claims = previews.calls(gid, away, home, fp, {}, places, extra)
            line = {"season": season, "game": gid, "calls": []}
            for c in claims:
                v = recaps.measure(S, gid, c, places, fp).get("verdict")
                pub.add(season, c, v)
                line["calls"].append([c["kind"], label(c), v])
            per_game.append(line)
            # every call the rules would make before the four-call limit (what a threshold change moves first)
            for c in candidates(gid, away, home, fp, places, extra):
                cand.add(season, c, recaps.measure(S, gid, c, places, fp).get("verdict"))
            if fp_open is None:
                fp_open = _open_changed(fp)
            for c in by_chance(gid, away, home, fp, fp_open, places, stab, shares):
                chance.add(season, c, recaps.measure(S, gid, c, places, fp).get("verdict"))
            games += 1
        if progress:
            print(f"  {season} {day}: {games} games, {time.time() - t0:.0f}s", flush=True)
    last = max(by_day) if by_day else None
    return {"games": games, "seconds": round(time.time() - t0), "through": last}


def candidates(gid: int, away: str, home: str, fp: dict, places: dict, extra: dict) -> list[dict]:
    """The calls each rule would make in this game, before priorities and the four-call limit."""
    out = previews.duel_claims(gid, away, home, fp, places)
    out += previews.changed_claims(gid, away, home, fp, places, extra.get("stab") or {})
    rc = previews.rest_claim(gid, away, home, places, extra.get("rest") or {}, extra.get("rest_usual"))
    cc = previews.control_claim(gid, away, home, places, extra.get("shares"))
    out += [c for c in (rc, cc) if c]
    out += previews.clash_claims(gid, away, home, fp, places)
    out += previews.tempo_claims(gid, away, home, fp, places)
    ec = previews.edge_claim(gid, away, home, fp, places, set(), hot_only=True)
    return out + ([ec] if ec else [])


def _sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def run(seasons=SEASONS, progress: bool = False) -> dict:
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    pub, cand, chance, per_game = Tally(), Tally(), Tally(), []
    info = {}
    for s in seasons:
        info[str(s)] = season_run(s, places, pub, cand, chance, per_game, progress)
    fid = None
    saved = SITE_DATA / "fingerprints.json"
    if saved.exists() and CURRENT_SEASON in seasons:
        fp_saved = json.loads(saved.read_text())
        R = Replay(CURRENT_SEASON)
        day = fp_saved["generated_at"][:10]
        fid = {"day": day, **fidelity(R.on(day), fp_saved, fp_saved["ready"])}
    live = None
    idx = SITE_DATA / "recaps" / "index.json"
    if idx.exists():
        live = {k: _rate({**v, "calls": sum(v.values())}) for k, v in json.loads(idx.read_text()).get("by_kind", {}).items()}
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": _sha(),
            "rules": previews.RULES_VERSION, "seasons": info, "published": pub.out(), "candidates": cand.out(), "chance": chance.out(),
            "live": live, "fidelity": fid, "games": per_game}
