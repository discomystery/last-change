"""Team fingerprints as they stood the morning of any day of any season, fast enough to replay whole seasons.

The same estimate as metrics/team_style.run (this season's games blended with last season, itself pulled toward the
league by each trait's stabilization weight; percentiles, ranks and scores among the clubs on the schedule), worked
out from running totals instead of one recompute per day. The likely ranges come from 300 bootstrap draws like the
real run's, with different random draws, so they can differ from it a little. Goaltending is left on last season's
team record (the real run starts from the goalies who have played for the club this season); no call uses it.
`fidelity` measures how closely a replayed day matches the saved fingerprints.json.
"""
import numpy as np
import polars as pl

from pipeline.config import REGULAR, TABLES
from pipeline.export import site_json
from pipeline.metrics import adjust, team_style, units
from pipeline.metrics.team_style import COLS, COUNTED, DIMS

BOOTSTRAPS = 300
SHOWN = site_json.READY + site_json.CALL_ONLY  # what fingerprints.json carries, so calls choose among the same traits
_IDX = {c: i for i, c in enumerate(COLS)}


def _dim_values(sums: np.ndarray) -> dict[str, np.ndarray]:
    """Every trait's value from column sums (last axis = COLS), NaN where the denominator is zero."""
    out = {}
    for dim, (num, den, mult, _) in DIMS.items():
        n = sum(sums[..., _IDX[c]] for c in num)
        d = sum(sums[..., _IDX[c]] for c in den)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[dim] = np.where(d == 0, np.nan, mult * n / np.where(d == 0, 1, d))
    return out


class Replay:
    """One season's per-game sums, ready to be cut at any date."""

    def __init__(self, season: int):
        self.season = season
        w = adjust.weights()
        self.k = team_style.stabilization(w)
        now, last = team_style.per_game(season, w), team_style.per_game(season - 1, w)
        games = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type") == REGULAR)
        self.abbr = {}
        for s_ in (season - 1, season):
            g = pl.read_parquet(TABLES / str(s_) / "games.parquet")
            self.abbr |= {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
        clubs = team_style._scheduled_clubs(season) or set(games["home_id"]) | set(games["away_id"])
        lm = team_style._matrix(last)
        self.league_last = {dim: float(np.nanmean([team_style._values(m)[dim] for m in lm.values()])) for dim in DIMS}
        self.prev, self.n_prev = {}, {}
        for tid in clubs:
            m = lm.get(tid)
            vals = team_style._values(m) if m is not None else dict(self.league_last)
            self.prev[tid] = {}
            self.n_prev[tid] = {}
            for dim in DIMS:
                n = int((m[:, _IDX[COUNTED[dim]]] > 0).sum()) if m is not None and dim in COUNTED else (len(m) if m is not None else 0)
                self.n_prev[tid][dim] = n
                self.prev[tid][dim] = vals[dim] if n else self.league_last[dim]
        self.dates, self.mats, self.cum = {}, {}, {}
        for tid in clubs:
            t = now.filter(pl.col("team_id") == tid).sort("date", "game_id")
            self.dates[tid] = np.array(t["date"].to_list(), dtype=object)
            self.mats[tid] = t.select(COLS).to_numpy() if t.height else np.empty((0, len(COLS)))
        shapes = units.shapes(season)
        self.gdates = dict(zip(games["game_id"], games["date"]))
        self.elevens = {tid: sorted(self.gdates[g] for (g, t), (f, _) in shapes.items() if t == tid and g in self.gdates and f <= units.ELEVEN)
                        for tid in clubs}
        self.rng = np.random.default_rng(season)

    def _n(self, tid: int, day: str) -> int:
        return int(np.searchsorted(self.dates[tid], day, side="left")) if len(self.dates[tid]) else 0

    def on(self, day: str) -> dict:
        """{abbr: {"games", "dims": {dim: {"blend": {...}, "season": {...}}}, "lineup"}} from games before `day`."""
        point, boots, games = {}, {}, {}
        for tid in self.mats:
            n = self._n(tid, day)
            mat = self.mats[tid][:n]
            games[tid] = n
            obs = _dim_values(mat.sum(axis=0)) if n else {dim: np.nan for dim in DIMS}
            counted = {dim: int((mat[:, _IDX[COUNTED[dim]]] > 0).sum()) if dim in COUNTED else n for dim in DIMS}
            point[tid] = self._blend(tid, obs, counted)
            if n:
                draws = self.rng.multinomial(n, np.full(n, 1 / n), size=BOOTSTRAPS) @ mat
                bobs = _dim_values(draws)
                boots[tid] = bobs  # season-mode draws: only the likely range of this season alone is used by calls
        out = {}
        for tid in self.mats:
            dims = {}
            for dim in SHOWN:
                higher = DIMS[dim][3]
                dims[dim] = {}
                for mode in ("blend", "season"):
                    league = np.array([point[t][mode][dim] for t in self.mats if _ok(point[t][mode][dim])])
                    v = point[tid][mode][dim]
                    if not _ok(v) or not len(league):
                        dims[dim][mode] = {"v": None, "pct": None, "lo": None, "hi": None, "rank": None, "index": None}
                        continue
                    lo = hi = None
                    if mode == "season" and tid in boots:
                        d = boots[tid][dim]
                        if np.isfinite(d).any():
                            a, b = np.nanpercentile(d, [10, 90])
                            lo, hi = sorted((round(team_style._pct(a, league, higher)), round(team_style._pct(b, league, higher))))
                    better = (league > v).sum() if higher else (league < v).sum()
                    mean = float(np.mean(league))
                    dims[dim][mode] = {"v": round(float(v), 4), "pct": round(team_style._pct(v, league, higher)), "lo": lo, "hi": hi,
                                       "rank": int(better) + 1, "index": round(100 * v / mean) if mean else None}
            n11 = int(np.searchsorted(np.array(self.elevens[tid], dtype=object), day, side="left")) if self.elevens[tid] else 0
            out[self.abbr.get(tid, str(tid))] = {"games": games[tid], "dims": dims,
                                                  "lineup": {"eleven": n11, "games": games[tid], "habit": units.plays_eleven(n11, games[tid])}}
        return out

    def _blend(self, tid: int, obs: dict, counted: dict) -> dict:
        out = {"season": {}, "blend": {}}
        for dim in DIMS:
            kk = self.k[dim]["k_games"]
            n = counted[dim]
            o = float(obs[dim]) if n else np.nan
            prior = (self.n_prev[tid][dim] * self.prev[tid][dim] + kk * self.league_last[dim]) / (self.n_prev[tid][dim] + kk)
            out["season"][dim] = o
            out["blend"][dim] = (n * o + kk * prior) / (n + kk) if n and _ok(o) else prior
        return out


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and np.isnan(x)) and not (isinstance(x, np.floating) and np.isnan(x))


def fidelity(replayed: dict, saved: dict, traits: list[str]) -> dict:
    """How often a replayed day agrees with the saved fingerprints, trait by trait: same rank, percentile within a
    point, value within 0.1%. Goaltending is left out (see the module note)."""
    same = total = 0
    worst = []
    for t, e in saved["teams"].items():
        for k in traits:
            if k == "goalie" or k not in e["dims"] or t not in replayed:
                continue
            a, b = e["dims"][k]["blend"], replayed[t]["dims"][k]["blend"]
            if a.get("v") is None or b.get("v") is None:
                continue
            total += 1
            ok = a["rank"] == b["rank"] and abs(a["pct"] - b["pct"]) <= 1 and abs(a["v"] - b["v"]) <= 1e-3 * max(1.0, abs(a["v"]))
            same += ok
            if not ok:
                worst.append(f"{t} {k}: saved {a['v']} ({a['rank']}), replayed {b['v']} ({b['rank']})")
    return {"matched": same, "of": total, "examples": worst[:5]}
