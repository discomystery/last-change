"""Post-game report cards: did each player do the jobs he is relied on for, and who stood out somewhere new.

Everything about a player going into a game is judged only from games before it (last season plus this season up
to the day before), so a card never changes once the game is played. Two kinds of job, matching the key-players
cards on team pages:
- relied on for: the power play (1:30+ a game on it lately), the penalty kill (1:00+ a game), faceoffs (8+ draws a
  game as a centre);
- better than most at: his own numbers (shot attempts, chances, hits, blocks, takeaways, faceoffs) at the 75th
  percentile or better among last season's regulars at his position, or driving / preventing chances at 5-on-5 at
  the 75th percentile or better over the two previous seasons' isolated ratings (rapm.py).

Each job is graded the way preview calls are: "held" means he reached a typical night for himself in the ice time he
got, "partly" means he beat a benchmark (an average player at his position, the league, or his own team with him off
the ice), "missed" means neither. A typical night is the level he reaches in about half his games, not his average:
single-game numbers are lopsided (a few big nights pull the average up), so an average would be missed more often
than not even by a player having his usual season. Too little ice time for the job is "na" and not counted.

"Stood out" notes look only at things that are NOT his jobs: a count so far above his own usual pace that it would
happen less than one game in fifty, and at least twice what an average player at his position would manage.

Writes site/public/data/jobs/{game_id}.json for every finished regular-season game.
"""
import json
import math
from collections import defaultdict

import numpy as np
import polars as pl

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, SITE_DATA, TABLES
from pipeline.metrics import players as P
from pipeline.metrics import rink_bias

OUT = SITE_DATA / "jobs"
STRONG = 75  # percentile that makes something a strength (the same bar as "Better than most at" on player pages)
MAX_JOBS = 3
STAND_OUT_P = 0.02  # chance of a night this big at his own usual pace
STAND_OUT_X = 2.0  # and at least this many times an average player's count
REF_GAMES = 20  # games last season to count in the reference pool
MIN_SEC5 = 360  # 5-on-5 time needed to judge a 5-on-5 job
MIN_SPECIAL = 60  # power-play or penalty-kill time needed to judge those jobs
MIN_DRAWS = 8
ROLE = {"pp": 90.0, "pk": 60.0, "draws": 8.0}  # per game, over his earlier games, to be relied on for it
ROLE_GAMES = 5
ONICE_K = 36000.0  # seconds of play at which his own on-ice rate counts as much as the benchmark
SPECIAL_K = 7200.0
EXTRA = ["pp_xgf", "sh_xga", "g", "a1", "a2", "sog", "hits", "blocks", "takes"]

# His own counts: (count column, time column, plural noun, singular noun, where).
COUNTS = {
    "shooting": ("icf5", "sec5", "shot attempts", "shot attempt", " at 5-on-5"),
    "chances": ("ixg5", "sec5", None, None, " at 5-on-5"),
    "hits": ("hits_adj", "sec", "hits", "hit", ""),
    "blocks": ("blocks_adj", "sec", "blocked shots", "blocked shot", ""),
    "takeaways": ("takes_adj", "sec", "takeaways", "takeaway", ""),
}
# What the numbers on a card measure, in words (shown under each job's label).
UNIT = {"shooting": "shot attempts at 5-on-5", "chances": "expected goals from his own shots at 5-on-5", "hits": "hits", "blocks": "blocked shots",
        "takeaways": "takeaways", "faceoffs": "faceoffs won", "offImpact": "expected goals per 60 with him on at 5-on-5",
        "defImpact": "expected goals per 60 allowed with him on at 5-on-5", "pp": "expected goals per 60 on the power play",
        "pk": "expected goals per 60 allowed on the penalty kill"}
RAW_COL = {"hits": "hits", "blocks": "blocks", "takeaways": "takes"}
LABEL = {"shooting": "Shooting", "chances": "Getting to dangerous spots", "hits": "Hitting", "blocks": "Blocking shots",
         "takeaways": "Taking the puck away", "faceoffs": "Faceoffs", "offImpact": "Creating chances at 5-on-5",
         "defImpact": "Preventing chances at 5-on-5", "pp": "Power play", "pk": "Kills penalties"}


def clock(sec: float) -> str:
    s = round(sec)
    return f"{s // 60}:{s % 60:02d}"


def poisson_tail(k: float, lam: float) -> float:
    """Chance of k or more events when lam are expected."""
    k = int(round(k))
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def binom_tail(k: int, n: int, p: float) -> float:
    """Chance of k or more successes in n tries."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def poisson_median(lam: float) -> int:
    """The count he reaches in at least half of games when lam are expected: a typical night, not an average one.
    (With 1.2 hits expected, one hit is a typical night; asking for the average would mean two.)"""
    k, term, cdf = 0, math.exp(-lam), 0.0
    while True:
        cdf += term
        if cdf >= 0.5:
            return k
        k += 1
        term *= lam / k


def grade(v: float, usual: float, bench: float, higher: bool = True) -> str:
    """held: his usual level or better; partly: beyond the benchmark; missed: neither."""
    beyond = (lambda a, b: a >= b - 1e-9) if higher else (lambda a, b: a <= b + 1e-9)
    if beyond(v, usual):
        return "held"
    if beyond(v, bench):
        return "partly"
    return "missed"


def plus_minus(season: int) -> dict[tuple[int, int], int]:
    """Plus-minus per player-game, straight from the NHL boxscore (rebuilding it from play-by-play disagrees with the
    league in a few games where the recorded manpower at a goal is off)."""
    import gzip
    out = {}
    for f in (RAW / str(season) / "box").glob("*.json.gz"):
        d = json.loads(gzip.decompress(f.read_bytes()))
        for side in ("awayTeam", "homeTeam"):
            for grp in ("forwards", "defense"):
                for p in (d.get("playerByGameStats") or {}).get(side, {}).get(grp, []):
                    out[(d["id"], p["playerId"])] = p.get("plusMinus") or 0
    return out


def night(box: dict, xg_share: float | None) -> tuple[str | None, str | None]:
    """The whole game, apart from his usual jobs: a big night (2+ points, +2 or better, or his team owning the chances
    with him on) or a rough one (-3 or worse with no points). Shown first on the card, so a player who had a great
    game in other ways never reads as "mixed" just because his usual jobs went quiet."""
    pts, pm = box["g"] + box["a"], box["pm"]
    bits = []
    if box["g"]:
        bits.append(f"{box['g']} {'goal' if box['g'] == 1 else 'goals'}")
    if box["a"]:
        bits.append(f"{box['a']} {'assist' if box['a'] == 1 else 'assists'}")
    if pm:
        bits.append(f"{'+' if pm > 0 else '−'}{abs(pm)}")
    owned = xg_share is not None and xg_share >= 0.7
    if pts >= 2 or pm >= 2 or (pts >= 1 and pm >= 1 and owned):
        if owned:
            bits.append(f"{round(100 * xg_share)}% of the 5-on-5 chances with him on")
        return "big", ", ".join(bits)
    if pm <= -3 and pts == 0:
        return "rough", ", ".join(bits)
    return None, None


def summary(verdicts: list[str]) -> str | None:
    """did: two thirds of the way or better (held counts 1, partly a half); didnt: a third or less; else mixed."""
    if not verdicts:
        return None
    score = sum({"held": 1.0, "partly": 0.5}.get(v, 0.0) for v in verdicts) / len(verdicts)
    return "did" if score >= 2 / 3 - 1e-9 else "didnt" if score <= 1 / 3 + 1e-9 else "mixed"


def shrink(n: float, v: float, k: float, prior: float) -> float:
    return (n * v + k * prior) / (n + k)


def _table(season: int) -> pl.DataFrame:
    """player_game for a season, rebuilt if it was saved before the power-play column existed."""
    path = TABLES / str(season) / "player_game.parquet"
    need = set(P.COLS) | set(EXTRA)
    if not path.exists() or not need <= set(pl.read_parquet_schema(path)):
        from pipeline.metrics import player_game
        player_game.build(season)
    return pl.read_parquet(path).filter(pl.col("pos") != "G")


def typical_ratios(last: pl.DataFrame) -> dict[str, float]:
    """Single-game chance numbers are lopsided: a few big nights pull the average up, so most nights fall short of it.
    For each measure, the median over last season's games of (that game's rate / the player's season rate) turns a
    player's average into a typical night, one he reaches about half the time."""
    def ratio(num: str, den: str, min_sec: float, role: float | None = None) -> float:
        t = last.group_by("player_id").agg(pl.col(num).sum().alias("N"), pl.col(den).sum().alias("D"), pl.len().alias("n")).filter((pl.col("n") >= REF_GAMES) & (pl.col("N") > 0))
        if role:
            t = t.filter(pl.col("D") / pl.col("n") >= role)
        j = last.join(t, on="player_id").filter(pl.col(den) >= min_sec)
        return float(((j[num] / j[den]) / (j["N"] / j["D"])).median())
    return {"pp": ratio("pp_xgf", "sec_pp", MIN_SPECIAL, ROLE["pp"]), "pk": ratio("sh_xga", "sec_pk", MIN_SPECIAL, ROLE["pk"]),
            "chances": ratio("ixg5", "sec5", MIN_SEC5), "offImpact": ratio("on_xgf_adj", "sec5", MIN_SEC5), "defImpact": ratio("on_xga_adj", "sec5", MIN_SEC5)}


class Reference:
    """Last season's regulars at each position: the average and the spread pre-game levels are placed against."""

    def __init__(self, season: int, last: pl.DataFrame):
        self.stab = P.stabilization()
        self.grp, self.sub = P._groups(last), P._groups(last, P.SUB)
        sums = {pid: g.select(P.COLS).to_numpy() for (pid,), g in last.group_by("player_id", maintain_order=True)}
        self.mu, self.pool = {}, {}
        for g in ("F", "D", "C", "W"):
            members = [p for p in sums if (self.sub.get(p) == g if g in ("C", "W") else self.grp.get(p) == g)]
            tot = sum(sums[p].sum(axis=0) for p in members)
            base = "D" if g == "D" else "F"
            self.mu[g], self.pool[g] = {}, {}
            for t in self.stab[base]:
                self.mu[g][t] = float(P._value(t, tot))
                k = self.stab[base][t]["k"]
                self.pool[g][t] = np.array([shrink(float(P._weight(t, s)), float(np.nan_to_num(P._value(t, s))), k, self.mu[g][t])
                                            for s in (sums[p].sum(axis=0) for p in members if len(sums[p]) >= REF_GAMES)])

    def usual(self, trait: str, g: str, sb: str, prev: np.ndarray | None, before: np.ndarray) -> tuple[float, float, float, str]:
        """His level going into the game (last season pulled toward the position average, then this season's earlier
        games on top, as on player pages), the position average, his percentile, and who he is compared with."""
        peer = P.peer(g, sb, trait)
        k = self.stab[g][trait]["k"]
        prior = self.mu[peer][trait]
        if prev is not None and P._weight(trait, prev) > 0:
            prior = shrink(float(P._weight(trait, prev)), float(np.nan_to_num(P._value(trait, prev))), k, prior)
        v = shrink(float(P._weight(trait, before)), float(np.nan_to_num(P._value(trait, before))), k, prior)
        return v, self.mu[peer][trait], P._pct(v, self.pool[peer][trait], P.TRAITS[trait][2]), peer


def impact_pcts(season: int) -> dict[int, dict[str, float]]:
    """Percentiles of each skater's isolated 5-on-5 offense and defense over the two seasons before this one."""
    from pipeline.metrics import rapm

    fit = rapm.run([season - 2, season - 1], P.RAPM_LAMBDA)
    gp = pl.concat([P.load(s).select("player_id", "pos") for s in (season - 2, season - 1)])
    info = {r["player_id"]: (r["n"], P.GROUP[r["pos"]]) for r in gp.group_by("player_id").agg(pl.len().alias("n"), pl.col("pos").mode().first()).iter_rows(named=True)}
    out = {}
    for g in ("F", "D"):
        rows = [r for r in fit.iter_rows(named=True) if info.get(r["player_id"], (0, None))[1] == g]
        for col, key in (("off", "offImpact"), ("def", "defImpact")):
            pool = np.array([r[col] for r in rows if info[r["player_id"]][0] >= 40])
            for r in rows:
                out.setdefault(r["player_id"], {})[key] = P._pct(r[col], pool, True)
    return out


def run(season: int = CURRENT_SEASON) -> dict:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    info = {r["game_id"]: r for r in games.iter_rows(named=True)}
    abbr = {**dict(zip(games["home_id"], games["home"])), **dict(zip(games["away_id"], games["away"]))}
    names = {r["player_id"]: f'{r["first"][0]}. {r["last"]}' for r in pl.read_parquet(d / "players.parquet").sort("game_id").iter_rows(named=True)}
    last = _table(season - 1).join(pl.read_parquet(TABLES / str(season - 1) / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id"), on="game_id")
    now = _table(season).join(games.select("game_id", "date", "home_id"), on="game_id").sort("player_id", "date", "game_id")

    ref = Reference(season, last)
    impact = impact_pcts(season)
    typical = typical_ratios(last)
    fac = {r["team_id"]: r for r in rink_bias.factors().iter_rows(named=True)}
    hr = rink_bias.home_road()
    special_cols = ["sec_pp", "pp_xgf", "sec_pk", "sh_xga", "fow", "fol"]
    lt = last.select(special_cols).sum().row(0, named=True)
    # League power-play and penalty-kill rates per 60: every skater on the ice shares the same chances and the same
    # seconds, so the ratio of the sums is the team rate.
    league = {"pp": 3600 * lt["pp_xgf"] / lt["sec_pp"], "pk": 3600 * lt["sh_xga"] / lt["sec_pk"]}
    prev_all = {pid: (g.height, g.select(P.COLS).to_numpy().sum(axis=0), g.select(special_cols).sum().row(0, named=True))
                for (pid,), g in last.group_by("player_id")}
    grp = {**ref.grp, **P._groups(now)}
    sub = {**ref.sub, **P._groups(now, P.SUB)}

    pm = plus_minus(season)
    cards = defaultdict(list)
    for (pid,), rows in now.group_by("player_id", maintain_order=True):
        m = rows.select(P.COLS).to_numpy()
        sp = rows.select(special_cols).to_numpy()
        ex = rows.select("game_id", "team_id", "home_id", "is_home", "date", "pos", *EXTRA).to_dicts()
        n_prev, prev, prev_sp = prev_all.get(pid, (0, None, {c: 0.0 for c in special_cols}))
        cum = np.vstack([np.zeros(m.shape[1]), np.cumsum(m, axis=0)])
        cum_sp = np.vstack([np.zeros(sp.shape[1]), np.cumsum(sp, axis=0)])
        for i, e in enumerate(ex):
            j = i
            while j > 0 and ex[j - 1]["date"] == e["date"]:  # only games on earlier days count as "before"
                j -= 1
            before_sp = {c: prev_sp[c] + cum_sp[j][k] for k, c in enumerate(special_cols)}
            card = player_card(e, grp.get(pid, P.GROUP.get(e["pos"], "F")), sub.get(pid, P.SUB.get(e["pos"], "W")), prev, cum[j], m[i], n_prev + j, before_sp,
                               ref, impact.get(pid, {}), fac, hr, league, typical)
            if card:
                card["id"] = pid
                card["box"]["pm"] = pm.get((e["game_id"], pid), 0)
                f, a = float(m[i][P.C["on_xgf_adj"]]), float(m[i][P.C["on_xga_adj"]])
                share = f / (f + a) if m[i][P.C["sec5"]] >= 600 and f + a > 0 else None
                card["night"], card["night_why"] = night(card["box"], share)
                cards[e["game_id"]].append(card)

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for gid, cs in cards.items():
        g = info[gid]
        for c in cs:
            c["name"] = names.get(c["id"], "?")
            c["team"] = abbr.get(c.pop("team_id"))
        out = {"game_id": gid, "away": g["away"], "home": g["home"], "players": [], "stood_out": []}
        for t in (g["away"], g["home"]):
            mine = sorted((c for c in cs if c["team"] == t), key=lambda c: -c["toi_sec"])
            out["players"] += [c for c in mine if c["jobs"] or c["night"]]
        # The most unlikely nights first, at most two of any one kind so hits can't crowd out everything else.
        notes, per_kind = [], defaultdict(int)
        for s in sorted(({**s, "id": c["id"], "name": c["name"], "team": c["team"]} for c in cs for s in c["standouts"]), key=lambda s: s["p"]):
            if per_kind[s["key"]] < 2 and len(notes) < 5:
                notes.append(s)
                per_kind[s["key"]] += 1
        out["stood_out"] = notes
        for c in cs:
            c.pop("standouts")
        (OUT / f"{gid}.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    return {"games": len(cards)}


def player_card(e: dict, g: str, sb: str, prev, before, tonight, n_before: int, before_sp: dict, ref: Reference, imp: dict, fac: dict, hr: dict, league: dict, typical: dict) -> dict | None:
    T = lambda c: float(tonight[P.C[c]])
    sec, sec5 = T("sec"), T("sec5")
    if sec < 60:
        return None
    jobs, outs = [], []
    both = before if prev is None else before + prev

    def arena(event: str) -> float:
        """Recorded events per arena-adjusted event in this building, for his side: turns his usual pace back into
        what this arena's scorer would be expected to credit."""
        f = (fac.get(e["home_id"]) or {}).get(event, 1.0)
        return f / (hr[event]["w_home"] if e["is_home"] else hr[event]["w_away"])

    # Roles: what his coach has been using him for.
    per_game = lambda c: before_sp[c] / n_before if n_before else 0.0
    # A role his team never needed in this game (no power plays, no penalties) is left off the card.
    if n_before >= ROLE_GAMES and per_game("sec_pp") >= ROLE["pp"] and T("sec_pp") > 0:
        jobs.append(special_job("pp", e, T("sec_pp"), float(e["pp_xgf"]), before_sp["sec_pp"], before_sp["pp_xgf"], league["pp"], per_game("sec_pp"), typical["pp"]))
    if n_before >= ROLE_GAMES and per_game("sec_pk") >= ROLE["pk"] and T("sec_pk") > 0:
        jobs.append(special_job("pk", e, T("sec_pk"), float(e["sh_xga"]), before_sp["sec_pk"], before_sp["sh_xga"], league["pk"], per_game("sec_pk"), typical["pk"]))

    # Faceoffs: a role for a centre who takes plenty, and a strength for one who wins them.
    if "faceoffs" in ref.stab[g]:
        usual, avg, pct, peer = ref.usual("faceoffs", g, sb, prev, before)
        draws, won = int(T("fow") + T("fol")), int(T("fow"))
        role = n_before >= ROLE_GAMES and (before_sp["fow"] + before_sp["fol"]) / n_before >= ROLE["draws"]
        experienced = float(P._weight("faceoffs", both)) >= 200
        if role or (pct >= STRONG and experienced):
            job = {"key": "faceoffs", "label": LABEL["faceoffs"], "why": "strength" if pct >= STRONG and experienced else "role", "pct": round(pct) if experienced else None, "vs": peer}
            if draws < MIN_DRAWS:
                job |= {"verdict": "na", "text": f"Took only {draws} {'draw' if draws == 1 else 'draws'}, too few to judge."}
            else:
                bench = max(avg, 50.0)
                job |= {"verdict": grade(100 * won / draws, usual, bench), "tonight": f"{won} of {draws}", "usual": f"{usual:.0f}%", "bench": f"{bench:.0f}%", "bench_is": "Even",
                        "text": f"Won {won} of {draws} draws ({100 * won / draws:.0f}%). Usual for him: {usual:.0f}%."}
            jobs.append(job)
        elif draws >= 12 and won / draws >= 0.65 and experienced and pct < 50:
            p = binom_tail(won, draws, usual / 100)
            if p < STAND_OUT_P:
                outs.append({"key": "faceoffs", "label": LABEL["faceoffs"], "p": round(p, 4), "text": f"Won {won} of {draws} draws, when he usually wins {usual:.0f}%."})

    # Strengths among his own counts, and stand-out nights in the ones that aren't.
    for t, (col, tcol, many, one, where) in COUNTS.items():
        usual, avg, pct, peer = ref.usual(t, g, sb, prev, before)
        time_ = T(tcol)
        exp_u, exp_a = usual * time_ / 3600, avg * time_ / 3600
        actual = T(col)
        enough = sec5 >= MIN_SEC5
        mult = arena(t if t != "takeaways" else "takes") if t in RAW_COL else 1.0
        shown = float(e[RAW_COL[t]]) if t in RAW_COL else actual
        if pct >= STRONG:
            job = {"key": t, "label": LABEL[t], "why": "strength", "pct": round(pct), "vs": peer}
            if not enough:
                job |= {"verdict": "na", "text": f"Only {clock(sec5)} at 5-on-5, too little to judge."}
            elif many is None:
                typ_u, typ_a = exp_u * typical[t], exp_a * typical[t]
                job |= {"verdict": grade(actual, typ_u, typ_a), "tonight": f"{actual:.2f}", "usual": f"{typ_u:.2f}", "bench": f"{typ_a:.2f}", "bench_is": "Average",
                        "text": f"His shots were worth {actual:.2f} expected goals in {clock(time_)}{where}. Typical for him in that time: {typ_u:.2f}. "
                                f"Average {word(peer)}: {typ_a:.2f}."}
            else:
                # Counts are graded against a typical night for that much ice time (in this arena's scoring, for
                # hits, blocks and takeaways), since a count can't reach a fractional average.
                n = round(shown)
                typ_u, typ_a = poisson_median(exp_u * mult), poisson_median(exp_a * mult)
                if typ_u < 1:
                    continue  # a typical night is none at all, so one game can't show the strength
                arena_note = " Both allow for this arena’s scorer." if t in RAW_COL else ""
                job |= {"verdict": grade(n, typ_u, typ_a), "tonight": str(n), "usual": str(typ_u), "bench": str(typ_a), "bench_is": "Average",
                        "text": f"{count(n, one, many)}{where} in {clock(time_)}. Typical for him in that time: {typ_u}. "
                                f"Average {word(peer)}: {typ_a}.{arena_note}"}
            jobs.append(job)
        elif enough and pct < 50:
            if many is None:
                if actual >= max(0.6, 3 * exp_u) and actual >= STAND_OUT_X * exp_a:
                    # No exact tail chance for a sum of shot values; "p" here only orders the notes.
                    outs.append({"key": t, "label": LABEL[t], "p": round(min(0.019, exp_u / actual / 20), 4),
                                 "text": f"His own shots at 5-on-5 were worth {actual:.2f} expected goals, about {actual / max(exp_u, 0.01):.0f} times his usual for that much ice time."})
                continue
            p = poisson_tail(actual, exp_u)
            if p < STAND_OUT_P and actual >= STAND_OUT_X * max(exp_a, 0.5):
                n = round(shown)
                outs.append({"key": t, "label": LABEL[t], "p": round(p, 4),
                             "text": f"{n} {one if n == 1 else many}{where} in {clock(time_)}, when his usual pace would give about {exp_u * mult:.1f}{' in this arena' if t in RAW_COL else ''}."})

    # Driving and preventing chances at 5-on-5, for players rated for it over the two previous seasons.
    for key, col, team_col, higher in (("offImpact", "on_xgf_adj", "t_xgf_adj", True), ("defImpact", "on_xga_adj", "t_xga_adj", False)):
        pct = imp.get(key)
        if pct is None or pct < STRONG:
            continue
        job = {"key": key, "label": LABEL[key], "why": "strength", "pct": round(pct), "vs": g}
        if sec5 < MIN_SEC5:
            jobs.append(job | {"verdict": "na", "text": f"Only {clock(sec5)} at 5-on-5, too little to judge."})
            continue
        off_sec = T("t_sec5") - sec5
        if off_sec < 300:
            jobs.append(job | {"verdict": "na", "text": "His team was hardly ever on the ice without him at 5-on-5, so there is nothing to compare with."})
            continue
        bench_all = 3600 * float(both[P.C[team_col]] - both[P.C[col]]) / max(1.0, float(both[P.C["t_sec5"]] - both[P.C["sec5"]]))
        n_on = float(both[P.C["sec5"]])
        mine = 3600 * float(both[P.C[col]]) / n_on if n_on else bench_all
        usual = shrink(n_on, mine, ONICE_K, bench_all) * typical[key]
        v = 3600 * T(col) / sec5
        off = 3600 * (T(team_col) - T(col)) / off_sec
        verb = "created" if higher else "allowed"
        jobs.append(job | {"verdict": grade(v, usual, off, higher), "tonight": f"{v:.1f}", "usual": f"{usual:.1f}", "bench": f"{off:.1f}", "bench_is": "Without him",
                           "text": f"His team {verb} {v:.1f} expected goals per 60 with him on at 5-on-5. Typical with him on: {usual:.1f}. "
                                   f"With him off the ice in this game: {off:.1f}."})

    order = {"pp": 0, "pk": 1}
    jobs.sort(key=lambda j: (j["verdict"] == "na", order.get(j["key"], 2), -(j.get("pct") or 0)))
    jobs = jobs[:MAX_JOBS]
    for j in jobs:
        j["unit"] = UNIT[j["key"]]
    return {"team_id": e["team_id"], "pos": e["pos"], "toi_sec": round(sec), "toi": clock(sec), "toi5": clock(sec5), "toi_pp": clock(T("sec_pp")), "toi_pk": clock(T("sec_pk")),
            "box": {"g": int(e["g"]), "a": int(e["a1"] + e["a2"]), "sog": int(e["sog"]), "hits": int(e["hits"]), "blocks": int(e["blocks"])},
            "jobs": jobs, "summary": summary([j["verdict"] for j in jobs if j["verdict"] != "na"]), "standouts": outs}


def special_job(key: str, e: dict, sec: float, xg: float, sec_before: float, xg_before: float, league: float, per_game: float, ratio: float) -> dict:
    """Power play (chances created with him on, higher is better) or penalty kill (chances allowed, lower is better)."""
    pp = key == "pp"
    job = {"key": key, "label": LABEL[key], "why": "role", "pct": None, "per_game": clock(per_game)}
    if sec < MIN_SPECIAL:
        what = "on the power play" if pp else "killing penalties"
        return job | {"verdict": "na", "text": f"Only {clock(sec)} {what}, too little to judge."}
    usual = shrink(sec_before, 3600 * xg_before / sec_before if sec_before else league, SPECIAL_K, league) * ratio
    typ_league = league * ratio
    v = 3600 * xg / sec
    if pp:
        text = (f"His team created {v:.1f} expected goals per 60 in his {clock(sec)} on the power play. "
                f"Typical with him on: {usual:.1f}. Typical power play: {typ_league:.1f}.")
    else:
        text = (f"The other team created {v:.1f} expected goals per 60 in his {clock(sec)} killing penalties. "
                f"Typical with him on: {usual:.1f}. Against a typical penalty kill: {typ_league:.1f}.")
    return job | {"verdict": grade(v, usual, typ_league, pp), "tonight": f"{v:.1f}", "usual": f"{usual:.1f}", "bench": f"{typ_league:.1f}", "bench_is": "League", "text": text}


def count(n: int, one: str, many: str) -> str:
    return f"No {many}" if n == 0 else f"{n} {one if n == 1 else many}"


def word(peer: str) -> str:
    return {"F": "forward", "D": "defenseman", "C": "centre", "W": "winger"}[peer]


if __name__ == "__main__":
    print(run())
