"""Track record: how the previews' calls and the win chances have held up, for the Track record page.

Reads the post-game files written by `recaps`, so it runs after it. Every graded call counts, whether its preview was
saved before puck drop or rebuilt afterwards from the games before it (the user's decision: the difference isn't
relevant to fans).

Win chances are never saved before a game. The post-game files carry the chance worked out afterwards from what was
known that morning, and this scores those against the results, beside the same model tested on the three seasons it
was built from (each season predicted by a model fitted on the other two).
"""
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, SITE_DATA
from pipeline.metrics import win_model

VERDICTS = ("held", "partly", "missed")
LATEST = 40  # most recent graded calls listed on the page
Z80 = 1.2816
BANDS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01))  # as in win_model.score


def _count() -> dict:
    return {v: 0 for v in VERDICTS}


def week_of(day: str) -> str:
    """Monday of the day's week, so the season builds up in weekly columns."""
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def call_record(recaps: list[dict]) -> dict:
    """Tallies overall, by kind of call and by week, plus the latest graded calls."""
    s = {"tally": _count(), "kinds": defaultdict(_count), "weeks": defaultdict(_count), "games": 0, "latest": []}
    for r in sorted(recaps, key=lambda r: (r["start"], r["game_id"])):
        graded = [c for c in r["calls"] if c.get("verdict") in VERDICTS]
        if graded:
            s["games"] += 1
        for c in graded:
            v = c["verdict"]
            s["tally"][v] += 1
            s["kinds"][c["kind"]][v] += 1
            s["weeks"][week_of(r["date"])][v] += 1
            s["latest"].append({"game_id": r["game_id"], "date": r["date"], "away": r["away"], "home": r["home"], "kind": c["kind"],
                                "head": c["head"], "call": c.get("call"), "verdict": v, "usual": c.get("usual"), "tonight": c.get("tonight")})
    s["kinds"] = dict(s["kinds"])
    s["weeks"] = [{"week": w, **n} for w, n in sorted(s["weeks"].items())]
    s["latest"] = s["latest"][::-1][:LATEST]
    return s


def _interval(k: int, n: int) -> tuple[float, float]:
    """80% Wilson range for a share of k in n."""
    p, z2 = k / n, Z80 ** 2
    mid = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z80 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, mid - half), min(1.0, mid + half)


def scored(p_home: np.ndarray, home_won: np.ndarray) -> dict:
    """How often the team given the better chance won, against picking the home team every time, and a calibration
    table (chance given to the favourite against how often it won, with an 80% range for the latter)."""
    s = win_model.score(p_home, home_won)
    fav = np.where(p_home >= 0.5, p_home, 1 - p_home)
    won = np.where(p_home >= 0.5, home_won, 1 - home_won)
    bands = []
    for lo, hi in BANDS:
        sel = (fav >= lo) & (fav < hi)
        n, k = int(sel.sum()), int(won[sel].sum())
        if n:
            r_lo, r_hi = _interval(k, n)
            bands.append({"band": f"{lo:.0%}-{hi:.0%}" if hi < 1 else f"{lo:.0%}+", "games": n, "said": round(float(fav[sel].mean()), 3),
                          "won": round(k / n, 3), "lo": round(r_lo, 3), "hi": round(r_hi, 3)})
    return {"games": s["games"], "picked": int(round(s["hit_rate"] * s["games"])), "hit_rate": round(s["hit_rate"], 4),
            "home_rate": round(float(home_won.mean()), 4), "brier": round(s["brier"], 4), "bands": bands}


def past_seasons() -> dict:
    """The morning model on the seasons it was built from, each predicted by a model fitted on the other two."""
    df = win_model.pregame(win_model.team_games(FULL_SEASONS))
    p, y = [], []
    for test in FULL_SEASONS:
        te = df.filter(df["season"] == test)
        m = win_model.fit(df.filter(df["season"].is_in([s for s in FULL_SEASONS if s != test])))
        p.append(win_model.predict(m, te))
        y.append(te["home_win"].to_numpy())
    return {"seasons": FULL_SEASONS, **scored(np.concatenate(p), np.concatenate(y))}


def win_record(recaps: list[dict]) -> dict | None:
    rows = [r for r in recaps if r.get("reveal")]
    if not rows:
        return None
    p = np.array([r["reveal"]["p_home"] for r in rows])
    y = np.array([1 if r["score"][r["home"]] > r["score"][r["away"]] else 0 for r in rows])
    out = scored(p, y)
    puck = [(r["reveal"]["p_home_puck"], yy) for r, yy in zip(rows, y) if r["reveal"].get("p_home_puck") is not None]
    if puck:
        pp, py = np.array([a for a, _ in puck]), np.array([b for _, b in puck])
        out["puck"] = {"games": len(puck), "picked": int(((pp >= 0.5) == (py == 1)).sum())}
    return out


def run(season: int = CURRENT_SEASON) -> dict:
    d = SITE_DATA / "recaps"
    recaps = [json.loads(p.read_text()) for p in sorted(d.glob("2*.json"))]
    recaps = [r for r in recaps if int(str(r["game_id"])[:4]) == season]
    calls = call_record(recaps)
    snaps = [json.loads(p.read_text()) for p in (SITE_DATA / "previews").glob("2*.json")]
    done = {r["game_id"] for r in recaps}
    try:
        past = past_seasons()
    except Exception as e:  # the page still works without the comparison
        print(f"win chance backtest unavailable: {e!r}")
        past = None
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season,
           "waiting": sum(1 for s in snaps if s["game_id"] not in done), "calls": calls, "win": {"season": win_record(recaps), "past": past}}
    (SITE_DATA / "track.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    return {"graded": sum(calls["tally"].values())}


if __name__ == "__main__":
    print(json.dumps(run()))
