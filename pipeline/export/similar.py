"""Compare page suggestions: for every skater, the three skaters whose game looks most like his.

Read from the player JSON written by `players.run`. Style only: how a player plays (shooting or passing, hitting,
blocking, takeaways, penalties, faceoffs, skating, where his shots come from, power-play and penalty-kill minutes), not
how good he is, so ratings of quality (driving play, power-play impact, chances, finishing, points) are left out.
Percentiles are this season and last ("blend"). A player's unusual traits weigh more (up to 3x at the ends of a scale),
forwards are matched with forwards and defensemen with defensemen, and a center and a winger are pushed 4 points apart.
"""
from __future__ import annotations

import json
import math

from pipeline.export.players import OUT

TRAITS = ["shooting", "playmaking", "shThreat", "hits", "blocks", "takeaways", "drawsPenalties", "discipline", "faceoffs"]
EDGE = ["topSpeed", "bursts", "shotSpeed", "distance", "oz"]
CLOSE_AREAS = ("crease", "lowSlot", "netSideL", "netSideR")
WEIGHT = {"toi_pp": 1.2, "toi_pk": 1.2}
MIN_GAMES = 30  # this season and last together, for a player to be suggested
MIN_SHARED = 9  # features both players are rated on
CENTER_GAP = 4.0
N = 3


def _features(p: dict) -> tuple[dict[str, float], dict[str, float]]:
    """(percentiles from the page, raw values still to be ranked within the position group)."""
    f: dict[str, float] = {}
    for k in TRAITS:
        t = ((p.get("traits") or {}).get(k) or {}).get("blend") or {}
        if t.get("ok") and t.get("pct") is not None:
            f[k] = t["pct"]
    for k in EDGE:
        t = (((p.get("edge") or {}).get("blend")) or {}).get(k) or {}
        if t.get("ok") and t.get("pct") is not None:
            f["e_" + k] = t["pct"]
    raw: dict[str, float] = {}
    toi = ((p.get("role") or {}).get("toi")) or {}
    for k in ("toi5", "toi_pp", "toi_pk"):
        if k in toi:
            raw[k] = toi[k]
    a = ((p.get("areas") or {}).get("blend")) or {}
    if a.get("n", 0) >= 40:
        raw["close"] = sum((a["areas"].get(x) or {}).get("share", 0) for x in CLOSE_AREAS)
    return f, raw


def _rank(players: list[dict]) -> None:
    """Turn raw values into percentiles within each position group (mid-rank for ties)."""
    for grp in ("F", "D"):
        group = [p for p in players if p["group"] == grp]
        for k in ("toi5", "toi_pp", "toi_pk", "close"):
            vals = sorted(p["raw"][k] for p in group if k in p["raw"])
            for p in group:
                if k in p["raw"]:
                    v = p["raw"][k]
                    below = sum(x < v for x in vals)
                    same = sum(x == v for x in vals)
                    p["f"][k] = round(100 * (below + same / 2) / len(vals))


def distance(a: dict, b: dict) -> float | None:
    num = den = 0.0
    shared = 0
    for k, pa in a["f"].items():
        pb = b["f"].get(k)
        if pb is None:
            continue
        w = WEIGHT.get(k, 1.0) * (1 + 2 * abs(pa - 50) / 50)
        num += w * (pa - pb) ** 2
        den += w
        shared += 1
    if shared < MIN_SHARED:
        return None
    d = math.sqrt(num / den)
    if a["group"] == "F" and (a["pos"] == "C") != (b["pos"] == "C"):
        d += CENTER_GAP
    return d


def suggest(players: list[dict], n: int = N) -> dict[int, list[int]]:
    pool = [p for p in players if p["games"] >= MIN_GAMES]
    out: dict[int, list[int]] = {}
    for a in players:
        scored = [(d, b["id"]) for b in pool if b["id"] != a["id"] and b["group"] == a["group"] and (d := distance(a, b)) is not None]
        if scored:
            out[a["id"]] = [i for _, i in sorted(scored)[:n]]
    return out


def run() -> int:
    players = []
    for path in OUT.glob("*.json"):
        if not path.stem.isdigit():
            continue
        p = json.loads(path.read_text())
        if p.get("group") not in ("F", "D") or not p.get("traits") or p.get("unplayed"):
            continue
        st = p.get("stats") or {}
        games = sum((st.get(k) or {}).get("gp", 0) for k in ("season", "last"))
        f, raw = _features(p)
        players.append({"id": p["id"], "group": p["group"], "pos": p.get("pos"), "games": games, "f": f, "raw": raw})
    _rank(players)
    out = suggest(players)
    (OUT / "similar.json").write_text(json.dumps({str(k): v for k, v in sorted(out.items())}, separators=(",", ":")))
    return len(out)
