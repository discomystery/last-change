"""Who will start in net: a guess from each team's recent pattern, for preview wording only.

For every goalie who has started one of the team's last 40 games, the features are: started the team's last game, his
run of straight starts, his share of the last 5 and 10 starts and of this season's, whether tonight is the second night
of a back-to-back (and whether he started the first night), his goals saved above expected over his last three starts
(the hot hand), days since his last start, and how far into the season the team is. A logistic regression fitted on
2023-24 to 2025-26 scores each goalie; the scores are scaled to add to one per team-game.

Backtest (each season predicted by a model fitted on the other two): picked the starter 70% / 69% of the time in
2023-24 / 2024-25, against 41% for "whoever started last", 57% for "the season's busiest" and 63-64% for "the busiest,
except the other one on the second night of a back-to-back". The hot hand adds about half a point; the back-to-back
does most of the work. A pick the model gives 70%+ was right about three times in four.

Goalies the availability table has out (injured, suspended, personal, off the roster) or no longer with the club are
dropped before scaling. Games further ahead are chained: the likeliest starter of each earlier unplayed game is taken
as its starter. Never shown as a number; it only decides which goalie a preview talks about first.

On game day NHL.com's own preview names a projected starter (pipeline/ingest/nhl_previews.py). It was right 91% of the
time over the first 141 team-games of 2026-27, so when it is in, its goalie goes first with NHL_CHANCE and the model's
others share the rest.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date
from functools import lru_cache

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS

POOL = 40  # team starts looked back over for candidates
FEATURES = ["last", "streak", "sh5", "sh10", "shs", "b2b", "last_b2b", "hot", "last_hot", "rest", "ncur"]
NHL_CHANCE = 0.9  # how often NHL.com's projected starter started, 2026-27 to Oct. 10
OUT = {"injured", "suspended", "personal", "no_club", "not_listed", "off_roster"}


@lru_cache(maxsize=1)
def history() -> pl.DataFrame:
    """Every regular-season start since 2023-24, oldest first: player_id, game_id, date, team, season, gsax."""
    from pipeline.metrics import goalies, venues

    st = venues.starts().filter(pl.col("game_id") // 1_000_000 >= FULL_SEASONS[0])
    st = st.with_columns(season=(pl.col("game_id") // 1_000_000).cast(pl.Int64))
    parts = []
    for s in range(FULL_SEASONS[0], CURRENT_SEASON + 1):
        try:
            parts.append(goalies.per_game(s).select("game_id", pl.col("goalie_id").alias("player_id"), (pl.col("xga") - pl.col("ga")).alias("gsax")))
        except FileNotFoundError:
            continue
    if parts:
        st = st.join(pl.concat(parts), on=["game_id", "player_id"], how="left")
    else:
        st = st.with_columns(gsax=pl.lit(None, pl.Float64))
    return st.select("player_id", "game_id", "date", "team", "season", "gsax").sort("date", "game_id")


def _rows(team_starts: list[tuple], goalie_starts: dict, day: str, season: int) -> list[dict]:
    """One feature row per candidate goalie for a team's game on `day`. `team_starts` are the team's earlier starts
    as (date, season, player_id), oldest first; `goalie_starts[pid]` each goalie's (date, gsax), oldest first."""
    h = team_starts
    today = Date.fromisoformat(day)
    cur = [x for x in h if x[1] == season]
    prev = h[-1] if h else None
    b2b = int(prev is not None and (today - Date.fromisoformat(prev[0])).days == 1)
    out = []
    for c in dict.fromkeys(x[2] for x in reversed(h[-POOL:])):
        streak = 0
        for x in reversed(h):
            if x[2] != c:
                break
            streak += 1

        def share(n: int) -> float:
            w = h[-n:]
            return sum(1 for x in w if x[2] == c) / max(1, len(w))
        mine = [x for x in goalie_starts.get(c, []) if x[0] < day]
        hot = sum(v for _, v in mine[-3:] if v is not None and v == v)
        last = int(prev is not None and prev[2] == c)
        out.append({"cand": c, "last": last, "streak": min(streak, 10), "sh5": share(5), "sh10": share(10),
                    "shs": sum(1 for x in cur if x[2] == c) / len(cur) if cur else share(20), "b2b": b2b, "last_b2b": last * b2b,
                    "hot": hot, "last_hot": last * hot, "rest": min(10, (today - Date.fromisoformat(mine[-1][0])).days) if mine else 10,
                    "ncur": min(len(cur), 10)})
    return out


def _walk(st: pl.DataFrame):
    """Feature rows for every start in `st`, in order, each built only from the starts before it."""
    team, gl = defaultdict(list), defaultdict(list)
    for r in st.iter_rows(named=True):
        for x in _rows(team[r["team"]], gl, r["date"], r["season"]):
            yield r, x
        team[r["team"]].append((r["date"], r["season"], r["player_id"]))
        gl[r["player_id"]].append((r["date"], r["gsax"]))


@lru_cache(maxsize=1)
def model():
    from sklearn.linear_model import LogisticRegression

    st = history().filter(pl.col("season").is_in(FULL_SEASONS))
    X, y = [], []
    for r, x in _walk(st):
        X.append([x[f] for f in FEATURES])
        y.append(int(x["cand"] == r["player_id"]))
    return LogisticRegression(C=1.0, max_iter=3000).fit(np.array(X), np.array(y))


def _score(rows: list[dict], drop: set[int]) -> list[tuple[int, float]]:
    rows = [x for x in rows if x["cand"] not in drop]
    if not rows:
        return []
    p = model().predict_proba(np.array([[x[f] for f in FEATURES] for x in rows]))[:, 1]
    p = p / p.sum()
    return sorted(((int(x["cand"]), round(float(q), 3)) for x, q in zip(rows, p)), key=lambda t: -t[1])


def _unavailable(season: int, team: str, day: str) -> tuple[set[int], set[int]] | None:
    """(goalies out, everyone with the club) from the latest availability rows on or before `day`; None if there are none."""
    from pipeline.metrics import availability

    t = availability.load(season)
    if t.is_empty():
        return None
    t = t.filter((pl.col("team") == team) & (pl.col("date") <= day))
    if t.is_empty():
        return None
    t = t.filter(pl.col("date") == t["date"].max())
    return set(t.filter(pl.col("status").is_in(list(OUT)))["player_id"].to_list()), set(t["player_id"].to_list())


def _promote(ranked: list[tuple[int, float]], pick: int) -> list[tuple[int, float]]:
    """NHL.com's projected starter first with NHL_CHANCE, the model's other goalies sharing the rest in their order."""
    rest = [(p, q) for p, q in ranked if p != pick]
    total = sum(q for _, q in rest)
    return [(pick, NHL_CHANCE)] + [(p, round((1 - NHL_CHANCE) * (q / total if total else 1 / len(rest)), 3)) for p, q in rest]


def nhl_picks(games: list[dict], season: int = CURRENT_SEASON, before: str | None = None) -> dict[int, dict[str, int]]:
    """NHL.com's projected starters for these games, where its preview was found (before `before`, an ISO time)."""
    from pipeline.ingest import nhl_previews
    out = {}
    for g in games:
        got = nhl_previews.projected(season, g["id"], before)
        if got:
            out[g["id"]] = got
    return out


def upcoming(games: list[dict], season: int = CURRENT_SEASON, before: str | None = None,
             nhl: dict[int, dict[str, int]] | None = None) -> dict[int, dict[str, list[tuple[int, float]]]]:
    """Likely starters for unplayed games: {game_id: {team: [(player_id, chance), ...]}}, likeliest first. `games`
    are schedule entries (id, date, home, away); starts on or after `before` (a date) are ignored, so a past day can be
    replayed. Later games of a team chain on the likeliest starter of its earlier ones. `nhl` holds NHL.com's projected
    starters ({game_id: {team: player_id}}, see `nhl_picks`); a goalie it names goes first unless he is out."""
    nhl = nhl_picks(games, season, before) if nhl is None else nhl
    st = history()
    if before:
        st = st.filter(pl.col("date") < before[:10])
    team, gl = defaultdict(list), defaultdict(list)
    for r in st.iter_rows(named=True):
        team[r["team"]].append((r["date"], r["season"], r["player_id"]))
        gl[r["player_id"]].append((r["date"], r["gsax"]))
    out: dict[int, dict] = {}
    for g in sorted(games, key=lambda g: (g["date"], g["id"])):
        out[g["id"]] = {}
        for t in (g["away"], g["home"]):
            got = _unavailable(season, t, g["date"])
            drop = set()
            if got:
                out_now, listed = got
                drop = out_now | {c for c in {x[2] for x in team[t][-POOL:]} if c not in listed}
            ranked = _score(_rows(team[t], gl, g["date"], season), drop)
            pick = (nhl.get(g["id"]) or {}).get(t)
            if pick and pick not in drop:
                ranked = _promote(ranked, pick)
            out[g["id"]][t] = ranked
            if ranked:  # the next game of this team assumes tonight's likeliest starter played
                team[t].append((g["date"], season, ranked[0][0]))
    return out


def backtest(seasons=(2024, 2025)) -> dict:
    """Share of starters picked, each season predicted by a model fitted on the other full seasons."""
    from sklearn.linear_model import LogisticRegression

    st = history().filter(pl.col("season").is_in(FULL_SEASONS))
    rows = [(r["season"], r["game_id"], r["team"], x, int(x["cand"] == r["player_id"])) for r, x in _walk(st)]
    games = st.group_by("season").len()
    out = {}
    for s in seasons:
        tr = [(x, y) for se, _, _, x, y in rows if se != s]
        m = LogisticRegression(C=1.0, max_iter=3000).fit(np.array([[x[f] for f in FEATURES] for x, _ in tr]), np.array([y for _, y in tr]))
        te = [(g, t, x, y) for se, g, t, x, y in rows if se == s]
        p = m.predict_proba(np.array([[x[f] for f in FEATURES] for _, _, x, _ in te]))[:, 1]
        best: dict = {}
        for (g, t, _, y), q in zip(te, p):
            if (g, t) not in best or q > best[(g, t)][0]:
                best[(g, t)] = (q, y)
        n = games.filter(pl.col("season") == s)["len"][0]
        out[s] = round(sum(y for _, y in best.values()) / n, 3)
    return out


if __name__ == "__main__":
    print(backtest())
