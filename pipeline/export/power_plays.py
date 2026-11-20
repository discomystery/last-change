"""Every power play of a finished game, and how each unit did: the post-game page's "Power plays" section.

KJ, 2026-11-20: make it easy to see how successful each power play and each unit was: shots, the chances on each
power play, the units, and goals. One small file per finished regular-season game, `powerplays/{game_id}.json`, read
when the post-game page is built (build-only, like matchups/).

A power play here is one unbroken stretch of the shift chart where one team has more skaters and the other has fewer
than five (5-on-4, 5-on-3, 4-on-3, and 6-on-4 with the goalie pulled), so a 5-on-3 that turns into a 5-on-4 is one
row, and 4-on-4 or an empty net at even strength never counts. The NHL counts opportunities by penalty, so its "of N"
can be one or two higher than the rows (two penalties that overlap are one stretch here); the team lines quote the
NHL's own count and goals (`game_stats`). Stretches under MIN_SEC are line-change blips.

Shots are unblocked attempts (on goal, missed, goals) and shots on goal; expected goals leave empty nets out, as
everywhere on the site; penalty shots are left out. `typical` is what an average power play of the same length and
manpower made this season and last (league expected goals per second at 5-on-4 and at 5-on-3), drawn as a dotted
mark beside each row.

Units are tonight's, built the way the team page builds them (`units._quarterback_units`): PP1 is anchored on the
defenseman with the most 5-on-4 time tonight, PP2 on the one with the most time while the first was off, each with the
four skaters most often out with him. Each moment of the power play goes to the unit whose quarterback is on the ice,
else the unit sharing most players, else "other". The other side's penalty killers are listed with their time and the
chances against while they were out.
"""
import json
from collections import defaultdict
from functools import lru_cache

import polars as pl

from pipeline.build.stints import penalty_shot
from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import pending, wording
from pipeline.metrics import units as U

OUT = SITE_DATA / "powerplays"
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
ON_GOAL = {"shot-on-goal", "goal"}
MIN_SEC = 5        # shorter manpower edges are players crossing on a change
JOIN_SEC = 1       # stints this close together with the same side ahead are one power play
OTHER_MIN_SEC = 10  # time with neither unit's quarterback (scrambles, 5-on-3 mixes) is listed only from this much
KILLER_MIN_SEC = 10
PERIOD_SEC = 1200
PERIODS = {1: "1st", 2: "2nd", 3: "3rd", 4: "OT"}

# Infractions in the NHL's codes that read badly with the dashes taken out.
INFRACTION = {
    "too-many-men-on-the-ice": "Too many men",
    "delaying-game-puck-over-glass": "Delay of game (puck over glass)",
    "delaying-game-face-off-violation": "Delay of game (faceoff)",
    "delaying-game-unsuccessful-challenge": "Delay of game (failed challenge)",
    "delaying-game-smothering-puck": "Delay of game (covering the puck)",
    "delaying-game-illegal-play-by-goalie": "Delay of game (goalie)",
    "high-sticking-double-minor": "High-sticking (double minor)",
    "illegal-check-to-head": "Illegal check to the head",
    "unsportsmanlike-conduct-bench": "Unsportsmanlike conduct (bench)",
    "closing-hand-on-puck": "Closing hand on the puck",
    "holding-the-stick": "Holding the stick",
    "goalie-removed-own-mask": "Goalie removed his mask",
}


def infraction(code: str | None) -> str:
    if not code:
        return "Penalty"
    if code in INFRACTION:
        return INFRACTION[code]
    t = code.replace("-", " ")
    return t[:1].upper() + t[1:]


def clock(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def ahead(n_home: int, n_away: int) -> int | None:
    """1 when the home side is on the power play, 0 the visitors, None otherwise (even, or an empty net at evens)."""
    if n_home > n_away and n_away < 5:
        return 1
    if n_away > n_home and n_home < 5:
        return 0
    return None


def manpower(n_own: int, n_opp: int) -> str:
    return f"{n_own}-on-{n_opp}"


def stretches(st: pl.DataFrame) -> list[dict]:
    """The game's power plays from its shift stints: {side, start, end, period, stints: [(start, end, n_own, n_opp,
    own skaters, opp skaters)]}."""
    out, cur = [], None
    for r in st.sort("start").iter_rows(named=True):
        a = ahead(r["n_home"], r["n_away"])
        if a is None:
            if cur:
                out.append(cur)
            cur = None
            continue
        own, opp = ("home", "away") if a else ("away", "home")
        row = (r["start"], r["end"], r[f"n_{own}"], r[f"n_{opp}"], frozenset(r[f"{own}_skaters"] or []), frozenset(r[f"{opp}_skaters"] or []))
        if cur and cur["side"] == a and r["start"] - cur["end"] <= JOIN_SEC:
            cur["end"] = r["end"]
            cur["stints"].append(row)
        else:
            if cur:
                out.append(cur)
            cur = {"side": a, "start": r["start"], "end": r["end"], "period": r["period"], "stints": [row]}
    if cur:
        out.append(cur)
    return [p for p in out if p["end"] - p["start"] >= MIN_SEC]


def _strength_key(n_own: int, n_opp: int) -> str:
    return "5v3" if n_own - n_opp >= 2 else "5v4"


@lru_cache(maxsize=4)
def league_rates(season: int) -> dict[str, float]:
    """League expected goals per second of power play, at 5-on-4 (and anything one man up) and two men up, both goalies
    in, over this season and last. Empty nets and penalty shots are left out."""
    xg, sec = defaultdict(float), defaultdict(float)
    for yr in (season - 1, season):
        d = TABLES / str(yr)
        if not (d / "stints.parquet").exists():
            continue
        games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR)["game_id"]
        st = pl.read_parquet(d / "stints.parquet").filter(pl.col("game_id").is_in(games.to_list()) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
        for r in st.select("n_home", "n_away", "duration").iter_rows():
            a = ahead(r[0], r[1])
            if a is not None:
                own, opp = (r[0], r[1]) if a else (r[1], r[0])
                sec[_strength_key(own, opp)] += r[2]
        ev = (pl.read_parquet(d / "events.parquet", columns=["game_id", "event_id", "type", "is_home", "situation_code", "home_on", "away_on", "home_goalie", "away_goalie"])
              .filter(pl.col("game_id").is_in(games.to_list()) & pl.col("type").is_in(UNBLOCKED) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
              .join(pl.read_parquet(d / "shots_xg_own.parquet"), on=["game_id", "event_id"]))
        for r in ev.iter_rows(named=True):
            if penalty_shot(r["situation_code"]) or r["is_home"] is None:
                continue
            nh, na = len(r["home_on"] or []), len(r["away_on"] or [])
            a = ahead(nh, na)
            if a is None or a != int(r["is_home"]):
                continue
            own, opp = (nh, na) if a else (na, nh)
            xg[_strength_key(own, opp)] += r["xg"] or 0.0
    return {k: xg[k] / sec[k] for k in ("5v4", "5v3") if sec[k]}


def _pen_list(pens: list[dict], pp: dict, name, used: set) -> list[dict]:
    """The penalties behind a power play: those taken by the short side that were still running when it started, or
    were called during it. Each penalty belongs to the first power play it touches (a minor ended by a goal does not
    come back for the next one)."""
    out = []
    short = 1 - pp["side"]
    for p in pens:
        if p["event_id"] in used or int(bool(p["is_home"])) != short or p["pen_type"] not in ("MIN", "MAJ", "BEN", "MAT"):
            continue
        length = (p["pen_min"] or 2) * 60
        if p["sec"] <= pp["start"] + 1 and p["sec"] + length > pp["start"] or pp["start"] < p["sec"] < pp["end"]:
            used.add(p["event_id"])
            who = p["p1"] if p["pen_type"] != "BEN" else None
            out.append({"what": infraction(p["pen_desc"]), "min": p["pen_min"], **({"by": name(who), "id": who} if who else {"by": "bench"})})
    return out


def _ending(pp: dict, goals_for: list, pens: list[dict], period_end: bool, game_end: bool) -> str:
    if goals_for and goals_for[-1]["sec"] == pp["end"]:
        return "goal"
    if any(int(bool(p["is_home"])) == pp["side"] and p["sec"] == pp["end"] and p["pen_type"] in ("MIN", "MAJ", "BEN", "MAT") for p in pens):
        return "penalty"
    if game_end:
        return "game"
    return "killed"


def game(gid: int, g: dict, ev: pl.DataFrame, xg: pl.DataFrame, st: pl.DataFrame, roster: pl.DataFrame, stats: dict[str, dict],
         rates: dict[str, float], place=None) -> dict | None:
    abbr = {0: g["away"], 1: g["home"]}
    info = {r["player_id"]: r for r in roster.iter_rows(named=True)}
    short = wording.short_names(info.values())
    name = lambda pid: short.get(pid, str(pid)) if pid is not None else None
    is_d = {pid: r["pos"] == "D" for pid, r in info.items()}
    pps = stretches(st)
    ev = ev.filter(pl.col("period_type") != "SO").sort("sort")
    length = int(st["end"].max() or 0)
    pens = ev.filter((pl.col("type") == "penalty") & pl.col("is_home").is_not_null()).to_dicts()
    shots = ev.filter(pl.col("type").is_in(UNBLOCKED) & pl.col("is_home").is_not_null()).join(xg, on="event_id", how="left").to_dicts()

    # Each shot to the power play it fell in, from the shooting side's view (the on-ice lists settle a shot on the
    # very second one stint ends and the next begins).
    rows = []
    for i, pp in enumerate(pps):
        tot = defaultdict(float)
        typical = 0.0
        strengths = []
        for a, b, no, nop, _, _ in pp["stints"]:
            k = manpower(no, nop)
            if not strengths or strengths[-1][0] != k:
                strengths.append([k, 0])
            strengths[-1][1] += b - a
            typical += rates.get(_strength_key(no, nop), rates.get("5v4", 0.0)) * (b - a)
        rows.append({"pp": pp, "for": [], "against": [], "typical": typical, "strengths": [s for s in strengths if s[1] >= 3] or strengths[:1]})
    for s in shots:
        if penalty_shot(s["situation_code"]):
            continue
        nh, na = len(s["home_on"] or []), len(s["away_on"] or [])
        a = ahead(nh, na)
        if a is None:
            continue
        for r in rows:
            pp = r["pp"]
            if pp["side"] == a and pp["start"] <= s["sec"] <= pp["end"]:
                mine = int(bool(s["is_home"])) == a
                goalie_in = s["away_goalie"] if s["is_home"] else s["home_goalie"]
                r["for" if mine else "against"].append({
                    "sec": s["sec"], "goal": s["type"] == "goal", "sog": s["type"] in ON_GOAL, "xg": (s["xg"] or 0.0) if goalie_in is not None else 0.0,
                    "who": s["p1"], "a1": s["p2"] if s["type"] == "goal" else None, "a2": s["p3"] if s["type"] == "goal" else None,
                    "on": frozenset((s["home_on"] if s["is_home"] else s["away_on"]) or []), "opp_on": frozenset((s["away_on"] if s["is_home"] else s["home_on"]) or [])})
                break

    def tally(shots: list[dict]) -> dict:
        return {"att": len(shots), "sog": sum(s["sog"] for s in shots), "xg": round(sum(s["xg"] for s in shots), 2), "goals": sum(s["goal"] for s in shots)}

    out_rows, used = [], set()
    for r in rows:
        pp = r["pp"]
        period = pp["period"]
        p0 = (period - 1) * PERIOD_SEC
        goals = [{"by": name(s["who"]), "id": s["who"], "assists": [name(x) for x in (s["a1"], s["a2"]) if x], "at": clock(s["sec"] - pp["start"])}
                 for s in r["for"] if s["goal"]]
        sh_goals = [{"by": name(s["who"]), "id": s["who"], "at": clock(s["sec"] - pp["start"])} for s in r["against"] if s["goal"]]
        out_rows.append({
            "team": abbr[pp["side"]], "h": pp["side"], "period": PERIODS.get(period, f"{period - 3}OT" if period > 4 else "OT"),
            "clock": clock(pp["start"] - p0), "start": pp["start"], "sec": pp["end"] - pp["start"],
            "strength": [[k, v] for k, v in r["strengths"]], "pens": _pen_list(pens, pp, name, used),
            **tally(r["for"]), "typical": round(r["typical"], 2), "scorers": goals,
            "against": {**tally(r["against"]), "scorers": sh_goals},
            "end": _ending(pp, [s for s in r["for"] if s["goal"]], pens, False, pp["end"] >= length),
        })

    # Units, per side, from tonight's power-play time.
    units, killers = {}, {}
    for side in (0, 1):
        mine = [r for r in rows if r["pp"]["side"] == side]
        if not mine:
            continue
        spans = [(b - a, on) for r in mine for a, b, no, nop, on, _ in r["pp"]["stints"] if (no, nop) == (5, 4)]
        if not spans:  # only 5-on-3s or 4-on-3s tonight: build the units on all of it
            spans = [(b - a, on) for r in mine for a, b, _, _, on, _ in r["pp"]["stints"]]
        toi = defaultdict(float)
        for sec, on in spans:
            for p in on:
                toi[p] += sec
        members, anchors, _ = U._quarterback_units(spans, toi, is_d, total_sec=sum(s for s, _ in spans))

        def credit(on: frozenset) -> int | None:
            there = [a in on for a in anchors]
            if sum(there) == 1:
                return there.index(True)
            counts = [len(m & on) for m in members]
            return counts.index(max(counts)) if counts and max(counts) >= 3 else None

        n = len(members)
        secs, shots_for, shots_against = [0.0] * (n + 1), [[] for _ in range(n + 1)], [[] for _ in range(n + 1)]
        ptoi = [defaultdict(float) for _ in range(n + 1)]
        all_toi = defaultdict(float)
        kill_toi, kill_shots = defaultdict(float), defaultdict(list)
        for r in mine:
            for a, b, _, _, on, opp in r["pp"]["stints"]:
                k = credit(on)
                k = n if k is None else k
                secs[k] += b - a
                for p in on:
                    ptoi[k][p] += b - a
                    all_toi[p] += b - a
                for p in opp:
                    kill_toi[p] += b - a
            for s in r["for"]:
                k = credit(s["on"])
                shots_for[n if k is None else k].append(s)
                for p in s["opp_on"]:
                    kill_shots[p].append(s)
            for s in r["against"]:
                k = credit(s["opp_on"])
                shots_against[n if k is None else k].append(s)
        lst = []
        for k in range(n + 1):
            if k == n and secs[k] < OTHER_MIN_SEC and not shots_for[k]:
                continue
            if k < n:
                players = sorted(members[k], key=lambda p: (p != anchors[k], -ptoi[k].get(p, 0.0)))
            else:
                players = sorted((p for p in ptoi[k] if ptoi[k][p] >= 5), key=lambda p: -ptoi[k][p])[:5]
            lst.append({"label": f"PP{k + 1}" if k < n else "Other", "qb": anchors[k] if k < n and is_d.get(anchors[k]) else None,
                        "players": [{"id": p, "n": name(p), "pos": info.get(p, {}).get("pos"), "sec": round(all_toi.get(p, 0.0))} for p in players],
                        "sec": round(secs[k]), **tally(shots_for[k]), "typical": round(secs[k] * rates.get("5v4", 0.0), 2),
                        "against": tally(shots_against[k])})
        units[abbr[side]] = lst
        killers[abbr[1 - side]] = sorted(
            [{"id": p, "n": name(p), "pos": info.get(p, {}).get("pos"), "sec": round(t), **tally(kill_shots[p])}
             for p, t in kill_toi.items() if t >= KILLER_MIN_SEC],
            key=lambda x: (x["pos"] == "D", -x["sec"]))

    teams = {}
    for side in (0, 1):
        a = abbr[side]
        mine = [r for r in out_rows if r["h"] == side]
        sec = sum(r["sec"] for r in mine)
        teams[a] = {"rows": len(mine), "opps": stats.get(a, {}).get("pp_opps", len(mine)), "pp_goals": stats.get(a, {}).get("pp_goals", sum(r["goals"] for r in mine)),
                    "sec": sec, "att": sum(r["att"] for r in mine), "sog": sum(r["sog"] for r in mine), "xg": round(sum(r["xg"] for r in mine), 2),
                    "typical": round(sum(r["typical"] for r in mine), 2),
                    "against": {k: (round(sum(r["against"][k] for r in mine), 2) if k == "xg" else sum(r["against"][k] for r in mine)) for k in ("att", "sog", "xg", "goals")}}
    if not out_rows and not any(t["opps"] for t in teams.values()):
        return None
    return {"game_id": gid, "away": abbr[0], "home": abbr[1], "rate": {k: round(v * 120, 3) for k, v in rates.items()}, "teams": teams,
            "list": out_rows, "units": units, "killers": killers, "lede": lede(abbr, teams, place)}


def lede(abbr: dict[int, str], teams: dict[str, dict], place=None) -> str:
    """One plain sentence per team: goals of chances, time, and the chances against a typical power play of that time."""
    place = place or (lambda a: a)
    parts = []
    for side in (0, 1):
        a = abbr[side]
        t = teams[a]
        if not t["opps"] and not t["rows"]:
            parts.append(f"{place(a)[:1].upper() + place(a)[1:]} never went on the power play.")
            continue
        ratio = t["xg"] / t["typical"] if t["typical"] else 1.0
        how = ("far more than" if ratio >= 1.6 else "more than" if ratio >= 1.15 else "well under" if ratio <= 0.6 else "less than" if ratio <= 0.85 else "about what")
        tail = f"{how} a typical power play makes in that time ({t['typical']:.1f})" if how != "about what" else f"about what a typical power play makes in that time"
        n = max(t["opps"], t["rows"])
        lead = f"{place(a)} went {t['pp_goals']} for {n}"
        cap = lambda x: x[:1].upper() + x[1:]
        parts.append(f"{cap(lead)} on the power play, with {t['sog']} shot{'s' if t['sog'] != 1 else ''} on goal and {t['xg']:.1f} expected goals in {clock(t['sec'])}, {tail}.")
    return " ".join(parts)


def run(season: int = CURRENT_SEASON) -> dict:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    ids = [g for g in games["game_id"].to_list() if g not in pending.games(season)]  # unfinished shift charts wait
    games = games.filter(pl.col("game_id").is_in(ids))
    ev = pl.read_parquet(d / "events.parquet").filter(pl.col("game_id").is_in(ids))
    xg = pl.read_parquet(d / "shots_xg_own.parquet").filter(pl.col("game_id").is_in(ids))
    st = pl.read_parquet(d / "stints.parquet").filter(pl.col("game_id").is_in(ids))
    roster = pl.read_parquet(d / "players.parquet").filter(pl.col("game_id").is_in(ids))
    stats = defaultdict(dict)
    if (d / "game_stats.parquet").exists():
        for r in pl.read_parquet(d / "game_stats.parquet").iter_rows(named=True):
            stats[r["game_id"]][r["team"]] = r
    rates = league_rates(season)
    teams = json.loads((SITE_DATA / "teams.json").read_text()) if (SITE_DATA / "teams.json").exists() else []
    places = {t["abbr"]: wording.place(t["place"]) for t in teams}
    place = lambda a: places.get(a, a)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    n = 0
    for g in games.iter_rows(named=True):
        gid = g["game_id"]
        sub = st.filter(pl.col("game_id") == gid)
        if sub.is_empty():
            continue
        out = game(gid, g, ev.filter(pl.col("game_id") == gid), xg.filter(pl.col("game_id") == gid).drop("game_id"), sub,
                   roster.filter(pl.col("game_id") == gid), stats.get(gid, {}), rates, place)
        if out is None:
            continue
        (OUT / f"{gid}.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        n += 1
    return {"games": n}


if __name__ == "__main__":
    print(run())
