"""One row per player per regular-season game: ice time by strength, what happened with him on the ice at 5-on-5
(raw and score-and-venue adjusted, plus his team's totals so the rest of the time can be worked out), and his own
shots, points, hits, blocks, takeaways, giveaways, faceoffs and penalties.

Hits, giveaways, takeaways and blocks also come arena-adjusted: divided by the home arena's scorer factor and
weighted to put home and road games on the same footing (see rink_bias).
"""
import polars as pl

from pipeline.config import REGULAR, TABLES
from pipeline.metrics import adjust, rink_bias

ATTEMPTS = ["shot-on-goal", "missed-shot", "goal", "blocked-shot"]
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
PENALTIES = ["MIN", "MAJ"]  # real infractions that put a team shorthanded; misconducts and bench minors are left out


def _sum(expr: pl.Expr, cond: pl.Expr) -> pl.Expr:
    return pl.when(cond).then(expr).otherwise(0.0).sum()


def build(season: int) -> pl.DataFrame:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).select("game_id", "home_id", "away_id")
    roster = pl.read_parquet(d / "players.parquet").select("game_id", "player_id", "team_id", "is_home", "pos")

    # Ice time by strength, from stints.
    st = pl.read_parquet(d / "stints.parquet").join(games, on="game_id")
    both = pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null()
    sides = []
    for home, own, opp, sk in ((True, "n_home", "n_away", "home_skaters"), (False, "n_away", "n_home", "away_skaters")):
        sides.append(st.select(
            "game_id", "duration", pl.col(sk).alias("player_id"), pl.lit(home).alias("home"),
            s5=both & (pl.col(own) == 5) & (pl.col(opp) == 5), pp=both & (pl.col(own) > pl.col(opp)), pk=both & (pl.col(own) < pl.col(opp))))
    stint_sides = pl.concat(sides)
    toi = stint_sides.explode("player_id").drop_nulls("player_id").group_by("game_id", "player_id").agg(
        pl.col("duration").sum().cast(pl.Float64).alias("sec"), _sum(pl.col("duration"), pl.col("s5")).alias("sec5"),
        _sum(pl.col("duration"), pl.col("pp")).alias("sec_pp"), _sum(pl.col("duration"), pl.col("pk")).alias("sec_pk"))
    team_sec5 = stint_sides.group_by("game_id", "home").agg(_sum(pl.col("duration"), pl.col("s5")).alias("t_sec5"))

    # Shot attempts with our expected-goal rating and the score-and-venue weight of the shooting side.
    w = adjust.weights()
    wt = {m: w.filter(pl.col("metric") == m).select("home_state", pl.col("w_home").alias(f"wh_{m}"), pl.col("w_away").alias(f"wa_{m}")) for m in ("cf", "xgf")}
    ev = pl.read_parquet(d / "events.parquet").join(games, on="game_id")
    xg = pl.read_parquet(d / "shots_xg_own.parquet")
    shots = ev.filter(pl.col("type").is_in(ATTEMPTS) & pl.col("is_home").is_not_null()).join(xg, on=["game_id", "event_id"], how="left").with_columns(
        pl.col("xg").fill_null(0.0), home_state=(pl.col("home_score") - pl.col("away_score")).clip(-3, 3),
        n_own=pl.when(pl.col("is_home")).then(pl.col("home_on").list.len()).otherwise(pl.col("away_on").list.len()),
        n_opp=pl.when(pl.col("is_home")).then(pl.col("away_on").list.len()).otherwise(pl.col("home_on").list.len()),
        own_g=pl.when(pl.col("is_home")).then(pl.col("home_goalie")).otherwise(pl.col("away_goalie")).is_not_null(),
        opp_g=pl.when(pl.col("is_home")).then(pl.col("away_goalie")).otherwise(pl.col("home_goalie")).is_not_null())
    for m in ("cf", "xgf"):
        shots = shots.join(wt[m], on="home_state", how="left").with_columns(
            pl.when(pl.col("is_home")).then(pl.col(f"wh_{m}")).otherwise(pl.col(f"wa_{m}")).alias(f"w_{m}")).drop(f"wh_{m}", f"wa_{m}")
    shots = shots.with_columns(
        s5=pl.col("own_g") & pl.col("opp_g") & (pl.col("n_own") == 5) & (pl.col("n_opp") == 5),
        pp=pl.col("own_g") & pl.col("opp_g") & (pl.col("n_own") > pl.col("n_opp")),
        unb=pl.col("type").is_in(UNBLOCKED), goal=pl.col("type") == "goal")

    # On the ice at 5-on-5, for and against.
    s5 = shots.filter("s5")
    on = []
    for home, col in ((True, "home_on"), (False, "away_on")):
        on.append(s5.select("game_id", pl.col(col).alias("player_id"), (pl.col("is_home") == home).alias("mine"), "xg", "unb", "goal", "w_cf", "w_xgf", pl.lit(home).alias("home")))
    on = pl.concat(on)
    agg = lambda prefix: [
        _sum(pl.lit(1.0), pl.col("mine")).alias(f"{prefix}cf"), _sum(pl.lit(1.0), ~pl.col("mine")).alias(f"{prefix}ca"),
        _sum(pl.col("w_cf"), pl.col("mine")).alias(f"{prefix}cf_adj"), _sum(pl.col("w_cf"), ~pl.col("mine")).alias(f"{prefix}ca_adj"),
        _sum(pl.col("xg"), pl.col("mine") & pl.col("unb")).alias(f"{prefix}xgf"), _sum(pl.col("xg"), ~pl.col("mine") & pl.col("unb")).alias(f"{prefix}xga"),
        _sum(pl.col("xg") * pl.col("w_xgf"), pl.col("mine") & pl.col("unb")).alias(f"{prefix}xgf_adj"), _sum(pl.col("xg") * pl.col("w_xgf"), ~pl.col("mine") & pl.col("unb")).alias(f"{prefix}xga_adj"),
        _sum(pl.lit(1.0), pl.col("mine") & pl.col("goal")).alias(f"{prefix}gf"), _sum(pl.lit(1.0), ~pl.col("mine") & pl.col("goal")).alias(f"{prefix}ga")]
    onice = on.explode("player_id").group_by("game_id", "player_id").agg(agg("on_"))
    team = on.group_by("game_id", "home").agg(agg("t_")).join(team_sec5, on=["game_id", "home"], how="full", coalesce=True)

    # His own shots, goals and assists.
    real_net = pl.col("opp_g")  # not an empty net
    ind = shots.filter(pl.col("p1").is_not_null()).group_by("game_id", pl.col("p1").alias("player_id")).agg(
        pl.len().cast(pl.Float64).alias("icf"), _sum(pl.lit(1.0), pl.col("s5")).alias("icf5"),
        _sum(pl.col("xg"), pl.col("unb") & pl.col("s5")).alias("ixg5"), _sum(pl.col("xg"), pl.col("unb") & pl.col("pp")).alias("ixg_pp"),
        _sum(pl.col("xg"), pl.col("unb") & real_net).alias("ixg_net"), _sum(pl.lit(1.0), pl.col("unb") & real_net).alias("iff_net"),
        _sum(pl.lit(1.0), pl.col("goal") & real_net).alias("g_net"), _sum(pl.lit(1.0), pl.col("goal")).alias("g"),
        _sum(pl.lit(1.0), pl.col("goal") & pl.col("s5")).alias("g5"), _sum(pl.lit(1.0), pl.col("goal") & pl.col("pp")).alias("g_pp"),
        _sum(pl.lit(1.0), pl.col("type").is_in(["shot-on-goal", "goal"])).alias("sog"))
    goals = shots.filter("goal")
    a1 = goals.filter(pl.col("p2").is_not_null()).group_by("game_id", pl.col("p2").alias("player_id")).agg(
        pl.len().cast(pl.Float64).alias("a1"), _sum(pl.lit(1.0), pl.col("s5")).alias("a1_5"), _sum(pl.lit(1.0), pl.col("pp")).alias("a1_pp"))
    a2 = goals.filter(pl.col("p3").is_not_null()).group_by("game_id", pl.col("p3").alias("player_id")).agg(pl.len().cast(pl.Float64).alias("a2"))

    # Hits, blocks, takeaways, giveaways: as recorded, and arena-adjusted for the player credited.
    f = rink_bias.factors().select(pl.col("team_id").alias("home_id"), "hits", "gives", "takes", "blocks")
    hr = rink_bias.home_road()
    venue = roster.select("game_id", "player_id", pl.col("is_home").alias("p_home"))
    credited = []
    for typ, who, event in (("hit", "p1", "hits"), ("giveaway", "p1", "gives"), ("takeaway", "p1", "takes"), ("blocked-shot", "p2", "blocks")):
        part = ev.filter((pl.col("type") == typ) & pl.col(who).is_not_null()).select("game_id", "home_id", pl.col(who).alias("player_id"))
        part = part.join(venue, on=["game_id", "player_id"]).join(f.select("home_id", pl.col(event).alias("f")), on="home_id", how="left").with_columns(pl.col("f").fill_null(1.0))
        credited.append(part.select("game_id", "player_id", pl.lit(event).alias("event"),
                                    (pl.when(pl.col("p_home")).then(hr[event]["w_home"]).otherwise(hr[event]["w_away"]) / pl.col("f")).alias("adj")))
    credited = pl.concat(credited).group_by("game_id", "player_id").agg(
        *[_sum(pl.lit(1.0), pl.col("event") == e).alias(e) for e in ("hits", "gives", "takes", "blocks")],
        *[_sum(pl.col("adj"), pl.col("event") == e).alias(f"{e}_adj") for e in ("hits", "gives", "takes", "blocks")])
    hit_on = ev.filter((pl.col("type") == "hit") & pl.col("p2").is_not_null()).group_by("game_id", pl.col("p2").alias("player_id")).agg(pl.len().cast(pl.Float64).alias("hits_taken"))
    fo = ev.filter(pl.col("type") == "faceoff")
    fow = fo.filter(pl.col("p1").is_not_null()).group_by("game_id", pl.col("p1").alias("player_id")).agg(pl.len().cast(pl.Float64).alias("fow"))
    fol = fo.filter(pl.col("p2").is_not_null()).group_by("game_id", pl.col("p2").alias("player_id")).agg(pl.len().cast(pl.Float64).alias("fol"))
    pen = ev.filter((pl.col("type") == "penalty") & pl.col("pen_type").is_in(PENALTIES))
    taken = pen.filter(pl.col("p1").is_not_null()).group_by("game_id", pl.col("p1").alias("player_id")).agg(pl.len().cast(pl.Float64).alias("pen_taken"))
    drawn = pen.filter(pl.col("p2").is_not_null()).group_by("game_id", pl.col("p2").alias("player_id")).agg(pl.len().cast(pl.Float64).alias("pen_drawn"))

    out = roster.join(games.select("game_id"), on="game_id").join(toi, on=["game_id", "player_id"])
    for part in (onice, ind, a1, a2, credited, hit_on, fow, fol, taken, drawn):
        out = out.join(part, on=["game_id", "player_id"], how="left")
    out = out.join(team.rename({"home": "is_home"}), on=["game_id", "is_home"], how="left")
    num = [c for c, t in out.schema.items() if t == pl.Float64]
    out = out.with_columns(pl.col(num).fill_null(0.0)).filter(pl.col("sec") > 0)
    out.write_parquet(d / "player_game.parquet")
    return out


if __name__ == "__main__":
    import sys

    for s in sys.argv[1:]:
        t = build(int(s))
        print(s, t.shape)
