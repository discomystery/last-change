"""PROTOTYPE (Sora, 2026-10-29, thread "Design post-game line matchup visuals"): one file per chosen game for the
lab page that tries three ways to show who played against whom. Not part of the nightly run.

`segs` is the game cut into stretches where nobody changed, with each side's skaters and the strength; `r` is each
skater's 5-on-5 rating going in (creating plus preventing chances, xG per 60, the player pages' blended ratings);
`cuts` are the league's quintiles of a forward trio's, a defense pair's and a five's summed rating, weighted by
5-on-5 time over every game this season, so a group can be shown as one to five pucks."""
import json
import sys

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, SITE_DATA, TABLES

OUT = SITE_DATA / "lab" / "linematch"


def ratings() -> dict[int, float]:
    out = {}
    for f in (SITE_DATA / "players").glob("*.json"):
        try:
            t = json.loads(f.read_text())["traits"]
            out[int(f.stem)] = round(t["offImpact"]["blend"]["v"] + t["defImpact"]["blend"]["v"], 3)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def strength(r) -> str:
    nh, na = r["n_home"], r["n_away"]
    gh, ga = r["home_goalie"] is not None, r["away_goalie"] is not None
    if not (gh and ga):
        return "EN"
    if nh == na:
        return f"{nh}v{na}"
    return "PP1" if nh > na else "PP0"   # PP1: home on the power play


def cuts(st: pl.DataFrame, pos: dict[int, str], r: dict[int, float]) -> dict:
    trio, pair, five = [], [], []
    for row in st.filter((pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null()).iter_rows(named=True):
        for side in ("home_skaters", "away_skaters"):
            ps = row[side] or []
            f = [p for p in ps if pos.get(p) != "D"]
            d = [p for p in ps if pos.get(p) == "D"]
            w = row["duration"]
            if len(f) == 3:
                trio.append((sum(r.get(p, 0) for p in f), w))
            if len(d) == 2:
                pair.append((sum(r.get(p, 0) for p in d), w))
            five.append((sum(r.get(p, 0) for p in ps), w))
    def q(xs):
        v = np.array([x for x, _ in xs]); w = np.array([w for _, w in xs], float)
        o = np.argsort(v); c = np.cumsum(w[o]) / w.sum()
        return [round(float(v[o][np.searchsorted(c, k)]), 3) for k in (0.2, 0.4, 0.6, 0.8)]
    return {"trio": q(trio), "pair": q(pair), "five": q(five)}


def run(gids: list[int], season: int = CURRENT_SEASON):
    d = TABLES / str(season)
    st = pl.read_parquet(d / "stints.parquet")
    pos = {r["player_id"]: r["pos"] for r in pl.read_parquet(d / "players.parquet").iter_rows(named=True)}
    r = ratings()
    league = cuts(st, pos, r)
    OUT.mkdir(parents=True, exist_ok=True)
    for gid in gids:
        flow = json.loads((SITE_DATA / "flow" / f"{gid}.json").read_text())
        segs = [[row["start"], row["end"], strength(row), row["away_skaters"] or [], row["home_skaters"] or []]
                for row in st.filter(pl.col("game_id") == gid).sort("start").iter_rows(named=True) if row["end"] > row["start"]]
        players = [{**{k: p[k] for k in ("id", "n", "h", "grp")}, "d": pos.get(p["id"]) == "D", "r": r.get(p["id"], 0.0)} for p in flow["players"]]
        out = {"game_id": gid, "len": flow["len"], "segs": segs, "players": players, "shots": [s[:8] + [s[8]] for s in flow["shots"]],
               "pp": flow["pp"], "cuts": league}
        (OUT / f"{gid}.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        print(gid, len(segs), "stretches")


if __name__ == "__main__":
    run([int(g) for g in sys.argv[1:]])
