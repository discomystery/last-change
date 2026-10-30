"""How many games each club's regulars have missed, ranked across the league, and forwards filling in on faceoffs.

Sora, 2026-10-19 (thread on a league-wide injury check): Carolina were short enough at center that Martinook, a winger,
took the draws, with their best winger on IR and a defenseman unsigned. Previews named who was out, but nothing said
how unusual that was, so the Out item and the team intro add a league rank, and previews tell a forward who rarely takes
faceoffs taking a center's draws.

A REGULAR is one of the top twelve forwards or six defensemen by 5-on-5 plus power-play ice time (how lines are numbered) for
his club last season (30+ games), or a regular anywhere last season, or one for his current club this season (3+ games,
by ice time a game so a player hurt early still counts), or a goalie who started 20%+ of his club's games last season or
this season (Sora: Shesterkin being out is a massive blow). Sora widened it from the top nine and four the same day, since
counting only top players favors thin teams; the value weighting keeps depth players from counting much. A MISSED GAME is a club game he didn't dress for while the
league listed him as injured, suspended, out for personal reasons or off the roster, or while unsigned. Healthy scratches,
players sent to the minors with nothing reported, and players with no club (retired, unsigned free agents) don't count.
Never says why a player is out.

The RANK is by what the absences cost, not the plain count (Sora, same day: weigh skaters by chances and goalies by GSAx,
with a goalie limited by how often he starts): a skater's game is worth his 5-on-5 impact rating (creating plus preventing,
the two seasons before, as the win model's lineups) above a replacement-level regular, times his usual 5-on-5 ice time; a
goalie's is his goals saved above expected a start (two seasons, pulled toward zero by 3,000 shots) above replacement, times
his share of his club's starts last season. Both are expected goals a game, so a starting goalie and a first-line forward
sit on one scale (the best of each are worth about 0.2-0.3 a game). The sentences still quote the games missed.
"""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from pipeline.config import TABLES

OUT = ("injured", "suspended", "personal", "off_roster")
F_TOP, D_TOP = 12, 6  # a full lineup (Sora, 2026-10-19: counting only the top nine and four favors thin teams)
LAST_GP, NOW_GP = 30, 3
STARTER = 0.2  # share of the club's starts that makes a goalie a regular (the backup too)
# A rank is told only when it stands apart: one of the top three, enough games played and missed, and twice what the
# usual club has lost.
RANK_TOP, MIN_GAMES, MIN_MISSED, OVER_MEDIAN = 3, 6, 6, 2.0
REPLACEMENT = 0.2  # replacement level: this quantile of rated regulars (skaters 20+ games, goalies 10+ starts)
VALUE_LAMBDA = 15000.0  # ratings as the win model's lineups
# Right now (Sora, 2026-10-20: the season total suits the team blurb, previews and post-game pages want who is out today):
# what today's absences are worth a game, told for the top three clubs when it is twice the median club's and at least
# NOW_FLOOR expected goals a game (about one top-line forward).
NOW_FLOOR = 0.2
# Every line says the count is weighed, without the method (Sora, 2026-10-20: imply it is more than a head count).
WEIGHED = "Taking into account how much value each missing player usually adds"
# Filling in on faceoffs: a forward who had been taking fewer than RARE_DRAWS a game (over GP_BEFORE+ games) took
# FILL_DRAWS+ in the club's last game while a player who takes CENTER_DRAWS+ a game was out.
RARE_DRAWS, GP_BEFORE, FILL_DRAWS, CENTER_DRAWS = 3.0, 20, 8, 8.0


def _abbr(season: int) -> pl.DataFrame:
    g = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["home", "away", "home_id", "away_id"])
    return pl.concat([g.select(team_id="home_id", team="home"), g.select(team_id="away_id", team="away")]).unique("team_id")


@lru_cache(maxsize=4)
def _skaters(season: int) -> pl.DataFrame:
    """One row per player-game: club, F or D, 5-on-5 plus power-play seconds, faceoffs, date."""
    pg = pl.read_parquet(TABLES / str(season) / "player_game.parquet", columns=["game_id", "player_id", "team_id", "pos", "sec5", "sec_pp", "fow", "fol", "g", "a1", "a2"])
    dates = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["game_id", "date"])
    return (pg.filter(pl.col("pos") != "G").join(_abbr(season), on="team_id").join(dates, on="game_id")
            .with_columns(grp=pl.when(pl.col("pos") == "D").then(pl.lit("D")).otherwise(pl.lit("F")),
                          core=pl.col("sec5") + pl.col("sec_pp"), fo=pl.col("fow") + pl.col("fol"),
                          pts=pl.col("g") + pl.col("a1") + pl.col("a2")))


def _ranked(df: pl.DataFrame, min_gp: int, by: str) -> pl.DataFrame:
    s = df.group_by("team", "player_id", "grp").agg(gp=pl.len(), core=pl.col("core").sum(), pts=pl.col("pts").sum()).filter(pl.col("gp") >= min_gp)
    s = s.with_columns(per=pl.col("core") / pl.col("gp"), ppg=1000 * pl.col("pts") / pl.col("gp"))
    s = s.with_columns(rk=pl.col(by).rank("ordinal", descending=True).over("team", "grp"))
    return s.filter(pl.when(pl.col("grp") == "F").then(pl.col("rk") <= F_TOP).otherwise(pl.col("rk") <= D_TOP))


@lru_cache(maxsize=4)
def _starts(season: int) -> pl.DataFrame:
    from pipeline.metrics import goalies
    if not (TABLES / str(season) / "events.parquet").exists():
        return pl.DataFrame(schema={"date": pl.Utf8, "team": pl.Utf8, "player_id": pl.Int64})
    s = goalies.per_game(season).filter(pl.col("started")).join(_abbr(season), on="team_id")
    return s.select(pl.col("date").cast(pl.Utf8), "team", pl.col("goalie_id").alias("player_id"))


def _starters(starts: pl.DataFrame, min_mine: int) -> pl.DataFrame:
    n = starts.group_by("team", "player_id").len()
    tot = starts.group_by("team").agg(total=pl.len())
    return n.join(tot, on="team").filter((pl.col("len") >= min_mine) & (pl.col("len") / pl.col("total") >= STARTER))


@lru_cache(maxsize=64)
def regulars(season: int, day: str) -> pl.DataFrame:
    """(team, player_id, weight) for every regular as of `day`. `weight` (points a game x 1000, a starting goalie first)
    only orders names, so fans see the scorers before the depth; team "*" means a regular for any club last season."""
    rows = []
    if (TABLES / str(season - 1) / "player_game.parquet").exists():
        last = _ranked(_skaters(season - 1), LAST_GP, "core")
        rows.append(last.select("team", "player_id", weight="ppg"))
        rows.append(last.select(pl.lit("*").alias("team"), "player_id", weight="ppg"))
        g = _starters(_starts(season - 1), 1)
        rows.append(g.select("team", "player_id", weight=pl.lit(3000.0)))
    now = _skaters(season).filter(pl.col("date") < day)
    if now.height:
        rows.append(_ranked(now, NOW_GP, "per").select("team", "player_id", weight="ppg"))
    g = _starters(_starts(season).filter(pl.col("date") < day), 3)
    rows.append(g.select("team", "player_id", weight=pl.lit(3000.0)))
    # a player on more than one list keeps his biggest weight
    rows = [r.with_columns(pl.col("weight").cast(pl.Float64)) for r in rows]
    return pl.concat(rows).group_by("team", "player_id").agg(pl.col("weight").max())


@lru_cache(maxsize=16)
def values(season: int, day: str) -> dict[int, float]:
    """Expected goals a game each player is worth above replacement (see the module note); empty without ratings."""
    from pipeline.metrics import goalies, rapm
    have = [s for s in (season - 2, season - 1) if (TABLES / str(s) / "player_game.parquet").exists()]
    if not have:
        return {}
    r = rapm.run(have, VALUE_LAMBDA).select("player_id", per60=pl.col("off") + pl.col("def"))
    pg = pl.concat([_skaters(s).filter(pl.col("date") < day).select("player_id", "sec5")
                    for s in (season - 1, season) if (TABLES / str(s) / "player_game.parquet").exists()])
    toi = pg.group_by("player_id").agg(m5=pl.col("sec5").mean() / 60, gp=pl.len())
    v = r.join(toi, on="player_id").filter(pl.col("gp") >= 20)
    if v.is_empty():
        return {}
    repl = v["per60"].quantile(REPLACEMENT)
    v = v.with_columns(val=(pl.col("per60") - repl).clip(0) * pl.col("m5") / 60)
    out = dict(zip(v["player_id"].to_list(), v["val"].to_list()))
    g = [goalies.per_game(s).filter(pl.col("date").cast(pl.Utf8) < day).with_columns(s=pl.lit(s))
         for s in (season - 1, season) if (TABLES / str(s) / "events.parquet").exists()]
    if g:
        g = pl.concat([x.select("goalie_id", "team_id", "started", "sa", "ga", "xga", "s") for x in g])
        tot = (g.group_by("goalie_id").agg(gsax=(pl.col("xga") - pl.col("ga")).sum(), sa=pl.col("sa").sum(), starts=pl.col("started").sum())
               .filter(pl.col("starts") >= 10)
               .with_columns(per=pl.col("gsax") / pl.col("starts") * pl.col("sa") / (pl.col("sa") + goalies.SHRINK_SHOTS)))
        if tot.height:
            grepl = tot["per"].quantile(REPLACEMENT)
            last = g.filter((pl.col("s") == season - 1) & pl.col("started"))
            share = (last.group_by("goalie_id", "team_id").len().join(last.group_by("team_id").agg(n=pl.len()), on="team_id")
                     .with_columns(share=pl.col("len") / pl.col("n")).group_by("goalie_id").agg(pl.col("share").max()))
            tot = (tot.join(share, on="goalie_id", how="left").with_columns(pl.col("share").fill_null(0.5))
                   .with_columns(val=(pl.col("per") - grepl).clip(0) * pl.col("share")))
            out.update(zip(tot["goalie_id"].to_list(), tot["val"].to_list()))
    return out


def _is_regular(av: pl.DataFrame, reg: pl.DataFrame) -> pl.DataFrame:
    club = av.join(reg.filter(pl.col("team") != "*"), on=["team", "player_id"], how="left")
    anywhere = reg.filter(pl.col("team") == "*").select("player_id", w_any="weight")
    out = club.join(anywhere, on="player_id", how="left").with_columns(weight=pl.coalesce("weight", "w_any")).drop("w_any")
    return out.filter(pl.col("weight").is_not_null())


def _out(av: pl.DataFrame) -> pl.Expr:
    return pl.col("status").is_in(OUT) | (pl.col("report_status") == "unsigned")


@lru_cache(maxsize=4)
def _availability(season: int) -> pl.DataFrame:
    f = TABLES / str(season) / "availability.parquet"
    if not f.exists():
        return pl.DataFrame()
    return pl.read_parquet(f, columns=["date", "team", "player_id", "first", "last", "pos", "lineup", "status", "report_status"])


@lru_cache(maxsize=64)
def table(season: int, day: str) -> dict[str, dict]:
    """Per club, from games before `day`: games, regular games missed, `lost` (expected goals those games were worth),
    rank by `lost` (1 = hit hardest), the clubs ahead of it, regulars out on `day` (names by worth) and that count's rank. Empty when the tables aren't there."""
    av = _availability(season)
    if av.is_empty() or not (TABLES / str(season) / "player_game.parquet").exists():
        return {}
    g = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["date", "home", "away", "game_type", "state"])
    g = g.filter((pl.col("game_type") == 2) & (pl.col("date") < day) & pl.col("state").is_in(["OFF", "FINAL"]))
    games = pl.concat([g.select("date", team="home"), g.select("date", team="away")])
    if games.is_empty():
        return {}
    reg = regulars(season, day)
    vals = values(season, day)
    typical = sorted(vals.values())[len(vals) // 2] if vals else 1.0  # a regular we can't rate counts as the middle player
    worth = lambda df: df.with_columns(val=pl.col("player_id").replace_strict(vals, default=typical, return_dtype=pl.Float64))
    played = av.join(games, on=["date", "team"])
    missed = _is_regular(played.filter((pl.col("lineup").is_null() | (pl.col("lineup") != "dressed")) & _out(played)), reg)
    missed = worth(missed)
    per = games.group_by("team").agg(games=pl.len()).join(missed.group_by("team").agg(missed=pl.len(), lost=pl.col("val").sum()), on="team", how="left")
    per = per.with_columns(pl.col("missed").fill_null(0), pl.col("lost").fill_null(0.0))
    today = av.filter(pl.col("date") == av.filter(pl.col("date") <= day)["date"].max())
    now = worth(_is_regular(today.filter(_out(today)), reg)).sort("val", "weight", descending=True)
    names = {t: [(r["player_id"], f"{r['first']} {r['last']}") for r in d.iter_rows(named=True)]
             for (t,), d in now.group_by("team", maintain_order=True)}
    now_lost = dict(now.group_by("team").agg(pl.col("val").sum()).iter_rows()) if now.height else {}
    per = per.with_columns(now=pl.col("team").map_elements(lambda t: len(names.get(t, [])), return_dtype=pl.Int64),
                           now_lost=pl.col("team").replace_strict(now_lost, default=0.0, return_dtype=pl.Float64))
    per = per.with_columns(rank=pl.col("lost").rank("min", descending=True).cast(pl.Int64),
                           now_rank=pl.col("now_lost").rank("min", descending=True).cast(pl.Int64))
    median = float(per["missed"].median())
    lost_median = float(per["lost"].median())
    now_median = float(per["now_lost"].median())
    by_now = per.sort("now_lost", descending=True)
    out = {}
    for r in per.sort("lost", descending=True).iter_rows(named=True):
        out[r["team"]] = {**r, "median": median, "lost_median": lost_median, "now_median": now_median, "names": names.get(r["team"], []),
                          "now_ahead": [t for t, m in zip(by_now["team"], by_now["now_lost"]) if m > r["now_lost"]],
                          "ahead": [t for t, m in zip(per.sort("lost", descending=True)["team"], per.sort("lost", descending=True)["lost"]) if m > r["lost"]],
}
    return out


def stands_out(r: dict | None) -> bool:
    """True when the club's season of missed games is worth a league rank (team blurbs)."""
    return bool(r) and (r["rank"] <= RANK_TOP and r["games"] >= MIN_GAMES and r["missed"] >= MIN_MISSED
                        and r["lost"] >= OVER_MEDIAN * r["lost_median"])


def out_now(r: dict | None) -> bool:
    """True when what the club is missing right now is worth a league rank (previews and post-game pages)."""
    return bool(r) and (r["now_rank"] <= RANK_TOP and r["now_lost"] >= NOW_FLOOR
                        and r["now_lost"] >= OVER_MEDIAN * r["now_median"])


def than_whom(r: dict, the) -> str:
    """"than any other team" or "than anyone but the Islanders". `the` names a club ("the Oilers")."""
    from pipeline.export.grammar import and_list
    return "than any other team" if r["rank"] == 1 else f"than anyone but {and_list([the(t) for t in r['ahead']])}"


def sentence(r: dict | None, the) -> str | None:
    """Preview, after the Out item's names: "No team is missing more right now." or "Only the Flyers and the Wild are
    missing more right now." None unless it stands out."""
    from pipeline.export.grammar import and_list
    if not out_now(r):
        return None
    if r["now_rank"] == 1:
        return f"{WEIGHED}, no team is missing more right now."
    return f"{WEIGHED}, only {and_list([the(t) for t in r['now_ahead']])} are missing more right now."


def postgame_line(r: dict | None, team_the: str, won: bool, the) -> str | None:
    """Post-game: "The Hurricanes won without Seth Jarvis, Mark Jankowski and Taylor Hall, and no team was missing more at
    the time." Names only, at most three, the costliest first; past tense, since the page stays up."""
    from pipeline.export.grammar import and_list
    if not out_now(r) or not r["names"]:
        return None
    names = and_list([n for _, n in r["names"][:3]] + (["others"] if len(r["names"]) > 3 else []))
    tail = ("no team was missing more at the time" if r["now_rank"] == 1
            else f"only {and_list([the(t) for t in r['now_ahead']])} were missing more at the time")
    verb = "won without" if won else "were without"
    return f"{team_the[0].upper() + team_the[1:]} {verb} {names}. {WEIGHED}, {tail}."


def filling_in(season: int, day: str, team: str) -> dict | None:
    """In the club's last game before `day`, a forward who had been taking fewer than RARE_DRAWS faceoffs a game took
    FILL_DRAWS or more, while a player who takes CENTER_DRAWS+ a game for the club was out. Returns the fill-in, his
    draws, his rate before and the missing centers (ids, by draws a game), or None."""
    if _availability(season).is_empty() or not (TABLES / str(season) / "player_game.parquet").exists():
        return None
    now = _skaters(season)
    mine = now.filter((pl.col("team") == team) & (pl.col("date") < day))
    if mine.is_empty():
        return None
    gid = mine.sort("date", "game_id")["game_id"][-1]
    night = mine.filter((pl.col("game_id") == gid) & (pl.col("grp") == "F") & (pl.col("fo") >= FILL_DRAWS))
    if night.is_empty():
        return None
    gday = night["date"][0]
    av = _availability(season)
    out_ids = set(av.filter((pl.col("date") == gday) & (pl.col("team") == team) & _out(av)
                            & (pl.col("lineup").is_null() | (pl.col("lineup") != "dressed")))["player_id"])
    before = now.filter(pl.col("date") < gday)
    prev = _skaters(season - 1) if (TABLES / str(season - 1) / "player_game.parquet").exists() else before.clear()
    hist = pl.concat([before.select("player_id", "team", "fo"), prev.select("player_id", "team", "fo")])
    rate = hist.group_by("player_id").agg(gp=pl.len(), per=pl.col("fo").sum() / pl.len())
    centers = (pl.concat([before.filter(pl.col("team") == team), prev.filter(pl.col("team") == team)])
               .filter(pl.col("player_id").is_in(list(out_ids))).group_by("player_id").agg(per=pl.col("fo").sum() / pl.len(), gp=pl.len())
               .filter((pl.col("per") >= CENTER_DRAWS) & (pl.col("gp") >= NOW_GP)).sort("per", descending=True))
    if centers.is_empty():
        return None
    cand = night.join(rate, on="player_id").filter((pl.col("gp") >= GP_BEFORE) & (pl.col("per") < RARE_DRAWS)).sort("fo", descending=True)
    if cand.is_empty():
        return None
    f = cand.row(0, named=True)
    return {"pid": f["player_id"], "draws": int(f["fo"]), "before": float(f["per"]), "game_id": gid, "date": gday,
            "centers": centers["player_id"].to_list()}


def filling_text(x: dict, team_the: str, N: dict[int, str], fallback: dict[int, str] | None = None) -> str | None:
    """"With Jordan Staal and Mark Jankowski out, Jordan Martinook took 12 faceoffs in the Hurricanes’ last game. He had
    been taking fewer than one a game." """
    from pipeline.export.grammar import and_list, num
    name = lambda p: N.get(p) or (fallback or {}).get(p)
    who, centers = name(x["pid"]), [n for n in (name(p) for p in x["centers"][:2]) if n]
    if not who or not centers:
        return None
    rate = "fewer than one" if x["before"] < 1 else f"about {num(round(x['before']))}"
    poss = team_the + ("’" if team_the.endswith("s") else "’s")
    return (f"With {and_list(centers)} out, {who} took {num(x['draws'])} faceoffs in {poss} last game. "
            f"He had been taking {rate} a game.")


def intro_line(r: dict | None, team_the: str, the) -> str | None:
    """Team intro, the season so far: "They’ve also been hit harder by absences than any other team: their regulars
    have missed 25 games so far, with Seth Jarvis, Taylor Hall and Jordan Staal still out." Names only, at most three,
    the costliest first."""
    from pipeline.export.grammar import and_list
    if not stands_out(r):
        return None
    names = and_list([n for _, n in r["names"][:3]])
    tail = f", with {names} still out." if names else "."
    who = "no team has" if r["rank"] == 1 else f"only {and_list([the(t) for t in r['ahead']])} have"
    return f"{WEIGHED}, {who} been hit harder by absences: their regulars have missed {r['missed']} games so far{tail}"
