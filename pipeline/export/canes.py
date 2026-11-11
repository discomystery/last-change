"""The Hurricanes page (unlinked, for Sora): how Hurricane-y every skater is, and how much each club collects a type.

Read from the player JSON written by `players.run` and the career files written by `ledger.run`; writes `canes.json`.

The Carolina profile is the games-weighted average of Carolina's current skaters' style percentiles (this season and
last, "blend"), forwards and defensemen apart. Style only, as on the Compare page: how a player plays, not how good he
is, so play-driving ratings, power-play ratings and finishing are left out. Offensive-zone time is shown but not
scored: every Carolina skater sits near the top of it, which is the system, not the player.

A trait counts when Carolina's average is 8+ points from the middle. Its weight grows with how far from the middle the
Canes sit (in league spreads) and with how alike they are on it (league spread over Carolina's, capped 0.5-2). A player
loses points mostly for falling short of Carolina's level in Carolina's direction (shooting more than the Canes do is
still Hurricane-y); the other style traits count a little, both ways. The score is the player's place among every regular at his position (100 = the closest fit).

Type-y-ness: for each club, how tightly its regulars sit around their own team average on the same style traits,
compared with the same players dealt to random clubs (`SHUFFLES` deals, same roster sizes).
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone

from pipeline.config import SITE_DATA
from pipeline.export.players import OUT

TEAM = "CAR"
TRAITS = ["shooting", "chances", "playmaking", "shThreat", "hits", "blocks", "takeaways", "drawsPenalties", "discipline", "faceoffs"]
EDGE = ["topSpeed", "bursts", "shotSpeed", "distance"]
SYSTEM = ["oz"]  # shown, never scored
MIN_GAMES = 30  # this season and last, to be scored
MIN_GAP = 8  # points from the middle for a trait to define the type
BEYOND = 0.25  # cost of going past Carolina on a defining trait, against falling short
OTHER = 0.12  # weight of a style trait that does not define the type
MIN_WEIGHT = 0.7  # share of the profile's weight a player must be rated on
LEAGUE_SD = 28.9  # spread of a percentile scale (uniform 0-100)
SHUFFLES = 400
SEED = 2026


def _features(p: dict) -> dict[str, float]:
    f: dict[str, float] = {}
    for k in TRAITS:
        t = ((p.get("traits") or {}).get(k) or {}).get("blend") or {}
        if t.get("ok") and t.get("pct") is not None:
            f[k] = t["pct"]
    for k in EDGE + SYSTEM:
        t = (((p.get("edge") or {}).get("blend")) or {}).get(k) or {}
        if t.get("ok") and t.get("pct") is not None:
            f["e_" + k] = t["pct"]
    return f


def _carolina_seasons(pid: int) -> list[int]:
    path = SITE_DATA / "careers" / f"{pid}.json"
    if not path.exists():
        return []
    d = json.loads(path.read_text())
    cols = d.get("cols") or []
    if not {"yr", "gt", "team"} <= set(cols):
        return []
    i, g, t = cols.index("yr"), cols.index("gt"), cols.index("team")
    return sorted({r[i] for r in d.get("rows") or [] if r[t] == TEAM and r[g] == 2})


def load() -> list[dict]:
    players = []
    for path in OUT.glob("*.json"):
        if not path.stem.isdigit():
            continue
        p = json.loads(path.read_text())
        if p.get("group") not in ("F", "D") or not p.get("traits") or p.get("unplayed") or not p.get("team"):
            continue
        st = p.get("stats") or {}
        games = sum((st.get(k) or {}).get("gp", 0) for k in ("season", "last"))
        players.append({"id": p["id"], "name": p["name"], "team": p["team"], "group": p["group"], "pos": p.get("pos"),
                        "age": p.get("age"), "games": games, "f": _features(p)})
    return players


def profile(players: list[dict], group: str) -> dict[str, dict]:
    """Carolina's average, its spread and the trait's weight, for every style trait."""
    car = [p for p in players if p["team"] == TEAM and p["group"] == group and p["games"] >= MIN_GAMES]
    out: dict[str, dict] = {}
    for k in [*TRAITS, *("e_" + e for e in EDGE + SYSTEM)]:
        vals = [(p["f"][k], p["games"]) for p in car if k in p["f"]]
        if len(vals) < 4:
            continue
        w_sum = sum(w for _, w in vals)
        m = sum(x * w for x, w in vals) / w_sum
        sd = math.sqrt(sum(w * (x - m) ** 2 for x, w in vals) / w_sum)
        scored = k[2:] not in SYSTEM and abs(m - 50) >= MIN_GAP
        weight = abs(m - 50) / LEAGUE_SD * min(2.0, max(0.5, LEAGUE_SD / max(sd, 1.0))) if scored else 0.0
        out[k] = {"m": round(m, 1), "sd": round(sd, 1), "n": len(vals), "w": round(weight, 3), "dir": 1 if m >= 50 else -1}
    return out


def shortfall(p: dict, prof: dict[str, dict]) -> float | None:
    """Weighted distance from Carolina's level, in league spreads (0 = the Carolina average on every trait).

    On a defining trait, going past Carolina in Carolina's direction costs only `BEYOND` as much as falling short, so a
    bigger shooter than the Canes still fits; the other style traits count a little (`OTHER`), both ways, so that among
    players who clear every defining bar the ones who otherwise play like the Canes come first."""
    num = den = total = 0.0
    for k, t in prof.items():
        if k[2:] in SYSTEM:
            continue
        w = t["w"] or OTHER * min(2.0, max(0.5, LEAGUE_SD / max(t["sd"], 1.0)))
        total += w
        if k not in p["f"]:
            continue
        gap = (p["f"][k] - t["m"]) * t["dir"] / LEAGUE_SD
        if t["w"] and gap > 0:
            gap *= BEYOND
        num += w * gap ** 2
        den += w
    if not total or den < MIN_WEIGHT * total:
        return None
    return math.sqrt(num / den)


def _why(p: dict, prof: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Defining traits he matches best (at or beyond Carolina's level) and where he falls furthest short."""
    near, far = [], []
    for k, t in prof.items():
        if t["w"] and k in p["f"]:
            gap = (p["f"][k] - t["m"]) * t["dir"]
            (near if gap >= 0 else far).append((gap * t["w"], gap, k))
    return [k for _, _, k in sorted(near, reverse=True)[:3]], [k for _, gap, k in sorted(far)[:2] if gap < -10]


def score(players: list[dict]) -> tuple[dict, list[dict]]:
    profiles = {g: profile(players, g) for g in ("F", "D")}
    rows = []
    for g in ("F", "D"):
        prof = profiles[g]
        pool = [(s, p) for p in players if p["group"] == g and p["games"] >= MIN_GAMES and (s := shortfall(p, prof)) is not None]
        shorts = sorted(s for s, _ in pool)
        for s, p in pool:
            worse = sum(x > s for x in shorts)
            same = sum(x == s for x in shorts) - 1
            pct = round(100 * (worse + same / 2) / max(1, len(shorts) - 1))
            near, far = _why(p, prof)
            rows.append({"id": p["id"], "name": p["name"], "team": p["team"], "group": g, "pos": p["pos"], "age": p["age"],
                         "gp": p["games"], "score": pct, "short": round(s, 3), "near": near, "far": far,
                         "v": {k: p["f"][k] for k in prof if k in p["f"]}})
    rows.sort(key=lambda r: (-r["score"], r["short"], r["name"]))
    return profiles, rows


def _spread(members: list[dict], keys: list[str]) -> float:
    """Games-weighted mean squared distance of a club's players from their own average."""
    tot = sum(p["games"] for p in members)
    d = 0.0
    for k in keys:
        vals = [(p["f"].get(k, 50.0), p["games"]) for p in members]
        m = sum(x * w for x, w in vals) / tot
        d += sum(w * (x - m) ** 2 for x, w in vals) / tot
    return d / len(keys)


def typey(players: list[dict], shuffles: int = SHUFFLES, seed: int = SEED) -> list[dict]:
    """Each club's style spread against random clubs made of the same players. Tightness > 0 = more alike than chance."""
    rng = random.Random(seed)
    out: dict[str, dict] = {}
    for g in ("F", "D"):
        keys = [k for k in TRAITS if k != "faceoffs" or g == "F"] + ["e_" + e for e in EDGE]
        pool = [p for p in players if p["group"] == g and p["games"] >= MIN_GAMES]
        teams = sorted({p["team"] for p in pool})
        sizes = {t: sum(p["team"] == t for p in pool) for t in teams}
        observed = {t: _spread([p for p in pool if p["team"] == t], keys) for t in teams if sizes[t] >= 3}
        null: dict[str, list[float]] = {t: [] for t in observed}
        for _ in range(shuffles):
            deck = pool[:]
            rng.shuffle(deck)
            i = 0
            for t in teams:
                chunk = deck[i:i + sizes[t]]
                i += sizes[t]
                if t in observed:
                    null[t].append(_spread(chunk, keys))
        for t, obs in observed.items():
            exp = sum(null[t]) / len(null[t])
            beat = sum(x > obs for x in null[t]) / len(null[t])  # share of random clubs more spread out
            row = out.setdefault(t, {"team": t})
            row[g] = {"tight": round(1 - obs / exp, 3), "beat": round(beat, 3), "n": sizes[t]}
    rows = list(out.values())
    for r in rows:
        parts = [r[g]["tight"] for g in ("F", "D") if g in r]
        r["tight"] = round(sum(parts) / len(parts), 3)
    rows.sort(key=lambda r: -r["tight"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def run() -> int:
    players = load()
    profiles, rows = score(players)
    ex = {}
    for r in rows:
        if r["team"] != TEAM and (yrs := _carolina_seasons(r["id"])):
            ex[r["id"]] = yrs
    for r in rows:
        if r["id"] in ex:
            r["ex"] = [ex[r["id"]][0], ex[r["id"]][-1]]
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "profiles": profiles,
           "players": rows, "typey": typey(players), "of": {g: sum(r["group"] == g for r in rows) for g in ("F", "D")}}
    (SITE_DATA / "canes.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    return len(rows)
