"""A map of shot chances for the Methods page's "drag a puck" explainer.

For each 3 ft square of the offensive zone and each common shot type, the average rating our own xG model gave real
5-on-5 shots taken there (three full seasons, empty nets and blocked shots out). A square with few shots of one type
leans on all shots from that square. Written once a run to `site/public/data/xg_grid.json` (a few KB)."""
import json

import polars as pl

from pipeline.config import FULL_SEASONS, SITE_DATA, TABLES

CELL = 3  # feet
X0, X1, Y1 = 25, 99, 42  # blue line to the end boards (x toward the net, which sits at x = 89), centre to the boards
TYPES = {"wrist": ["wrist"], "snap": ["snap"], "slap": ["slap"], "backhand": ["backhand"], "tip": ["tip-in", "deflected"]}
K = 15  # shots of weight for the all-types square when a type is thin there
MIN_SHOTS = 8  # squares with fewer shots of any type are left blank


def shots() -> pl.DataFrame:
    frames = []
    for s in FULL_SEASONS:
        f = pl.read_parquet(TABLES / str(s) / "shot_features.parquet",
                            columns=["game_id", "event_id", "type", "x", "y_abs", "shot_type", "own_skaters", "opp_skaters", "empty_net"])
        xg = pl.read_parquet(TABLES / str(s) / "shots_xg_own.parquet")
        frames.append(f.join(xg, on=["game_id", "event_id"]))
    d = pl.concat(frames)
    return d.filter((pl.col("type") != "blocked-shot") & ~pl.col("empty_net") & (pl.col("own_skaters") == 5) & (pl.col("opp_skaters") == 5)
                    & (pl.col("x") >= X0) & (pl.col("x") <= X1) & (pl.col("y_abs") <= Y1))


def grid(d: pl.DataFrame) -> dict:
    d = d.with_columns(cx=((pl.col("x") - X0) // CELL).cast(pl.Int32), cy=(pl.col("y_abs") // CELL).cast(pl.Int32),
                       kind=pl.col("shot_type").replace_strict({v: k for k, vs in TYPES.items() for v in vs}, default=None))
    nx, ny = (X1 - X0) // CELL + 1, Y1 // CELL + 1
    allc = {(r["cx"], r["cy"]): (r["n"], r["m"]) for r in d.group_by("cx", "cy").agg(n=pl.len(), m=pl.col("xg").mean()).iter_rows(named=True)}
    by = {(r["kind"], r["cx"], r["cy"]): (r["n"], r["m"]) for r in d.filter(pl.col("kind").is_not_null()).group_by("kind", "cx", "cy").agg(n=pl.len(), m=pl.col("xg").mean()).iter_rows(named=True)}
    out = {}
    for kind in TYPES:
        rows = []
        for cy in range(ny):
            row = []
            for cx in range(nx):
                n_all, m_all = allc.get((cx, cy), (0, None))
                if n_all < MIN_SHOTS:
                    row.append(None)
                    continue
                n, m = by.get((kind, cx, cy), (0, 0.0))
                row.append(round(100 * (n * m + K * m_all) / (n + K), 1))
            rows.append(row)
        out[kind] = rows
    counts = d.filter(pl.col("kind").is_not_null()).group_by("kind").agg(n=pl.len(), m=pl.col("xg").mean())
    return {"cell": CELL, "x0": X0, "y1": Y1, "nx": nx, "ny": ny, "net_x": 89, "seasons": f"{FULL_SEASONS[0]}-{str(FULL_SEASONS[0] + 1)[2:]} to {FULL_SEASONS[-1]}-{str(FULL_SEASONS[-1] + 1)[2:]}",
            "shots": d.height, "types": {r["kind"]: {"n": r["n"], "avg": round(100 * r["m"], 1)} for r in counts.iter_rows(named=True)}, "grid": out}


def run() -> int:
    g = grid(shots())
    (SITE_DATA / "xg_grid.json").write_text(json.dumps(g, separators=(",", ":")))
    return g["shots"]


if __name__ == "__main__":
    print(run())
