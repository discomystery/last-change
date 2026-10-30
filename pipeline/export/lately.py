"""The "Lately" strip at the top of each preview (Sora picked it 2026-10-30, thread "Improve game previews and automate
tuning": every analytics and betting-style preview leads with recent form, and ours had only records and the last
result).

For each club, from its games before the preview's day only (so a rebuilt preview sees what a live one would have):
  games     the last ten regular-season games this season, oldest first: date, opponent, home, goals, how it ended
  record    wins, losses, overtime losses over those games, and goals for and against
  chances   the share of 5-on-5 expected goals over those games (score-and-venue adjusted, as on the Season trend
            chart), the share over the whole season so far, and a word: "better" or "worse" only when the gap is
            outside the bounce band of ten-game windows (trend.json `band`, about 4.4 points either way), else "usual";
            plus its rank among the 32 clubs' last ten
  pp / pk   power-play goals of chances, and power-play goals allowed of times shorthanded (official counts)
  goalie    the projected starter's last five starts: saves of shots, and goals saved above expected
Facts about what has happened, never a lean on tonight.
"""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, TABLES

N = 10
GOALIE_STARTS = 5
MIN_GAMES = 3  # fewer games than this and the strip waits


@lru_cache(maxsize=4)
def _games(season: int) -> pl.DataFrame:
    from pipeline.export import trend
    g = trend.games(season)
    st = TABLES / str(season) / "game_stats.parquet"
    if st.exists():
        s = pl.read_parquet(st)
        g = g.join(s.rename({"pp_goals": "ppg", "pp_opps": "ppo"}), on=["game_id", "team"], how="left")
        opp = s.rename({"team": "opp", "pp_goals": "ppga", "pp_opps": "ppa"})
        g = g.join(opp, on=["game_id", "opp"], how="left")
    return g


@lru_cache(maxsize=4)
def _goalie_games(season: int) -> pl.DataFrame:
    from pipeline.metrics import goalies
    return goalies.per_game(season).filter(pl.col("started"))


def _band() -> list[float]:
    import json
    from pipeline.config import SITE_DATA
    p = SITE_DATA / "trend.json"
    try:
        return json.loads(p.read_text())["band"]
    except (OSError, ValueError, KeyError):
        return [-4.4, 4.3]


def _share(f: float, a: float) -> float | None:
    return round(100 * f / (f + a), 1) if f + a else None


def _result(gf: int, ga: int, end: str) -> str:
    return "W" if gf > ga else ("L" if end == "REG" else "OT")


def team_form(g: pl.DataFrame, team: str, band: list[float]) -> dict | None:
    """One club's strip from its games (already cut to before the preview)."""
    mine = g.filter(pl.col("team") == team).sort("game_id")
    if mine.height < MIN_GAMES:
        return None
    last = mine.tail(N)
    rows = last.to_dicts()
    res = [_result(r["gf"], r["ga"], r["end"]) for r in rows]
    now = _share(last["xgf_adj"].sum(), last["xga_adj"].sum())
    season = _share(mine["xgf_adj"].sum(), mine["xga_adj"].sum())
    gap = None if now is None or season is None else round(now - season, 1)
    word = "usual" if gap is None or mine.height < 2 * N or band[0] <= gap <= band[1] else ("better" if gap > 0 else "worse")
    out = {"n": len(rows), "games": [{"date": r["date"], "opp": r["opp"], "home": r["home"], "gf": r["gf"], "ga": r["ga"],
                                      "end": r["end"], "res": x} for r, x in zip(rows, res)],
           "record": {"w": res.count("W"), "l": res.count("L"), "otl": res.count("OT"),
                      "gf": int(last["gf"].sum()), "ga": int(last["ga"].sum())},
           "chances": {"now": now, "season": season, "gap": gap, "word": word, "season_games": mine.height,
                       "xgf": round(float(last["xgf_adj"].sum()), 1), "xga": round(float(last["xga_adj"].sum()), 1)}}
    if "ppg" in last.columns:
        out["pp"] = [int(last["ppg"].fill_null(0).sum()), int(last["ppo"].fill_null(0).sum())]
        out["pk"] = [int(last["ppga"].fill_null(0).sum()), int(last["ppa"].fill_null(0).sum())]
    return out


def goalie_form(season: int, goalie: int | None, before: str) -> dict | None:
    if not goalie:
        return None
    gg = _goalie_games(season).filter((pl.col("goalie_id") == goalie) & (pl.col("date") < before)).sort("game_id").tail(GOALIE_STARTS)
    if gg.is_empty():
        return None
    sa, ga = int(gg["sa"].sum()), int(gg["ga"].sum())
    return {"id": goalie, "starts": gg.height, "saves": sa - ga, "shots": sa,
            "gsax": round(float(gg["xga"].sum() - ga), 1)}


def run(away: str, home: str, before: str, goalies: dict | None = None, season: int = CURRENT_SEASON) -> dict | None:
    """Both clubs' strips for a preview, from games before `before` (the game's local date)."""
    g = _games(season).filter(pl.col("date") < before[:10])
    if g.is_empty():
        return None
    band = _band()
    out = {t: team_form(g, t, band) for t in (away, home)}
    if not all(out.values()):
        return None
    shares = []  # every club's last-ten share, for the rank
    for t in g["team"].unique().to_list():
        last = g.filter(pl.col("team") == t).sort("game_id").tail(N)
        if last.height >= MIN_GAMES:
            shares.append((_share(last["xgf_adj"].sum(), last["xga_adj"].sum()) or 50.0, t))
    order = [t for _, t in sorted(shares, reverse=True)]
    for t in (away, home):
        out[t]["chances"]["rank"] = order.index(t) + 1 if t in order else None
        out[t]["chances"]["of"] = len(order)
        gf = goalie_form(season, (goalies or {}).get(t), before[:10])
        if gf:
            out[t]["goalie"] = gf
    return out
