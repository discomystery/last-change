"""How many games each club's regulars have missed, ranked across the league, and forwards filling in on faceoffs.

Sora, 2026-10-19 (thread on a league-wide injury check): Carolina were short enough at center that Martinook, a winger,
took the draws, with their best winger on IR and a defenseman unsigned. Previews named who was out, but nothing said
how unusual that was, so the Out item and the team intro add a league rank, and previews tell a forward who rarely takes
faceoffs taking a center's draws.

A REGULAR is a top-nine forward or top-four defenseman by 5-on-5 plus power-play ice time (how lines are numbered) for
his club last season (30+ games), or a regular anywhere last season, or one for his current club this season (3+ games,
by ice time a game so a player hurt early still counts), or a goalie who started 40%+ of his club's games last season or
this season (Sora: Shesterkin being out is a massive blow). A MISSED GAME is a club game he didn't dress for while the
league listed him as injured, suspended, out for personal reasons or off the roster, or while unsigned. Healthy scratches,
players sent to the minors with nothing reported, and players with no club (retired, unsigned free agents) don't count.
Never says why a player is out.
"""
from __future__ import annotations

from functools import lru_cache

import polars as pl

from pipeline.config import TABLES

OUT = ("injured", "suspended", "personal", "off_roster")
F_TOP, D_TOP = 9, 4
LAST_GP, NOW_GP = 30, 3
STARTER = 0.4  # share of the club's starts that makes a goalie its starter
# A rank is told only when it stands apart: one of the top three, enough games played, and well above the usual club.
RANK_TOP, MIN_GAMES, MIN_MISSED, OVER_MEDIAN = 3, 6, 12, 2.0
NOW_MIN = 4  # regulars out at once that make "no team has more out right now" worth saying
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
    """Per club, from games before `day`: games, regular games missed, rank (1 = most; ties share a rank), the clubs
    tied with it, regulars out on `day` and that count's rank. Empty when the tables aren't there."""
    av = _availability(season)
    if av.is_empty() or not (TABLES / str(season) / "player_game.parquet").exists():
        return {}
    g = pl.read_parquet(TABLES / str(season) / "games.parquet", columns=["date", "home", "away", "game_type", "state"])
    g = g.filter((pl.col("game_type") == 2) & (pl.col("date") < day) & pl.col("state").is_in(["OFF", "FINAL"]))
    games = pl.concat([g.select("date", team="home"), g.select("date", team="away")])
    if games.is_empty():
        return {}
    reg = regulars(season, day)
    played = av.join(games, on=["date", "team"])
    missed = _is_regular(played.filter((pl.col("lineup").is_null() | (pl.col("lineup") != "dressed")) & _out(played)), reg)
    per = games.group_by("team").agg(games=pl.len()).join(missed.group_by("team").agg(missed=pl.len()), on="team", how="left")
    per = per.with_columns(pl.col("missed").fill_null(0))
    today = av.filter(pl.col("date") == av.filter(pl.col("date") <= day)["date"].max())
    now = _is_regular(today.filter(_out(today)), reg).sort("weight", descending=True)
    names = {t: [(r["player_id"], f"{r['first']} {r['last']}") for r in d.iter_rows(named=True)]
             for (t,), d in now.group_by("team", maintain_order=True)}
    per = per.with_columns(now=pl.col("team").map_elements(lambda t: len(names.get(t, [])), return_dtype=pl.Int64))
    per = per.with_columns(rank=pl.col("missed").rank("min", descending=True).cast(pl.Int64),
                           now_rank=pl.col("now").rank("min", descending=True).cast(pl.Int64))
    median = float(per["missed"].median())
    out = {}
    for r in per.iter_rows(named=True):
        out[r["team"]] = {**r, "median": median, "names": names.get(r["team"], []),
                          "tied": sorted(t for t, m in zip(per["team"], per["missed"]) if m == r["missed"] and t != r["team"]),
                          "ahead": sorted(t for t, m in zip(per["team"], per["missed"]) if m > r["missed"]),
                          "now_tied": int((per["now"] == r["now"]).sum()) - 1}
    return out


def stands_out(r: dict | None) -> str | None:
    """"season" when the club's missed games are worth a league rank, "now" when only today's count is, else None."""
    if not r:
        return None
    if (r["rank"] <= RANK_TOP and r["games"] >= MIN_GAMES and r["missed"] >= MIN_MISSED
            and r["missed"] >= OVER_MEDIAN * max(r["median"], 1)):
        return "season"
    if r["now"] >= NOW_MIN and r["now_rank"] == 1 and not r["now_tied"]:
        return "now"
    return None


def rank_clause(r: dict, the) -> str:
    """"the most in the league", "tied with the Oilers for the most in the league", "second only to the Senators",
    "the third-most in the league". `the` names a club ("the Oilers")."""
    from pipeline.export.grammar import and_list
    tied, ahead = [the(t) for t in r["tied"]], [the(t) for t in r["ahead"]]
    if r["rank"] == 1:
        return "the most in the league" if not tied else f"tied with {and_list(tied)} for the most in the league"
    if r["rank"] == 2 and len(ahead) == 1 and not tied:
        return f"second only to {ahead[0]}"
    nth = {2: "second", 3: "third"}[r["rank"]]
    return f"the {nth}-most in the league" if not tied else f"tied for the {nth}-most in the league"


def sentence(r: dict, the, lead: str = "Their regulars have missed") -> str | None:
    """The rank in one sentence, or None. `lead` starts it; previews and intros word the opening their own way."""
    kind = stands_out(r)
    if kind == "season":
        return f"{lead} {r['missed']} games this season, {rank_clause(r, the)}."
    if kind == "now":
        return "No team has more regulars out right now."
    return None


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
    """Team intro: "They’ve also been missing a lot of regulars: 25 games so far, the most in the league, with Seth
    Jarvis, Taylor Hall and Jordan Staal still out." Names only, at most three."""
    from pipeline.export.grammar import and_list
    kind = stands_out(r)
    names = and_list([n for _, n in r["names"][:3]]) if kind else ""
    if kind == "season":
        tail = f", with {names} still out." if names else "."
        return f"They’ve also been missing a lot of regulars: {r['missed']} games so far, {rank_clause(r, the)}{tail}"
    if kind == "now":
        return f"No team has more regulars out right now than {team_the}, with {names} among them."
    return None
