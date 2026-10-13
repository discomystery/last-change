"""Impact: one number per player per game, with its parts, for the post-game page.

A skater's night is the sum of four parts, in goal-sized units:
  scoresheet   his share of the goals he scored or set up (scorer 40%, first assist 35%, second assist 25%, at half
               weight), a quarter of the expected goals of his own shots, penalties drawn minus taken (a minor is
               worth 0.16 goals to the other team: measured 0.156-0.171 net expected goals per minor over 2023-25)
               and faceoffs won minus lost (a 5-on-5 draw is worth 0.006 expected goals over the next 20 seconds);
  5-on-5 play  chances for minus against with him on the ice, half against league average (score-and-venue
               adjusted) and half against his own team with him off the ice, plus goals for minus against at half
               weight (expected goals predict future goals far better than goals do, but the goals he was on the
               ice for are what fans saw; at half weight rather than a quarter a team's total follows the score far
               better, 0.76 to 0.87 over 2025-26, while staying mostly about chances);
  special teams everything with him on the ice on the power play or penalty kill, both ways: chances and goals for
               minus against, each judged against what a league-average unit creates and allows in the same time
               (so a shorthanded goal against costs the power-play unit that gave it up);
  usage        a small give-back for strong linemates and weak opponents (and credit for the reverse), from the
               5-on-5 impact ratings as they stood at the end of last season.
PLAY sets how much on-ice play counts against the scoresheet; the user chose "play-led" (2026-10-10), so it is the
largest part, as on Dom Luszczyszyn's Game Score cards. A goalie's night is his goals saved above expected.

Each night also gets a percentile against every night last season at his position (forwards, defensemen,
goalies), scored the same way. Everything comes from finished games; nothing here predicts anything.
"""
import json

import numpy as np
import polars as pl

from pipeline.build.stints import PENALTY_SHOT
from pipeline.config import CURRENT_SEASON, GAME_TYPES, REGULAR, SITE_DATA, TABLES, types_for
from pipeline.export import pending
from pipeline.metrics import players as P
from pipeline.metrics import rapm
from pipeline.metrics import units as U

OUT = SITE_DATA / "scores"
PLAY = 0.6  # weight of an on-ice expected goal; 0.2 = scoresheet-led, 0.4 = even, 0.6 = play-led (user's choice)
GOALS = 0.5  # weight of goal credit; the other half of a goal's worth is in the chances
SHOOTER = 0.25  # weight of the expected goals of his own shots
SPLIT = (0.4, 0.35, 0.25)  # scorer, first assist, second assist
PENALTY, FACEOFF = 0.16, 0.006
GOALS_ON_ICE = 0.5  # on-ice goals for and against, relative to on-ice expected goals (0.25 until 2026-10-11)
MIN_OFF = 600  # seconds of 5-on-5 team time with him off the ice before his team's off-ice rate is trusted
G_MIN_SHOTS = 5  # goalies facing fewer shots get no score
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]


SPECIAL = ("pp_xgf", "sec_pp", "sh_xga", "sh_xgf", "sec_pk")  # newer player_game columns; older saved seasons get rebuilt
ST_COLS = ("pp_xga", "pp_gf", "pp_ga", "pk_gf", "pk_ga")
ST_GOALS = ST_COLS[1:]  # special-teams goals with him on the ice, named in the Swing breakdown


def special_on_ice(season: int) -> pl.DataFrame:
    """Per player-game, the special-teams events player_game leaves out: chances against on the power play, and goals
    for and against with him on the ice on the power play and on the penalty kill (both goalies in, no penalty shots)."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type").is_in(types_for(season))).select("game_id")
    xg = pl.read_parquet(d / "shots_xg_own.parquet").select("game_id", "event_id", "xg")
    s = pl.read_parquet(d / "events.parquet").join(games, on="game_id").filter(
        pl.col("type").is_in(UNBLOCKED) & pl.col("is_home").is_not_null() & (pl.col("period_type") != "SO")
        & ~pl.col("situation_code").is_in(list(PENALTY_SHOT)) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null()
    ).join(xg, on=["game_id", "event_id"], how="left").with_columns(
        pl.col("xg").fill_null(0.0), goal=(pl.col("type") == "goal").cast(pl.Float64),
        n_own=pl.when(pl.col("is_home")).then(pl.col("home_on").list.len()).otherwise(pl.col("away_on").list.len()),
        n_opp=pl.when(pl.col("is_home")).then(pl.col("away_on").list.len()).otherwise(pl.col("home_on").list.len()),
        own_on=pl.when(pl.col("is_home")).then(pl.col("home_on")).otherwise(pl.col("away_on")),
        opp_on=pl.when(pl.col("is_home")).then(pl.col("away_on")).otherwise(pl.col("home_on")))
    pp, sh = s.filter(pl.col("n_own") > pl.col("n_opp")), s.filter(pl.col("n_own") < pl.col("n_opp"))
    zero = pl.lit(0.0)
    cols = lambda *xs: [x.alias(n) for x, n in zip(xs, ST_COLS)]
    parts = [
        pp.select("game_id", pl.col("own_on").alias("player_id"), *cols(zero, pl.col("goal"), zero, zero, zero)),  # his power play scores
        pp.select("game_id", pl.col("opp_on").alias("player_id"), *cols(zero, zero, zero, zero, pl.col("goal"))),  # scored on while he kills
        sh.select("game_id", pl.col("own_on").alias("player_id"), *cols(zero, zero, zero, pl.col("goal"), zero)),  # his kill scores
        sh.select("game_id", pl.col("opp_on").alias("player_id"), *cols(pl.col("xg"), zero, pl.col("goal"), zero, zero)),  # his power play gives one up
    ]
    return pl.concat(parts).explode("player_id").drop_nulls("player_id").group_by("game_id", "player_id").agg(pl.all().sum())


def _ratings(through: int) -> dict[int, tuple[float, float]]:
    """Impact ratings as they stood at the end of season `through` (rapm.chained, nothing of the season after)."""
    r = rapm.chained(through)
    return {p: (o, d) for p, o, d in zip(r["player_id"].to_list(), r["off"].to_list(), r["def"].to_list())}


def usage(season: int, ratings: dict[int, tuple[float, float]]) -> pl.DataFrame:
    """Expected goals his 5-on-5 linemates and opponents would add for his team (minus what they would give up),
    from their impact ratings, over his 5-on-5 time. Positive = easy company."""
    st = rapm.stretches(season)
    acc: dict[tuple[int, int], float] = {}
    get = lambda p: ratings.get(p, (0.0, 0.0))
    for g, hs, aw, d in zip(st["game_id"].to_list(), st["home_skaters"].to_list(), st["away_skaters"].to_list(), st["duration"].to_list()):
        for mine, opp in ((hs, aw), (aw, hs)):
            mo, md = sum(get(p)[0] for p in mine), sum(get(p)[1] for p in mine)
            oo, od = sum(get(p)[0] for p in opp), sum(get(p)[1] for p in opp)
            for p in mine:
                o, df = get(p)
                ctx = ((mo - o) - od) - (oo - (md - df))  # per 60: teammates' offense against their defense, and back
                acc[(g, p)] = acc.get((g, p), 0.0) + ctx * d / 3600
    return pl.DataFrame({"game_id": [k[0] for k in acc], "player_id": [k[1] for k in acc], "ctx": list(acc.values())},
                        schema={"game_id": pl.Int64, "player_id": pl.Int64, "ctx": pl.Float64})


def shot_credit(season: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Per player-game: goal credit and the expected goals of his own unblocked shots at a goalie (shootouts out);
    per goalie-game: expected goals and goals on the unblocked shots he faced."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type").is_in(types_for(season))).select("game_id")
    ev = pl.read_parquet(d / "events.parquet").join(games, on="game_id")
    xg = pl.read_parquet(d / "shots_xg_own.parquet").select("game_id", "event_id", "xg")
    sh = ev.filter(pl.col("type").is_in(UNBLOCKED) & pl.col("is_home").is_not_null() & (pl.col("period_type") != "SO")).join(
        xg, on=["game_id", "event_id"], how="left").with_columns(
        pl.col("xg").fill_null(0.0),
        goalie=pl.when(pl.col("is_home")).then(pl.col("away_goalie")).otherwise(pl.col("home_goalie")))
    at_goalie = sh.filter(pl.col("goalie").is_not_null())
    own = at_goalie.filter(pl.col("p1").is_not_null()).group_by("game_id", pl.col("p1").alias("player_id")).agg(pl.col("xg").sum().alias("ixg"))
    g, a1, a2 = SPLIT
    goals = sh.filter(pl.col("type") == "goal").select("game_id", "p1", "p2", "p3")
    # Credit per goal: the split, with a missing assist's share going to the players who are credited.
    parts = []
    for col, w_none, w_one, w_two in (("p1", 1.0, g / (g + a1), g), ("p2", 0.0, a1 / (g + a1), a1), ("p3", 0.0, 0.0, a2)):
        w = (pl.when(pl.col("p2").is_null()).then(w_none).when(pl.col("p3").is_null()).then(w_one).otherwise(w_two))
        parts.append(goals.filter(pl.col(col).is_not_null()).select("game_id", pl.col(col).alias("player_id"), w.alias("credit")))
    credit = pl.concat(parts).group_by("game_id", "player_id").agg(pl.col("credit").sum())
    skaters = own.join(credit, on=["game_id", "player_id"], how="full", coalesce=True).with_columns(pl.col("ixg", "credit").fill_null(0.0))
    goalies = at_goalie.group_by("game_id", pl.col("goalie").alias("player_id")).agg(
        pl.col("xg").sum().alias("xga"), (pl.col("type") == "goal").sum().alias("ga"), pl.len().alias("shots"))
    return skaters, goalies


def table(season: int, ratings: dict[int, tuple[float, float]], league: dict[str, float]) -> pl.DataFrame:
    """Every skater's and goalie's night in a season, with its parts."""
    pg = P.load(season, need=SPECIAL)
    credit, goalies = shot_credit(season)
    ctx = usage(season, ratings) if ratings else pl.DataFrame(schema={"game_id": pl.Int64, "player_id": pl.Int64, "ctx": pl.Float64})
    d = pg.join(credit, on=["game_id", "player_id"], how="left").join(ctx, on=["game_id", "player_id"], how="left").join(
        special_on_ice(season), on=["game_id", "player_id"], how="left").with_columns(pl.col("ixg", "credit", "ctx", "sh_xgf", *ST_COLS).fill_null(0.0))
    off_share = pl.col("sec5") / (pl.col("t_sec5") - pl.col("sec5")).clip(MIN_OFF)
    rel = (pl.col("on_xgf") - pl.col("on_xga")) - ((pl.col("t_xgf") - pl.col("on_xgf")) - (pl.col("t_xga") - pl.col("on_xga"))) * off_share
    sk = d.select(
        "game_id", "player_id", "team_id", "pos", *ST_GOALS,
        sheet=GOALS * pl.col("credit") + SHOOTER * pl.col("ixg") + PENALTY * (pl.col("pen_drawn") - pl.col("pen_taken")) + FACEOFF * (pl.col("fow") - pl.col("fol")),
        play=PLAY * (0.5 * (pl.col("on_xgf_adj") - pl.col("on_xga_adj")) + 0.5 * rel + GOALS_ON_ICE * (pl.col("on_gf") - pl.col("on_ga"))),
        special=PLAY * ((pl.col("pp_xgf") - pl.col("pp_xga")) - (league["pp"] - league["sh"]) * pl.col("sec_pp")
                        + (pl.col("sh_xgf") - pl.col("sh_xga")) - (league["sh"] - league["pp"]) * pl.col("sec_pk")
                        + GOALS_ON_ICE * ((pl.col("pp_gf") - pl.col("pp_ga")) - (league["pp_g"] - league["sh_g"]) * pl.col("sec_pp")
                                          + (pl.col("pk_gf") - pl.col("pk_ga")) - (league["sh_g"] - league["pp_g"]) * pl.col("sec_pk"))),
        usage=-PLAY * 0.5 * pl.col("ctx"),
        net=pl.lit(0.0))
    roster = pl.read_parquet(TABLES / str(season) / "players.parquet").select("game_id", "player_id", "team_id", "pos")
    gk = goalies.filter(pl.col("shots") >= G_MIN_SHOTS).join(roster, on=["game_id", "player_id"]).select(
        "game_id", "player_id", "team_id", "pos", *[pl.lit(0.0).alias(c) for c in (*ST_GOALS, "sheet", "play", "special", "usage")],
        net=pl.col("xga") - pl.col("ga").cast(pl.Float64))
    out = pl.concat([sk, gk], how="vertical_relaxed").with_columns(
        score=pl.sum_horizontal("sheet", "play", "special", "usage", "net"),
        grp=pl.when(pl.col("pos") == "G").then(pl.lit("G")).when(pl.col("pos") == "D").then(pl.lit("D")).otherwise(pl.lit("F")))
    return out


def league_rates(season: int) -> dict[str, float]:
    """Per second of power play, what the league's power plays create (pp, pp_g) and give up shorthanded (sh, sh_g),
    in chances and goals (every skater on the ice shares the same events and seconds, so the ratio of the sums is the
    team rate). A penalty kill's baseline is the same thing from the other side."""
    t = P.load(season, need=SPECIAL).select(SPECIAL).sum().row(0, named=True)
    g = special_on_ice(season).select(ST_COLS).sum().row(0, named=True)
    return {"pp": t["pp_xgf"] / t["sec_pp"], "sh": g["pp_xga"] / t["sec_pp"],
            "pp_g": g["pp_gf"] / t["sec_pp"], "sh_g": g["pp_ga"] / t["sec_pp"]}


def run(season: int = CURRENT_SEASON, only: set[int] | None = None) -> dict:
    """Swing for every finished game, or only the games in `only` (the quick game-night pass). Games whose shift
    chart the NHL is still filling in get none yet."""
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type").is_in(types_for(season))) & pl.col("state").is_in(["OFF", "FINAL"]))
    info = {r["game_id"]: r for r in games.iter_rows(named=True)}
    abbr = {**dict(zip(games["home_id"], games["home"])), **dict(zip(games["away_id"], games["away"]))}
    names = {r["player_id"]: f'{r["first"][0]}. {r["last"]}' for r in pl.read_parquet(d / "players.parquet").sort("game_id").iter_rows(named=True)}
    league = league_rates(season - 1)
    wanted = (set(info) if only is None else set(info) & set(only)) - pending.games(season)
    now = table(season, _ratings(season - 1), league).filter(pl.col("game_id").is_in(list(wanted)))
    ref = table(season - 1, _ratings(season - 2), league_rates(season - 2))
    pools = {g: np.sort(ref.filter(pl.col("grp") == g)["score"].to_numpy()) for g in ("F", "D", "G")}
    pct = lambda g, v: int(max(1, min(99, round(100 * np.searchsorted(pools[g], v, side="right") / len(pools[g]))))) if len(pools[g]) else None

    # Tonight's lines and pairs: the trios and pairs that played most together at 5-on-5 in this game.
    gu = pl.read_parquet(d / "game_units.parquet") if (d / "game_units.parquet").exists() else None
    def lines(gid: int, team_id: int) -> list[dict]:
        if gu is None:
            return []
        mine = gu.filter((pl.col("game_id") == gid) & (pl.col("team_id") == team_id))
        out = []
        for kind, count in (("F", 4), ("D", 3)):
            for i, u in enumerate(U._pick(mine.filter(pl.col("kind") == kind), count), 1):
                out.append({"kind": kind, "n": i, "ids": [int(x) for x in u["unit"].split("-")], "sec": int(u["sec"]),
                            "xgf": round(u["xgf"], 2), "xga": round(u["xga"], 2)})
        return out

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json") if only is None else ():
        old.unlink()
    for (gid,), rows in now.group_by("game_id"):
        g = info[gid]
        players = []
        for r in rows.sort("score", descending=True).iter_rows(named=True):
            players.append({
                "id": r["player_id"], "name": names.get(r["player_id"], "?"), "team": abbr.get(r["team_id"]), "pos": r["pos"],
                "score": round(r["score"], 2), "pct": pct(r["grp"], r["score"]),
                "parts": {k: round(r[k], 2) for k in (("net",) if r["pos"] == "G" else ("sheet", "play", "special", "usage"))},
                **({"st": st} if (st := {k: int(r[k]) for k in ST_GOALS if r[k]}) else {})})
        units = {g["away"]: lines(gid, g["away_id"]), g["home"]: lines(gid, g["home_id"])}
        (OUT / f"{gid}.json").write_text(json.dumps({"game_id": gid, "away": g["away"], "home": g["home"], "players": players, "units": units}, separators=(",", ":"), ensure_ascii=False))
    return {"games": now["game_id"].n_unique()}


if __name__ == "__main__":
    print(run())
