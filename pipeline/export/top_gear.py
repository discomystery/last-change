"""Top gear: how often a skater reaches each speed, per 60 minutes of his ice time (player page, Skating and shot).

From NHL EDGE's skating-speed detail saved beside each skater's EDGE file (`speed` in ingest/edge.py): how many
times he skated 18-20, 20-22 and 22+ mph, and his ten fastest skates. Together they give a falling curve of
"times per 60 at or above this speed": 18, 20 and 22 mph from the bands, then his k-th fastest skate reached k times.
The headline is the speed he reaches about once every 60 minutes, read off that curve (log scale between points),
which says more than a top speed reached once. Percentiles and the league band (10th, 50th and 90th percentile of the
curve at each speed) are among regulars at his position, as for the other EDGE cards. Modes follow the Seasons
setting: this season, or this season and last pooled (counts and ice time added, the ten fastest of both).

Inspired by Post Hockey's hourly burst chart (Sora, 2026-10-31); built from EDGE counts alone, with no fitted shape.
"""
import json
import math

import numpy as np

from pipeline.config import SITE_DATA
from pipeline.ingest import edge

OUT = SITE_DATA / "top_gear.json"  # the league bands, read at build time only
GRID = [round(18 + 0.25 * i, 2) for i in range(27)]  # 18 to 24.5 mph
FLOOR = 0.05  # times per 60 the chart bottoms out at
TAIL = 0.6  # past his fastest skate the curve drops a hundredfold over this many mph (for the league band only)


def _season(season: int, pid: int) -> dict | None:
    e = edge.read(season, pid)
    sp = (e or {}).get("speed")
    if not sp or sp.get("b18") is None:
        return None
    return {"n18": (sp["b18"] or 0) + (sp["b20"] or 0) + (sp["b22"] or 0), "n20": (sp["b20"] or 0) + (sp["b22"] or 0), "n22": sp["b22"] or 0, "top": sp.get("top") or []}


def _pool(parts: list[tuple[dict, float]]) -> tuple[dict, float] | None:
    parts = [(p, h) for p, h in parts if p and h]
    if not parts:
        return None
    return ({k: sum(p[k] for p, _ in parts) for k in ("n18", "n20", "n22")} | {"top": sorted((t for p, _ in parts for t in p["top"]), reverse=True)[:10]},
            sum(h for _, h in parts))


def curve(p: dict, hours: float) -> list[tuple[float, float]]:
    """Times per 60 at or above each speed: the three bands, then his ten fastest skates, never rising."""
    pts = [(18.0, p["n18"] / hours), (20.0, p["n20"] / hours), (22.0, p["n22"] / hours)]
    tops = [(s, (k + 1) / hours) for k, s in enumerate(p["top"])]
    fastest = tops[0][0] if tops else 22.0
    out, last = [], math.inf
    for s, r in sorted([q for q in pts if q[1] > 0 and q[0] < fastest] + tops):
        r = min(r, last)
        if r > 0:
            out.append((s, r))
            last = r
    return out


def at(c: list[tuple[float, float]], v: float) -> float:
    """The curve's rate at speed v (log-linear between points; past his fastest skate it falls a hundredfold over TAIL mph)."""
    if not c:
        return 0.0
    if v <= c[0][0]:
        return c[0][1]
    ext = c + [(c[-1][0] + TAIL, c[-1][1] / 100)]
    for (s0, r0), (s1, r1) in zip(ext, ext[1:]):
        if s0 <= v <= s1:
            t = (v - s0) / (s1 - s0) if s1 > s0 else 0.0
            return 10 ** (math.log10(r0) + t * (math.log10(r1) - math.log10(r0)))
    return 0.0


def once_an_hour(c: list[tuple[float, float]]) -> float | None:
    """The speed he reaches about once every 60 minutes."""
    if not c or c[0][1] < 1:
        return None
    for (s0, r0), (s1, r1) in zip(c, c[1:]):
        if r0 >= 1 >= r1:
            return s0 if r0 == r1 else s0 + (s1 - s0) * -math.log10(r0) / (math.log10(r1) - math.log10(r0))
    return c[-1][0]  # still more than once an hour at his fastest skate: his fastest is the best we can say


def run(season: int, players: dict[int, dict], hours: dict[int, dict[str, float]], regular: dict[str, set[int]]) -> dict[int, dict]:
    """Top gear for every skater with speed data, per mode, with percentiles among regulars at his position; writes the
    league bands to top_gear.json. players: {pid: {"group": F/D}}; hours: {pid: {"now": h, "last": h}};
    regular: {mode: pids in the regulars' pool}."""
    curves: dict[str, dict[int, dict]] = {"season": {}, "blend": {}}
    for pid in players:
        h = hours.get(pid, {})
        now, last = (_season(season, pid), h.get("now")), (_season(season - 1, pid), h.get("last"))
        for mode, parts in (("season", [now]), ("blend", [now, last])):
            got = _pool(parts)
            if not got:
                continue
            p, hrs = got
            c = curve(p, hrs)
            if c:
                curves[mode][pid] = {"c": c, "p": p, "hours": hrs}
    out: dict[int, dict] = {}
    bands = {}
    for mode, cs in curves.items():
        bands[mode] = {}
        for g in ("F", "D"):
            pool = [pid for pid in cs if players[pid]["group"] == g and pid in regular[mode]]
            if not pool:
                continue
            band = []
            for v in GRID:
                vals = np.array([at(cs[pid]["c"], v) for pid in pool])
                p10, p50, p90 = np.percentile(vals, [10, 50, 90])
                if p90 < FLOOR:
                    break
                band.append([v, round(max(p10, FLOOR), 3), round(max(p50, FLOOR), 3), round(p90, 3)])
            hour_pool = np.array([x for x in (once_an_hour(cs[pid]["c"]) for pid in pool) if x is not None])
            c20_pool = np.array([cs[pid]["p"]["n20"] / cs[pid]["hours"] for pid in pool])
            bands[mode][g] = {"band": band, "hour": round(float(np.median(hour_pool)), 2) if len(hour_pool) else None,
                              "c20": round(float(np.median(c20_pool)), 2), "of": len(pool)}
            for pid, x in cs.items():
                if players[pid]["group"] != g:
                    continue
                hr, c20 = once_an_hour(x["c"]), x["p"]["n20"] / x["hours"]
                out.setdefault(pid, {})[mode] = {
                    "curve": [[round(s, 2), round(r, 3)] for s, r in x["c"]],
                    "marks": [[s, round(x["p"][k] / x["hours"], 2)] for s, k in ((18, "n18"), (20, "n20"), (22, "n22"))],
                    "hour": round(hr, 2) if hr is not None else None,
                    "hour_pct": round(100 * float((hour_pool < hr).mean())) if hr is not None and len(hour_pool) else None,
                    "c20": round(c20, 2), "c20_pct": round(100 * float((c20_pool < c20).mean())),
                    "hours": round(x["hours"], 1), "ok": pid in regular[mode]}
    OUT.write_text(json.dumps(bands, separators=(",", ":")))
    return out
