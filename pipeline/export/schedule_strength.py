"""Strength of schedule for every club: the games played so far, the rest of the season, and the next ten games.

Each game is rated by how hard it would be for an average team, in the win model's own units (expected goals a game,
see metrics/win_model.py), so nothing new is fitted here and the hidden win chance is never written out:
  - the opponent: its chance rating, the decaying average of its expected-goal difference a game (empty nets out;
    a game counts 5% less with each game played since, so a hot or cold month shows, and last season's games are
    worth less again). For games already played it is the opponent's rating on the morning of that game, so a
    team met during a hot run counts as the team it was then; for games to come it is the rating now.
  - where it is played: road games are harder by the win model's home-ice term, turned into expected goals.
  - rest: the second night of a back-to-back is harder by the model's back-to-back term (easier when it is the
    opponent's second night).
A schedule's difficulty is the average over its games: "like facing a team that out-chances its opponents by 0.15
expected goals a game, every night". Clubs are ranked 1 (hardest) to 32.

Backtest (`python -m pipeline.export.schedule_strength`, seasons 2024-25 and 2025-26 split at the halfway date): see
`backtest()`; the figures are written into the Methods text on the page.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timezone

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FINAL_STATES, FULL_SEASONS, REGULAR, SITE_DATA
from pipeline.metrics import win_model

NEXT = 10  # "the next ten games"


def _ratings_by_day(games: pl.DataFrame, p: win_model.Params = win_model.Params()):
    """Walk the played games in date order, as win_model.pregame does. Returns the morning rating of every team on
    every game day (game_id -> {team: rating}) and the ratings after the last game."""
    sw: dict[int, float] = defaultdict(float)
    sx: dict[int, float] = defaultdict(float)
    season, morning = None, {}
    for day in games.partition_by("date", maintain_order=True):
        if day["season"][0] != season:
            season = day["season"][0]
            for t in list(sw):
                sw[t] *= p.carry
                sx[t] *= p.carry
        rate = lambda t: sx[t] / (sw[t] + p.prior_games)
        for g in day.iter_rows(named=True):
            morning[g["game_id"]] = {g["home_id"]: rate(g["home_id"]), g["away_id"]: rate(g["away_id"])}
        for g in day.iter_rows(named=True):
            for t, x in ((g["home_id"], g["h_xgd"]), (g["away_id"], g["a_xgd"])):
                sw[t] = sw[t] * p.decay + 1
                sx[t] = sx[t] * p.decay + x
    now = {t: sx[t] / (sw[t] + p.prior_games) for t in sw}
    return morning, now


def _terms(model) -> tuple[float, float]:
    """Home ice and the back-to-back, in expected goals a game (the model's terms divided by its chance weight)."""
    coef = dict(zip(win_model.FEATURES, model.coef_[0]))
    return float(model.intercept_[0] / coef["xgd"]), float(coef["b2b"] / coef["xgd"])


def _b2b(schedule: list[dict]) -> dict[tuple[int, int], int]:
    """(game_id, team) -> 1 when the team also played the day before."""
    days = defaultdict(set)
    for g in schedule:
        for t in (g["home_id"], g["away_id"]):
            days[t].add(g["date"])
    out = {}
    for g in schedule:
        d = date.fromisoformat(str(g["date"]))
        for t in (g["home_id"], g["away_id"]):
            out[(g["game_id"], t)] = int(str(date.fromordinal(d.toordinal() - 1)) in days[t])
    return out


def game_rows(schedule: list[dict], played: set[int], morning: dict, now: dict, home: float, b2b: float) -> pl.DataFrame:
    """One row per club per game: the opponent's rating, the venue and rest parts, and the game's difficulty."""
    rest = _b2b(schedule)
    rows = []
    for g in schedule:
        for t, o, at_home in ((g["home_id"], g["away_id"], True), (g["away_id"], g["home_id"], False)):
            done = g["game_id"] in played
            opp = (morning.get(g["game_id"], {}).get(o, now.get(o, 0.0)) if done else now.get(o, 0.0))
            venue = -home if at_home else home
            tired = b2b * (rest[(g["game_id"], o)] - rest[(g["game_id"], t)])  # b2b < 0: a tired opponent helps
            rows.append({"game_id": g["game_id"], "date": str(g["date"]), "team_id": t, "opp_id": o, "home": at_home,
                         "played": done, "b2b": rest[(g["game_id"], t)], "opp_b2b": rest[(g["game_id"], o)],
                         "opp": opp, "venue": venue, "rest": tired, "hard": opp + venue + tired})
    return pl.DataFrame(rows).sort("team_id", "date", "game_id")


def summarise(rows: pl.DataFrame) -> pl.DataFrame:
    """Each club's three schedules: averages of the game parts, with ranks among the clubs (1 = hardest)."""
    nxt = rows.filter(~pl.col("played")).group_by("team_id", maintain_order=True).head(NEXT)
    parts = []
    for name, df in (("past", rows.filter(pl.col("played"))), ("left", rows.filter(~pl.col("played"))), ("next", nxt)):
        s = df.group_by("team_id").agg(
            pl.len().alias("games"), pl.col("hard").mean(), pl.col("opp").mean(), pl.col("venue").mean(), pl.col("rest").mean(),
            pl.col("home").sum().alias("home_games"), pl.col("b2b").sum().alias("b2b"), pl.col("opp_b2b").sum().alias("opp_b2b"))
        s = s.with_columns(rank=pl.col("hard").rank("min", descending=True).cast(pl.Int32))
        parts.append(s.rename({c: f"{name}_{c}" for c in s.columns if c != "team_id"}))
    out = parts[0]
    for p in parts[1:]:
        out = out.join(p, on="team_id", how="full", coalesce=True)
    return out


def model():
    return win_model.fit(win_model.pregame(win_model.team_games(FULL_SEASONS)))


def build(season: int, m=None, cut: str | None = None):
    """Rows and summary for one season, as of the morning of `cut` (default: now, every final game counted)."""
    from pipeline.ingest import nhl

    m = m or model()
    home, b2b = _terms(m)
    have = [s for s in FULL_SEASONS + [CURRENT_SEASON] if s <= season]
    played_games = win_model.team_games(have)
    if cut:
        played_games = played_games.filter(pl.col("date").cast(pl.Utf8) < cut)
    morning, now = _ratings_by_day(played_games)
    played = set(played_games.filter(pl.col("season") == season)["game_id"].to_list())
    schedule = [g for g in nhl.season_games(season) if g["game_type"] == REGULAR]
    rows = game_rows(schedule, played, morning, now, home, b2b)
    return rows, summarise(rows), {"home": home, "b2b": b2b}


def _abbr(season: int) -> dict[int, str]:
    from pipeline.ingest import nhl

    out = {}
    for g in nhl.season_games(season):
        out[g["home_id"]], out[g["away_id"]] = g["home"], g["away"]
    return out


r3 = lambda x: None if x is None else round(float(x), 3)


def run(season: int = CURRENT_SEASON) -> dict:
    rows, summ, terms = build(season)
    abbr = _abbr(season)
    _, now = _ratings_by_day(win_model.team_games([s for s in FULL_SEASONS + [CURRENT_SEASON] if s <= season]))
    # Opponents' points percentage today, as a familiar check beside the rating (teams.json is written first).
    tj = SITE_DATA / "teams.json"
    pct = {t["abbr"]: t.get("pt_pct") for t in json.loads(tj.read_text())} if tj.exists() else {}
    rows = rows.with_columns(opp_pct=pl.col("opp_id").replace_strict(abbr, default=None).replace_strict(pct, default=None, return_dtype=pl.Float64))
    teams = []
    for s in summ.iter_rows(named=True):
        tid = s["team_id"]
        t = {"team": abbr[tid]}
        mine = rows.filter(pl.col("team_id") == tid)
        nxt = mine.filter(~pl.col("played")).head(NEXT)
        for k, part in (("past", mine.filter(pl.col("played"))), ("left", mine.filter(~pl.col("played"))), ("next", nxt)):
            if not s.get(f"{k}_games"):
                t[k] = None
                continue
            t[k] = {"games": s[f"{k}_games"], "rank": s[f"{k}_rank"], "hard": r3(s[f"{k}_hard"]), "opp": r3(s[f"{k}_opp"]),
                    "venue": r3(s[f"{k}_venue"]), "rest": r3(s[f"{k}_rest"]), "home": s[f"{k}_home_games"],
                    "b2b": s[f"{k}_b2b"], "opp_b2b": s[f"{k}_opp_b2b"], "opp_pct": r3(part["opp_pct"].mean())}
        t["games"] = [{"id": g["game_id"], "date": g["date"], "opp": abbr[g["opp_id"]], "home": g["home"],
                       "b2b": g["b2b"], "opp_b2b": g["opp_b2b"], "hard": r3(g["hard"])} for g in nxt.iter_rows(named=True)]
        teams.append(t)
    teams.sort(key=lambda t: t["team"])
    # Where an upcoming game sits among every game left in the league, for the shading of the next-ten strip.
    left = rows.filter(~pl.col("played"))["hard"].to_numpy()
    cuts = [r3(x) for x in np.quantile(left, [0.2, 0.4, 0.6, 0.8])] if len(left) else []
    payload = {"season": season, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "next": NEXT,
               "terms": {k: r3(v) for k, v in terms.items()}, "cuts": cuts,
               "ratings": {abbr[t]: r3(v) for t, v in sorted(now.items()) if t in abbr}, "teams": teams}
    (SITE_DATA / "schedule_strength.json").write_text(json.dumps(payload, separators=(",", ":")))
    return {"schedule_strength": len(teams)}


def backtest(seasons=(2024, 2025)) -> dict:
    """At each season's halfway date, does the remaining schedule's difficulty help predict how teams do the rest of
    the way, beside their own rating? And does correcting the first half's record for the schedule played make it a
    better guide to the second half? Win share counts every win (overtime and shootout too)."""
    m = win_model.fit(win_model.pregame(win_model.team_games([2023])))  # fitted before either test season
    out = {}
    for season in seasons:
        all_games = win_model.team_games([season])
        mid = str(all_games["date"].sort()[all_games.height // 2])
        rows, summ, _ = build(season, m, cut=mid)
        _, now = _ratings_by_day(win_model.team_games([s for s in FULL_SEASONS if s < season] + [season]).filter(pl.col("date").cast(pl.Utf8) < mid))
        res = []
        for half, sel in (("first", pl.col("date").cast(pl.Utf8) < mid), ("second", pl.col("date").cast(pl.Utf8) >= mid)):
            g = all_games.filter(sel)
            w = pl.concat([g.select(pl.col("home_id").alias("team_id"), (pl.col("home_score") > pl.col("away_score")).alias("w")),
                           g.select(pl.col("away_id").alias("team_id"), (pl.col("away_score") > pl.col("home_score")).alias("w"))])
            res.append(w.group_by("team_id").agg(pl.col("w").mean().alias(half)))
        df = summ.join(res[0], on="team_id").join(res[1], on="team_id")
        df = df.with_columns(own=pl.col("team_id").replace_strict(now, default=0.0))
        y = df["second"].to_numpy()

        def r2(*cols):
            X = np.column_stack([np.ones(len(y))] + [df[c].to_numpy() for c in cols])
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            return float(1 - ((y - X @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum()), [float(b) for b in beta[1:]]

        out[season] = {"mid": mid, "spread_left": float(df["left_hard"].std()), "spread_past": float(df["past_hard"].std()),
                       "own": r2("own"), "own+left": r2("own", "left_hard"),
                       "first": r2("first"), "first+past": r2("first", "past_hard"),
                       "corr_left_second": float(np.corrcoef(df["left_hard"], y)[0, 1])}
    return out


if __name__ == "__main__":
    print(json.dumps(backtest(), indent=1))
