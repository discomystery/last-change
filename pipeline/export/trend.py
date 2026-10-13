"""Season trend on team pages: where a team has come from, and whether this season's team is a new version of it.

Three pictures, one story, written to trend.json for every club:

1. The long view. Every season since 1997-98 from the franchise's NHL record (ingest/history.py): points percentage
   and how far it went in the playoffs.
2. Last season and this one, ten games at a time. Each regular-season game's share of the 5-on-5 chances (expected
   goals, score-and-venue adjusted, the same figure as the intro's "better of the chances"), a ten-game line through
   them, and a hatched band for how far a ten-game stretch drifts on bounces alone. A change is only marked when one
   split of the two seasons into before and after is bigger than any split of the same games reshuffled would give in
   19 of 20 tries (the season-trend mock-up's test, run over both seasons so a summer of roster moves can show too).
3. Then and now. Each style trait last season against this season so far, with the preview's "not the team you
   remember" rule: the season has as many games as the trait needs to be half signal, the two readings are 25+
   percentile points apart, and last season sits outside this season's likely range. Traits that pass are new; the
   team's strongest habits from last season that still hold are what carried over.

The words are rule-based, like the intros: teams are "they", ranks in words, numbers kept to the chart.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, SITE_DATA, TABLES
from pipeline.export.intros import _and, _cap, _num, _pick, _poss, _teams, _the
from pipeline.ingest import history

WINDOW = 10  # games in the rolling line
MIN_SIDE = 10  # games on each side of a marked change
SHUFFLES = 1000  # reshuffles for the change test
P_CHANGE = 0.05
CHANGED_GAP = 25  # percentile points between last season and this one (previews.CHANGED_GAP)
SKIP = {"goalie", "rebounds", "turnover", "breakdowns"}  # goaltending follows the goalies; the rest take a season or more to settle
SIGNATURE = 20  # last season's habits at the 80th percentile or above (or 20th or below) count as what they were known for

# How a move reads after "they're ...": (percentile went up, went down). Percentiles are oriented like the fingerprint
# cards: up is better for better/worse traits and "more" for style traits.
MOVE = {
    "volume": ("shooting more", "shooting less"),
    "quality": ("getting better looks", "settling for worse looks"),
    "dShots": ("sending their defensemen in to shoot more", "leaving more of the shooting to the forwards"),
    "inClose": ("getting closer to the net", "shooting from further out"),
    "shotsThrough": ("getting more shots past the blockers", "getting more shots blocked"),
    "suppression": ("giving up fewer shots", "giving up more shots"),
    "qualityAllowed": ("giving up fewer dangerous shots", "giving up more dangerous shots"),
    "longShifts": ("getting stuck on long shifts less", "getting pinned on long shifts more"),
    "blocking": ("blocking more shots", "blocking fewer shots"),
    "pointAllowed": ("letting the point shots come", "taking away the point"),
    "icing": ("icing the puck less", "icing the puck more"),
    "pace": ("playing faster", "slowing games down"),
    "forecheck": ("forechecking harder", "sitting back more"),
    "physical": ("hitting more", "hitting less"),
    "depth": ("rolling all four lines more", "leaning harder on their top players"),
    "pp": ("creating more on the power play", "creating less on the power play"),
    "pk": ("killing penalties better", "leaking more on the penalty kill"),
    "powerKill": ("more dangerous shorthanded", "less dangerous shorthanded"),
    "discipline": ("staying out of the box more", "taking more penalties"),
}
# What a team was known for, after "they ...": (top of the league, bottom of the league).
KNOWN = {
    "volume": ("fire from everywhere", "pick their spots"),
    "quality": ("get to the dangerous chances", "settle for hopeful shots"),
    "dShots": ("send their defensemen in to shoot", "leave the shooting to the forwards"),
    "inClose": ("get right to the net", "shoot from distance"),
    "shotsThrough": ("get their shots through", "get a lot of shots blocked"),
    "suppression": ("give up very few shots", "let the other team shoot a lot"),
    "qualityAllowed": ("keep the other team to hopeful shots", "give up dangerous chances"),
    "longShifts": ("rarely get stuck on long shifts", "get pinned on long shifts"),
    "blocking": ("block a lot of shots", "rarely block shots"),
    "pointAllowed": ("collapse around the net", "push out to take away the point"),
    "icing": ("make clean exits", "ice the puck to get out of trouble"),
    "pace": ("play fast, wide-open games", "grind games down"),
    "forecheck": ("hound the puck in the other team's end", "sit back rather than forecheck"),
    "physical": ("hit everything that moves", "play a finesse game"),
    "depth": ("roll all four lines", "lean hard on their top players"),
    "pp": ("have a lethal power play", "have a toothless power play"),
    "pk": ("kill penalties well", "leak on the penalty kill"),
    "powerKill": ("create chances shorthanded", None),
    "discipline": ("stay out of the box", "spend a lot of time in the box"),
}
ROUND = {0: "missed the playoffs", 1: "went out in the first round", 2: "went out in the second round",
         3: "went out in the conference final", 4: "lost in the Final", 5: "won the Stanley Cup"}
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _day(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{MON[int(m) - 1]} {int(dd)}"


def _season_name(s: int) -> str:
    return f"{s}-{str(s + 1)[-2:]}"


# ---------------------------------------------------------------- games and the change test

def games(season: int) -> pl.DataFrame:
    """One row per team per regular-season game: date, opponent, score, and 5-on-5 expected goals for and against
    (score-and-venue adjusted)."""
    from pipeline.metrics import adjust
    d = TABLES / str(season)
    g = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    tg = pl.read_parquet(d / "team_game.parquet").filter(pl.col("strength") == "5v5")
    t = adjust.apply(tg, adjust.weights()).group_by("game_id", "team_id").agg(pl.col("xgf_adj").sum(), pl.col("xga_adj").sum())
    rows = []
    for r in g.iter_rows(named=True):
        for side, other in (("home", "away"), ("away", "home")):
            rows.append({"game_id": r["game_id"], "date": r["date"], "team": r[side], "team_id": r[f"{side}_id"], "opp": r[other],
                         "home": side == "home", "gf": r[f"{side}_score"], "ga": r[f"{other}_score"], "end": r["last_period"]})
    return pl.DataFrame(rows).join(t, on=["game_id", "team_id"]).with_columns(season=pl.lit(season)).sort("team", "game_id")


def _share(f: np.ndarray, a: np.ndarray) -> float:
    s = f.sum() + a.sum()
    return float(f.sum() / s) if s else 0.5


def _split_stats(f: np.ndarray, a: np.ndarray) -> np.ndarray:
    """For every allowed split point k (MIN_SIDE games or more on each side), how far apart the before and after shares
    are, weighted so splits near the ends are not favoured (sqrt(k(n-k)/n), as in a two-sample test). Works on a stack
    of reshuffles at once: f and a are (shuffles, games)."""
    n = f.shape[-1]
    cf, ca = np.cumsum(f, axis=-1), np.cumsum(a, axis=-1)
    tf, ta = cf[..., -1:], ca[..., -1:]
    k = np.arange(MIN_SIDE, n - MIN_SIDE + 1)
    bf, ba = cf[..., k - 1], ca[..., k - 1]
    before = bf / np.maximum(bf + ba, 1e-9)
    after = (tf - bf) / np.maximum(tf - bf + ta - ba, 1e-9)
    return np.abs(before - after) * np.sqrt(k * (n - k) / n)


def change(f: np.ndarray, a: np.ndarray, seed: int) -> dict | None:
    """The strongest before/after split, kept only when fewer than P_CHANGE of reshuffled seasons beat it."""
    n = len(f)
    if n < 2 * MIN_SIDE:
        return None
    obs = _split_stats(f[None], a[None])[0]
    best = int(np.argmax(obs))
    rng = np.random.default_rng(seed)
    order = np.argsort(rng.random((SHUFFLES, n)), axis=1)
    null = _split_stats(f[order], a[order]).max(axis=1)
    p = float((null >= obs[best]).mean())
    if p >= P_CHANGE:
        return None
    k = best + MIN_SIDE
    return {"k": k, "before": round(100 * _share(f[:k], a[:k]), 1), "after": round(100 * _share(f[k:], a[k:]), 1), "p": round(p, 3)}


def bounce_band() -> list[float]:
    """How far a ten-game share drifts from the season's share on bounces alone: the 10th and 90th percentiles of
    ten-game windows in reshuffled seasons, 2023-24 to 2025-26, in percentage points."""
    rng = np.random.default_rng(7)
    devs = []
    for s in FULL_SEASONS:
        for _, g in games(s).group_by("team"):
            f, a = g["xgf_adj"].to_numpy(), g["xga_adj"].to_numpy()
            whole = _share(f, a)
            for _ in range(20):
                o = rng.permutation(len(f))
                cf, ca = np.convolve(f[o], np.ones(WINDOW), "valid"), np.convolve(a[o], np.ones(WINDOW), "valid")
                devs.append(100 * (cf / (cf + ca) - whole))
    lo, hi = np.percentile(np.concatenate(devs), [10, 90])
    return [round(float(lo), 1), round(float(hi), 1)]


# ---------------------------------------------------------------- then and now

def last_season_traits(season: int) -> dict[str, dict[str, dict]]:
    """Each team's style traits over last season alone: value, league percentile (oriented like the fingerprint
    cards) and rank. Abbreviations are last season's."""
    from pipeline.metrics import adjust, team_style
    from pipeline.export.site_json import READY
    w = adjust.weights()
    mats = team_style._matrix(team_style.per_game(season, w))
    vals = {tid: team_style._values(m) for tid, m in mats.items()}
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    abbr = {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
    out: dict[str, dict[str, dict]] = {}
    for dim in READY:
        if dim in SKIP:
            continue
        higher = team_style.DIMS[dim][3]
        league = np.array([v[dim] for v in vals.values()])
        for tid, v in vals.items():
            x = v[dim]
            better = int((league > x).sum() if higher else (league < x).sum())
            out.setdefault(abbr[tid], {})[dim] = {"v": round(x, 4), "pct": round(100 * (len(league) - better - 0.5) / len(league)), "rank": better + 1}
    return out


def then_now(then: dict[str, dict], now: dict, stab: dict) -> list[dict]:
    """New this season (the preview's changed rule against last season), then what carried over: last season's
    signature habits that this season's likely range still covers. At most four in all."""
    games = now.get("games", 0)
    new, kept = [], []
    for dim, t in then.items():
        s = (now["dims"].get(dim) or {}).get("season") or {}
        if s.get("pct") is None or s.get("lo") is None:
            continue
        settled = games >= (stab.get(dim, {}).get("k_games") or 999)
        row = {"key": dim, "then": t, "now": {k: s[k] for k in ("v", "pct", "lo", "hi", "rank")}, "gap": abs(s["pct"] - t["pct"])}
        if settled and row["gap"] >= CHANGED_GAP and not s["lo"] <= t["pct"] <= s["hi"]:
            new.append({**row, "tag": "new"})
        elif settled and s["lo"] <= t["pct"] <= s["hi"] and abs(t["pct"] - 50) >= 50 - SIGNATURE and KNOWN[dim][0 if t["pct"] > 50 else 1]:
            kept.append({**row, "tag": "kept"})
    new.sort(key=lambda r: -r["gap"])
    kept.sort(key=lambda r: -abs(r["then"]["pct"] - 50))
    pick = new[:3]
    return pick + kept[: 4 - len(pick)]


# ---------------------------------------------------------------- words

def _history_line(t: dict, full: list[dict]) -> str | None:
    """Where the franchise has been: a drought, a run of playoff springs, or last spring's result."""
    if not full:
        return None
    X = _the(t)
    past = [h for h in full if h["season"] < CURRENT_SEASON]
    if not past:
        return None
    last = past[-1]
    dry = next((i for i, h in enumerate(reversed(past)) if h["playoffs"]), len(past))
    run = next((i for i, h in enumerate(reversed(past)) if not h["playoffs"]), len(past))
    cups = [h["season"] for h in past if h["playoffs"] == 5]
    ten = past[-10:]
    made = sum(1 for h in ten if h["playoffs"])
    if dry >= 4:
        return f"{_cap(X)} haven’t made the playoffs in {_num(dry)} seasons."
    if run >= 4:
        return f"{_cap(X)} have made the playoffs {_num(run)} springs running, and last spring they {ROUND[last['playoffs']]}."
    tail = f"; last spring they {ROUND[last['playoffs']]}" if last["playoffs"] else "; last spring they missed"
    cup = f" Their last Stanley Cup came in {cups[-1] + 1}." if cups and cups[-1] >= CURRENT_SEASON - 12 and last["playoffs"] != 5 else ""
    return f"{_cap(X)} have made the playoffs in {_num(made)} of the last {_num(len(ten))} seasons{tail}.{cup}"


def _level(share: float) -> str:
    return ("controlled the play" if share >= 53 else "had a bit more of the play" if share >= 51 else "broke about even" if share > 49
            else "chased the play a little" if share > 47 else "spent a lot of nights chasing the play")


def _last_spring(full: list[dict]) -> dict | None:
    """Last season's playoff result and the streak it extends, short enough to sit on the chart."""
    past = [h for h in full if h["season"] < CURRENT_SEASON]
    if not past:
        return None
    last = past[-1]
    dry = next((i for i, h in enumerate(reversed(past)) if h["playoffs"]), len(past))
    run = next((i for i, h in enumerate(reversed(past)) if not h["playoffs"]), len(past))
    words = ["Missed the playoffs", "Out in round one", "Out in round two", "Out in the conference final", "Lost in the Final",
             "Won the Stanley Cup"][last["playoffs"]]
    streak = (f"{_ordinal(dry)} straight year out" if dry >= 2 else None) if not last["playoffs"] else \
        (f"{_ordinal(run)} straight spring in" if run >= 3 else None)
    return {"result": words, "streak": streak}


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def story(abbr: str, t: dict, g: pl.DataFrame, brk: dict | None, traits: list[dict], full: list[dict]) -> dict:
    """A headline, a one- or two-sentence lede about style, the history sentence (kept for the fold under the chart)
    and the notes the chart writes on itself: last season's level and how it ended, this season's level so far, and
    the change date."""
    X, key = _the(t), (abbr, "trend")
    nick = t.get("nick") or t["place"]
    last = g.filter(pl.col("season") == CURRENT_SEASON - 1)
    now = g.filter(pl.col("season") == CURRENT_SEASON)
    new = [r for r in traits if r["tag"] == "new"]
    kept = [r for r in traits if r["tag"] == "kept"]
    lede = []
    if new:
        moves = [MOVE[r["key"]][0 if r["now"]["pct"] > r["then"]["pct"] else 1] for r in new]
        lead = "One thing has changed" if len(new) == 1 else "What’s new"
        lede.append(f"{lead}: they’re {_and(moves)}, by more than {'early-season ' if len(now) < 20 else ''}bounces would explain.")
    if kept:
        habits = [KNOWN[r["key"]][0 if r["then"]["pct"] > 50 else 1] for r in kept[:2]]
        lede.append(f"{'Otherwise they' if new else 'Their style hasn’t moved: they'} still {_and(habits)}.")
    notes: dict = {}
    if len(last):
        s = 100 * _share(last["xgf_adj"].to_numpy(), last["xga_adj"].to_numpy())
        notes["last"] = {"share": round(s, 1), "words": _level(s), **(_last_spring(full) or {})}
    if len(now):
        notes["now"] = {"share": round(100 * _share(now["xgf_adj"].to_numpy(), now["xga_adj"].to_numpy()), 1), "games": len(now)}
    dates = g["date"].to_list()
    n_last = len(dates) - len(now)
    recent = bool(brk) and brk["k"] >= n_last - WINDOW  # the change takes in this season, or last season's final stretch
    # Headline: a new version of themselves (one new trait or a recent change is enough, Sora 2026-10-12), a turn last
    # season, the same team, or too early to say.
    if new or recent:
        head, changed = _pick([f"A new version of the {nick}", f"Not the {nick} you remember", f"The {nick} have changed"], *key), True
    elif brk:
        when = _day(dates[brk["k"]])
        head = _pick([f"The {nick} turned a corner", f"A different {nick} since {when}"], *key) if brk["after"] > brk["before"] else \
            _pick([f"The {nick} have slipped", f"Something went out of the {nick} around {when}"], *key)
        changed = True
    elif kept:
        head, changed = _pick([f"Same {nick}, so far", f"The {nick} you know", f"No surprises from the {nick} yet"], *key), False
    else:
        head, changed = _pick([f"Too early to call the {nick}", f"Still getting to know the {nick}"], *key), False
    hist = _history_line(t, full)
    return {"head": head, "lede": lede, "history_text": hist.replace("'", "’") if hist else None, "notes": notes, "changed": changed}


# ---------------------------------------------------------------- around the same time

NEAR = (6, 10)  # games before and after a change date that count as "around the same time"
REGULAR_OF = (12, 15)  # dressed in 12 of the team's previous 15 games...
REGULAR_TOI = 10 * 60  # ...at 10:00+ of 5-on-5 a game
GONE = 8  # a regular who misses this many straight games (or the rest of the season) is news
BREAK_DAYS = 8


def lineup_table(seasons: list[int]) -> pl.DataFrame:
    """Every player-game in these seasons: who dressed for whom and their 5-on-5 time, with names and club abbreviations."""
    out = []
    for s in seasons:
        d = TABLES / str(s)
        g = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)
        ab = pl.concat([g.select("game_id", pl.col("home_id").alias("team_id"), pl.col("home").alias("club")),
                        g.select("game_id", pl.col("away_id").alias("team_id"), pl.col("away").alias("club"))])
        nm = pl.read_parquet(d / "players.parquet").select("player_id", "first", "last").unique("player_id")
        out.append(pl.read_parquet(d / "player_game.parquet").select("game_id", "player_id", "team_id", "pos", "sec5")
                   .join(ab, on=["game_id", "team_id"]).join(g.select("game_id", "date"), on="game_id").join(nm, on="player_id", how="left"))
    return pl.concat(out).sort("game_id")


def around(abbr: str, mine: pl.DataFrame, brk: dict | None, lineups: pl.DataFrame, teams: dict, coaches: pl.DataFrame | None) -> list[dict]:
    """What happened near the change date, as plain facts: breaks in the schedule, regulars who played their last game
    for the club, regulars who missed a long stretch, new regulars, and coaching changes. Never why, never "because"."""
    if not brk:
        return []
    place = teams.get(abbr, {}).get("place", abbr)
    ids = mine["game_id"].to_list()
    dates = mine["date"].to_list()
    seasons = mine["season"].to_list()
    n, k = len(ids), brk["k"]
    lo, hi = max(1, k - NEAR[0]), min(n - 1, k + NEAR[1])
    pos = {gid: i for i, gid in enumerate(ids)}
    own = lineups.filter(pl.col("club") == abbr)
    played: dict[int, dict] = {}
    for r in own.iter_rows(named=True):
        i = pos.get(r["game_id"])
        if i is None:
            continue
        p = played.setdefault(r["player_id"], {"name": f"{r['first']} {r['last']}", "pos": r["pos"], "games": {}})
        p["games"][i] = r["sec5"] or 0
    elsewhere = lineups.filter(pl.col("club") != abbr).group_by("player_id").agg(pl.col("date").min().alias("first_other"),
                                                                                 pl.col("date").max().alias("last_other"),
                                                                                 pl.col("club").last())
    other = {r["player_id"]: r for r in elsewhere.iter_rows(named=True)}

    def regular(p: dict, j: int) -> bool:
        prev = [i for i in range(max(0, j - REGULAR_OF[1]), j) if seasons[i] == seasons[j - 1]]
        g = [p["games"][i] for i in prev if i in p["games"]]
        return len(prev) >= REGULAR_OF[1] - 2 and len(g) >= REGULAR_OF[0] and sum(g) / len(g) >= REGULAR_TOI

    events: dict[int, list[str]] = {}
    add = lambda j, text: events.setdefault(j, []).append(text)
    for j in range(lo, hi + 1):
        gap = (datetime.fromisoformat(dates[j]) - datetime.fromisoformat(dates[j - 1])).days
        if seasons[j] == seasons[j - 1] and gap >= BREAK_DAYS:
            add(j, f"{'Olympic break: no' if gap >= 14 and dates[j][5:7] == '02' else 'No'} games for {gap - 1} days")
    left, out_, new = {}, {}, {}
    for pid, p in played.items():
        for j in range(lo, hi + 1):
            if j in p["games"] or (j - 1) not in p["games"] or not regular(p, j):
                continue
            later = [i for i in p["games"] if i > j and seasons[i] == seasons[j]]
            o = other.get(pid)
            if seasons[j] != seasons[j - 1]:
                if not later and o and o["first_other"] >= dates[j - 1]:
                    left.setdefault(j, []).append((p["name"], teams.get(o["club"], {}).get("place")))
                continue
            if not later and o and o["first_other"] > dates[j - 1]:
                left.setdefault(j, []).append((p["name"], teams.get(o["club"], {}).get("place")))
            else:
                back = min(later) if later else None
                miss = (back if back is not None else (n if seasons[-1] == seasons[j] else seasons.index(seasons[j] + 1) if seasons[j] + 1 in seasons else n)) - j
                if miss >= GONE:
                    out_.setdefault(j, []).append((p["name"], miss, back is None))
    for pid, p in played.items():
        first = min(p["games"])
        if lo <= first <= hi and seasons[first] == seasons[k]:
            nxt = [i for i in range(first, min(n, first + 10)) if seasons[i] == seasons[first]]
            if len(nxt) >= 5 and sum(i in p["games"] for i in nxt) >= 0.8 * len(nxt):
                new.setdefault(first, []).append(p["name"])
    for j, who in left.items():
        names = [f"{nm} (now with {pl_})" if pl_ else nm for nm, pl_ in who]
        add(j, f"Not back this season: {_and(names)}" if seasons[j] != seasons[j - 1]
            else f"Played {'their' if len(names) > 1 else 'his'} last game for {place}: {_and(names)}")
    for j, who in out_.items():
        for nm, miss, rest in who:
            add(j, f"{nm} out of the lineup for {'the last ' if rest else ''}{miss} games")
    for j, who in new.items():
        add(j, f"New in the lineup: {_and(who)}")
    if coaches is not None:
        c = coaches.filter(pl.col("team") == abbr).sort("date", "game_id")
        by_game = dict(zip(c["game_id"].to_list(), c["coach"].to_list()))
        for j in range(lo, hi + 1):
            a, b = by_game.get(ids[j - 1]), by_game.get(ids[j])
            if a and b and a != b:
                add(j, f"{b} takes over behind the bench")
    return [{"i": j, "date": dates[j], "text": [t.replace("'", "’") for t in v]} for j, v in sorted(events.items())]


# ---------------------------------------------------------------- run

def run(season: int = CURRENT_SEASON) -> dict:
    teams = _teams()
    fp = json.loads((SITE_DATA / "fingerprints.json").read_text())
    stab = fp["stabilization"]
    g = pl.concat([games(season - 1), games(season)])
    then = last_season_traits(season - 1)
    band = bounce_band()
    lineups = lineup_table([season - 1, season])
    cpath = TABLES / str(season) / "coaches.parquet"
    coaches = pl.concat([pl.read_parquet(p) for p in (TABLES / str(season - 1) / "coaches.parquet", cpath) if p.exists()]) \
        if cpath.exists() else None
    out = {}
    for abbr, t in teams.items():
        mine = g.filter(pl.col("team") == abbr).sort("game_id")
        if mine.is_empty():
            continue
        f, a = mine["xgf_adj"].to_numpy(), mine["xga_adj"].to_numpy()
        brk = change(f, a, seed=sum(map(ord, abbr)))
        traits = then_now(then.get(abbr, {}), fp["teams"].get(abbr, {"games": 0, "dims": {}}), stab) if abbr in fp["teams"] else []
        full = history.franchise_history(abbr)
        out[abbr] = {
            # Every season since 1997-98: [season it started, points %, playoff round 0-5]
            "history": [[h["season"], round(h["pts"] / (2 * h["gp"]), 3) if h["gp"] else None, h["playoffs"]] for h in full if h["season"] < season],
            # Last season and this one: [date, opponent, home, goals for, against, how it ended, xG for, xG against, season]
            "games": [[r["date"], r["opp"], int(r["home"]), r["gf"], r["ga"], r["end"], round(r["xgf_adj"], 3), round(r["xga_adj"], 3), r["season"]]
                      for r in mine.iter_rows(named=True)],
            "change": brk,
            # Around the same time as the change: [{i: game index, date, text: [facts]}]
            "around": around(abbr, mine, brk, lineups, teams, coaches),
            "traits": [{"key": r["key"], "tag": r["tag"], "then": r["then"], "now": r["now"]} for r in traits],
            **story(abbr, t, mine, brk, traits, full),
        }
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season, "window": WINDOW,
               "band": band, "teams": out}
    (SITE_DATA / "trend.json").write_text(json.dumps(payload, separators=(",", ":")))
    return {"teams": len(out), "changes": sum(1 for v in out.values() if v["change"]), "changed": sum(1 for v in out.values() if v["changed"])}


if __name__ == "__main__":
    print(run())
