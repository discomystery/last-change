"""The fuller preview (Sora, 2026-10-30, thread on improving the site's voice): after the opening paragraph (meetings
and form), three more paragraphs built from the same kinds of facts as the hand-written drafts in `voice/runs/`:
how each team plays (its most distinctive habits, and its scoring at the ends of the league), who carries the
scoring, and who is expected in net. Everything comes from games before the game's own date, so a rebuilt preview
says what a live one would have."""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON
from pipeline.export.grammar import and_list, cap, num, ordinal_word

# Each habit as something a team does, at the high end of its scale and at the low end (plural verbs: teams are
# "they"). Quality is the average shot's chance of going in, never a place on the ice (wording notes, 2026-10-10).
HABITS = {
    "volume": ("throw a lot of pucks at the net", "pick their shots"),
    "quality": ("take a lot of high-percentage shots", "take a lot of low-percentage shots"),
    "rebounds": ("get plenty of second chances", "rarely get second chances"),
    "turnover": ("pounce on turnovers", "rarely create chances off turnovers"),
    "dShots": ("get their defensemen shooting", "leave the shooting to their forwards"),
    "inClose": ("get their shots from in close", "do a lot of their shooting from distance"),
    "shotsThrough": ("get their shots through to the net", "get a lot of their shots blocked"),
    "suppression": ("keep shots away from their net", "give up a lot of shots"),
    "qualityAllowed": ("keep dangerous shots away", "give up a lot of dangerous shots"),
    "breakdowns": ("rarely break down in front of their goalie", "break down in front of their goalie a lot"),
    "longShifts": ("rarely get pinned in their own end", "get pinned in their own end on long shifts"),
    "blocking": ("block a lot of shots", "don't block many shots"),
    "pointAllowed": ("let the point shots come and guard the net", "take away shots from the point"),
    "icing": ("rarely ice the puck", "ice the puck a lot"),
    "goalie": ("have had some of the best goaltending in the league", "have had shaky goaltending"),
    "pace": ("play fast, open games", "play slow, tight games"),
    "forecheck": ("hound the puck on the forecheck", "sit back rather than chase on the forecheck"),
    "physical": ("hit a lot", "rarely throw hits"),
    "rough": ("get into a lot of chippy games", "keep things calm after the whistle"),
    "pp": ("have a dangerous power play", "have struggled on the power play"),
    "pk": ("kill penalties well", "have a leaky penalty kill"),
    "powerKill": ("create chances on the penalty kill", "rarely threaten on the penalty kill"),
    "discipline": ("draw more power plays than they give up", "give up more power plays than they draw"),
}
LABELS = {"volume": "shot volume", "quality": "shot quality", "rebounds": "second chances", "turnover": "offense off turnovers",
          "dShots": "shots from defensemen", "inClose": "shots from in close", "shotsThrough": "getting shots through",
          "suppression": "shot suppression", "qualityAllowed": "keeping dangerous shots away", "breakdowns": "avoiding breakdowns",
          "longShifts": "avoiding long shifts", "blocking": "blocking shots", "pointAllowed": "point shots allowed",
          "icing": "avoiding icings", "goalie": "goaltending", "pace": "pace", "forecheck": "forecheck pressure",
          "physical": "physicality", "rough": "rough stuff", "pp": "the power play", "pk": "the penalty kill",
          "powerKill": "shorthanded threat", "discipline": "discipline"}
NEUTRAL = {"pointAllowed", "dShots", "inClose", "pace", "volume", "rough", "physical", "forecheck", "turnover"}  # no "worst" wording
BOUNCY = {"rebounds", "turnover", "breakdowns"}  # habits that take a hundred games or more to settle: mostly bounces
STYLE_PCT = 80  # a habit is told at the 80th percentile or the 20th and beyond
STYLE_MAX = 3
RANK_TOLD = 3  # a rank is told within this many places of either end
SCORING_EDGE = 4  # goals a game for or against told within this many places of either end
MIN_GAMES = 5  # before this, standings and scoring ranks say little
HOT_POINTS = 7  # points in his team's last five games to be "hot"


def _picks(t: str, fp: dict) -> list[tuple]:
    """Their most distinctive habits, furthest from average first: (distance, trait, blend figures)."""
    dims = (fp.get(t) or {}).get("dims") or {}
    picked = []
    for k, d in dims.items():
        b = (d or {}).get("blend") or {}
        if k in HABITS and k not in BOUNCY and b.get("pct") is not None and b.get("rank") and (b["pct"] >= STYLE_PCT or b["pct"] <= 100 - STYLE_PCT):
            picked.append((abs(b["pct"] - 50), k, b))
    return sorted(picked, key=lambda x: (-x[0], x[1]))[:STYLE_MAX]


def style(t: str, picked: list[tuple], W) -> str | None:
    """Their most distinctive habits in one sentence, with the ranks at the very ends of the league."""
    if not picked:
        return None
    good = lambda k, b: k not in NEUTRAL and b["pct"] >= 50
    bad = lambda k, b: k not in NEUTRAL and b["pct"] < 50
    ups = [HABITS[k][0 if b["pct"] >= 50 else 1] for _, k, b in picked if not bad(k, b)]
    downs = [HABITS[k][1] for _, k, b in picked if bad(k, b)]
    mixed = bool(ups and downs) and any(good(k, b) for _, k, b in picked)
    out = f"{cap(W.the(t))} {and_list(ups)}, but {and_list(downs)}" if mixed else f"{cap(W.the(t))} {and_list(ups + downs)}"
    # ranks only at the top end of a scale: "32nd in rough stuff" reads as a knock on a calm team
    told = [(b["rank"], k) for _, k, b in picked if b["rank"] <= RANK_TOLD and b["pct"] >= 50]
    if mixed and told:  # one contrast a sentence: the ranks get their own
        r, k = told[0]
        return out + (f". No team is better at {LABELS[k]}" if r == 1 and good(k, dict(pct=99)) else f". They’re {ordinal_word(r)} in {LABELS[k]}")
    if len(told) > 1 and len(told) == len(picked) and all(r == told[0][0] for r, _ in told):
        return out + f", and they’re {ordinal_word(told[0][0])} in the league in {'both' if len(told) == 2 else 'all three'}"
    if told:
        return out + ", and they’re " + and_list([f"{ordinal_word(r)} in {LABELS[k]}" for r, k in told])
    return out


@lru_cache(maxsize=64)
def _scoring(season: int, day: str) -> dict[str, dict]:
    """Each club's goals for and against a game this season, in games before `day` (shootout deciders left out)."""
    from pipeline.export.stories import book
    G, _, _ = book()
    g = G.filter((pl.col("season") == season) & (pl.col("date") < day))
    so = pl.col("last_period") == "SO"  # the shootout winner gets a goal in the score; it was no goal
    g = g.with_columns(hs=pl.when(so & (pl.col("home_score") > pl.col("away_score"))).then(pl.col("home_score") - 1).otherwise(pl.col("home_score")),
                       as_=pl.when(so & (pl.col("away_score") > pl.col("home_score"))).then(pl.col("away_score") - 1).otherwise(pl.col("away_score")))
    rows = pl.concat([g.select(team=pl.col("home"), gf=pl.col("hs"), ga=pl.col("as_")), g.select(team=pl.col("away"), gf=pl.col("as_"), ga=pl.col("hs"))])
    agg = rows.group_by("team").agg(pl.len().alias("gp"), pl.col("gf").sum(), pl.col("ga").sum())
    out = {r["team"]: {"gp": r["gp"], "gfpg": r["gf"] / r["gp"], "gapg": r["ga"] / r["gp"]} for r in agg.iter_rows(named=True)}
    for k, best_high in (("gfpg", True), ("gapg", False)):
        vals = sorted((v[k] for v in out.values()), reverse=best_high)
        for v in out.values():
            v[f"{k}_rank"] = vals.index(v[k]) + 1
    return out


def scoring(t: str, season: int, day: str, W) -> str | None:
    """Goals for or against a game, when near either end of the league."""
    s = _scoring(season, day).get(t)
    if not s or s["gp"] < MIN_GAMES:
        return None
    n = len(_scoring(season, day))
    edge = lambda k: s[f"{k}_rank"] <= SCORING_EDGE or s[f"{k}_rank"] > n - SCORING_EDGE

    def rk(k):  # "the most", "third fewest": goals allowed rank 1 is the fewest
        r = s[f"{k}_rank"] if k == "gfpg" else n + 1 - s[f"{k}_rank"]
        word, q = ("most", r) if r <= n / 2 else ("fewest", n + 1 - r)
        return f"the {word}" if q == 1 else f"the {ordinal_word(q)} {word}"
    if edge("gfpg") and edge("gapg"):
        return f"{W.place(t)} has scored {s['gfpg']:.2f} goals a game, {rk('gfpg')} in the league, and allowed {s['gapg']:.2f}, {rk('gapg')}."
    if edge("gfpg"):
        return f"{W.place(t)} has scored {s['gfpg']:.2f} goals a game, {rk('gfpg')} in the league."
    if edge("gapg"):
        return f"{W.place(t)} has allowed {s['gapg']:.2f} goals a game, {rk('gapg')} in the league."
    return None


def how_they_play(away: str, home: str, fp: dict, season: int, day: str, W) -> str | None:
    sents = []
    picks = {t: _picks(t, fp) for t in (away, home)}
    side = lambda b: b["pct"] >= 50
    shared = [(k, b) for _, k, b in picks[away] if any(k == k2 and side(b) == side(b2) for _, k2, b2 in picks[home])]
    if shared:  # "Both teams get their defensemen shooting." once, not in each team's list
        sents.append(f"Both teams {and_list([HABITS[k][0 if side(b) else 1] for k, b in shared])}.")
        gone = {k for k, _ in shared}
        picks = {t: [x for x in v if x[1] not in gone] for t, v in picks.items()}
    for t in (away, home):
        s = style(t, picks[t], W)
        if s:
            sents.append(s + ".")
        sc = scoring(t, season, day, W)
        if sc:
            sents.append(sc)
    return " ".join(sents) or None


def _team_lines(season: int, day: str, t: str) -> pl.DataFrame:
    from pipeline.export.stories import book
    _, P, _ = book()
    return P.filter((pl.col("season") == season) & (pl.col("team") == t) & (pl.col("date") < day))


def scorers(t: str, season: int, day: str, W, N: dict, gone: set, second: bool = False) -> tuple[str | None, set]:
    """Who carries the scoring: the points leader (with his goals), the goal leader if someone else, and a player
    hot over the team's last five games. Players the page lists as out are left out."""
    p = _team_lines(season, day, t)
    if p.is_empty():
        return None, set()
    p = p.filter(~pl.col("player_id").is_in(list(gone)))
    tot = (p.group_by("player_id").agg(pl.col("g").sum().cast(pl.Int64), pl.col("pts").sum().cast(pl.Int64), pl.len().alias("gp"))
           .filter(pl.col("pts") > 0).sort(["pts", "g", "player_id"], descending=[True, True, False]))
    if tot.is_empty():
        return None, set()
    lead = tot.row(0, named=True)
    if lead["player_id"] not in N:
        return None, set()
    used = {lead["player_id"]}
    g_part = f"{num(lead['g'])} {'goal' if lead['g'] == 1 else 'goals'} and " if lead["g"] else ""
    pts = f"{g_part}{num(lead['pts'])} {'point' if lead['pts'] == 1 else 'points'}"
    # the second team's lines are worded apart from the first's ("leads the x with" twice read as a template)
    if second and lead["g"] and lead["pts"] > 1:
        pts = f"{num(lead['pts'])} points, {num(lead['g'])} of them goals"
    first = f"For {W.the(t)}, it’s {N[lead['player_id']]}, with {pts}" if second else f"{N[lead['player_id']]} leads {W.the(t)} with {pts}"
    rest = []
    gl = tot.sort(["g", "pts", "player_id"], descending=[True, True, False]).row(0, named=True)
    if gl["player_id"] != lead["player_id"] and gl["g"] >= max(3, lead["g"]) and gl["player_id"] in N:
        if second:
            rest.append(f"{N[gl['player_id']]} {'leads them in goals with' if gl['g'] > lead['g'] else 'also has'} {num(gl['g'])}")
        else:
            rest.append(f"{N[gl['player_id']]} has {'a team-high' if gl['g'] > lead['g'] else 'just as many'} {num(gl['g'])} goals")
        used.add(gl["player_id"])
    games = p.select("game_id", "date").unique().sort("date", "game_id")["game_id"].to_list()[-5:]
    if len(games) == 5:
        hot = (p.filter(pl.col("game_id").is_in(games)).group_by("player_id").agg(pl.col("pts").sum().cast(pl.Int64))
               .filter(pl.col("pts") >= HOT_POINTS).sort(["pts", "player_id"], descending=[True, False]))
        for r in hot.iter_rows(named=True):
            if r["player_id"] in N:
                who = "he has" if r["player_id"] == lead["player_id"] else f"{N[r['player_id']]} has"
                rest.append(f"{who} {num(r['pts'])} points " + ("over the last five" if second else "in their last five games"))
                used.add(r["player_id"])
                break
    if not rest:
        return first + ".", used
    if rest[-1].startswith("he has"):
        return f"{first}, and {rest[-1]}." if len(rest) == 1 else f"{first}, and {rest[-1]}. {cap(rest[0])}.", used
    return f"{first}, and {and_list(rest)}.", used


def who_scores(away: str, home: str, season: int, day: str, W, N: dict, gone: set) -> tuple[str | None, set]:
    sents, used = [], set()
    for i, t in enumerate((away, home)):
        s, u = scorers(t, season, day, W, N, gone, second=bool(sents))
        if s:
            sents.append(s)
            used |= u
    return (" ".join(sents) or None), used


@lru_cache(maxsize=4)
def _goalie_games(season: int) -> pl.DataFrame:
    from pipeline.metrics import goalies
    return goalies.per_game(season).select("game_id", "goalie_id", "date", "sa", "ga", "started")


def in_net(away: str, home: str, projected: dict, season: int, day: str, W, N: dict) -> str | None:
    """The projected starters with their season so far: record, save percentage, starts."""
    from pipeline.export import goalie_streaks
    if not projected:
        return None
    gg = _goalie_games(season).filter(pl.col("date") < day)
    dec = goalie_streaks.decisions(season)
    parts = []
    for t in (away, home):
        x = projected.get(t)
        if not x or x["id"] not in N:
            continue
        pid = x["id"]
        mine = gg.filter(pl.col("goalie_id") == pid)
        starts = int(mine["started"].sum()) if mine.height else 0
        verb = "is expected to start" if x.get("from") == "nhl" else "should get the start"
        who = f"{N[pid]} {verb} for {W.place(t)}"
        if not starts:
            parts.append(f"{who}, his first start of the season")
            continue
        ds = [d.get(pid) for gid, d in dec.items() if pid in d and gid in set(mine["game_id"].to_list())]
        rec = f"{ds.count('W')}-{ds.count('L')}-{ds.count('O')}"
        sa, ga = int(mine["sa"].sum()), int(mine["ga"].sum())
        sv = f"{(sa - ga) / sa:.3f}".lstrip("0") if sa else None
        parts.append(f"{who} ({rec}{', ' + sv if sv else ''} in {num(starts)} {'start' if starts == 1 else 'starts'})")
    if not parts:
        return None
    if len(parts) == 2:
        verb = next((v for v in ("is expected to start", "should get the start") if v in parts[0] and v in parts[1]), None)
        second = parts[1].replace(f" {verb}", "", 1) if verb else parts[1]
        return cap(f"{parts[0]}, and {second}.")
    return cap(parts[0]) + "."


def standing(away: str, home: str, season: int, day: str, W, said: str) -> str | None:
    """Where they stand going in: division places from the last saved standings before the game (league first or
    last when true), with the record unless the paragraph already gave it."""
    from pipeline.export.stories import _table_marks
    from pipeline.ingest import standings_daily as sd
    rows = sd.before(season, day)
    if not rows:
        return None
    m = _table_marks(rows)
    if any(t not in m or m[t]["gp"] < MIN_GAMES for t in (away, home)):
        return None
    rec = {r["teamAbbrev"]: f"{r['wins']}-{r['losses']}-{r['otLosses']}" for r in rows if "wins" in r}

    def where(t):
        x = m[t]
        if x["pts"] == 1 and x["pts_n"] == 1:
            return "first in the NHL"
        if x["pts_low"] == 1 and x["pct_low"] == 1:
            return "last in the NHL"
        return f"last in the {x['division']}" if x["div"] == x["div_n"] else f"{ordinal_word(x['div'])} in the {x['division']}"

    def who(t):
        r = rec.get(t)
        return W.the(t) + (f" ({r})" if r and r not in said else "")
    a, h = where(away), where(home)
    div = m[away]["division"]
    if m[home]["division"] == div and " NHL" not in a + h:
        return f"In the {div}, {who(away)} are {a.split(' in the ')[0]} and {who(home)} {h.split(' in the ')[0]}."
    return f"Going in, {who(away)} are {a} and {who(home)} {h}."


def paragraphs(gid: int, away: str, home: str, day: str, W, N: dict, fp: dict | None, projected: dict | None,
               gone: set, said: str, season: int = CURRENT_SEASON) -> dict:
    """The fuller preview's extra parts: `standing` (a sentence for the opening paragraph), then `play`, `scoring`
    and `net` paragraphs, and `used` (players the scoring paragraph named)."""
    out = {"standing": None, "play": None, "scoring": None, "net": None, "used": set()}
    for k, f in (("standing", lambda: standing(away, home, season, day, W, said)),
                 ("play", lambda: how_they_play(away, home, fp or {}, season, day, W)),
                 ("net", lambda: in_net(away, home, projected or {}, season, day, W, N))):
        try:
            out[k] = f()
        except Exception as e:  # each part is optional; the preview stands without it
            print(f"preview {gid}: {k} failed: {e!r}")
    try:
        out["scoring"], out["used"] = who_scores(away, home, season, day, W, N, gone)
    except Exception as e:
        print(f"preview {gid}: scoring failed: {e!r}")
    return out
