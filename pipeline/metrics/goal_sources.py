"""Where a team's goals come from, and where the goals it allows come from.

Each goal gets exactly one source, checked in this order: empty net, power play, shorthanded, goalie pulled
(extra attacker), then for the rest (even strength) rebound, off a turnover, off an offensive-zone faceoff win,
and everything else. Rush goals are not a source: public play-by-play records too little in the neutral zone to
find them (shots our rush rule flags score less often than ordinary shots, not more).

Each share also carries a luck band: where a league-average team's share would land 80% of the time with the
same number of goals, from chance alone. Teams differ far less in their goal mix than single seasons suggest.

Shares are shrunk toward the league share with a prior worth K goals, K estimated per source from how much teams
really differ over the three pooled seasons (beta-binomial method of moments). Ranges are 80% Beta intervals.
"""
import polars as pl
from scipy.stats import beta, binom

from pipeline.config import CURRENT_SEASON, TABLES

SOURCES = ["other", "pp", "rebound", "turnover", "faceoff", "en", "sh", "ea"]
FULL_SEASONS = [2023, 2024, 2025]
PP, SH = {"5v4", "5v3", "4v3"}, {"4v5", "3v5", "3v4"}
EVEN = {"5v5", "4v4", "3v3"}


def tagged(season: int) -> pl.DataFrame:
    """One row per goal with its source, scoring team and conceding team."""
    d = TABLES / str(season)
    g = pl.read_parquet(d / "goals.parquet")
    games = pl.read_parquet(d / "games.parquet").select("game_id", "home_id", "away_id")
    src = (
        pl.when(pl.col("strength") == "EN").then(pl.lit("en"))
        .when(pl.col("strength").is_in(list(PP))).then(pl.lit("pp"))
        .when(pl.col("strength").is_in(list(SH))).then(pl.lit("sh"))
        .when(pl.col("strength") == "EA").then(pl.lit("ea"))
        .when(pl.col("rebound")).then(pl.lit("rebound"))
        .when(pl.col("off_turnover")).then(pl.lit("turnover"))
        .when(pl.col("off_faceoff")).then(pl.lit("faceoff"))
        .otherwise(pl.lit("other"))
    )
    return (
        g.filter(pl.col("strength").is_in([*PP, *SH, *EVEN, "EN", "EA"]))
        .join(games, on="game_id")
        .with_columns(source=src, against=pl.when(pl.col("is_home")).then(pl.col("away_id")).otherwise(pl.col("home_id")))
        .select("game_id", "team_id", "against", "source")
    )


def _counts(goals: pl.DataFrame, side: str) -> pl.DataFrame:
    """Goals per (team, source) for side 'for' (team_id) or 'against' (conceding team)."""
    col = "team_id" if side == "for" else "against"
    return goals.group_by(pl.col(col).alias("team"), "source").agg(pl.len().alias("n"))


def prior_weight() -> dict[str, float]:
    """K per source: how many league-average goals the prior is worth, from real between-team spread."""
    rows = []
    for s in FULL_SEASONS:
        g = tagged(s)
        for side in ("for", "against"):
            c = _counts(g, side)
            tot = c.group_by("team").agg(pl.col("n").sum().alias("tot"))
            rows.append(c.join(tot, on="team").with_columns(season=pl.lit(s), side=pl.lit(side)))
    allc = pl.concat(rows)
    k = {}
    for src in SOURCES:
        # every team-season, including those with zero goals of this source
        teams = allc.select("team", "season", "side", "tot").unique()
        x = teams.join(allc.filter(pl.col("source") == src).select("team", "season", "side", "n"), on=["team", "season", "side"], how="left").fill_null(0)
        p = x["n"].sum() / x["tot"].sum()
        share = x["n"] / x["tot"]
        sampling = (p * (1 - p) / x["tot"]).mean()
        tau2 = max(share.var() - sampling, 1e-6)
        k[src] = round(min(max(p * (1 - p) / tau2 - 1, 10.0), 2000.0), 1)
    return k


def run(season: int = CURRENT_SEASON) -> dict:
    k = prior_weight()
    modes = {"blend": [season - 1, season], "season": [season]}
    pooled = {m: pl.concat([tagged(s) for s in yrs]) for m, yrs in modes.items()}
    league = pl.concat([tagged(s) for s in FULL_SEASONS])
    lg = {r["source"]: r["n"] / league.height for r in league.group_by("source").agg(pl.len().alias("n")).iter_rows(named=True)}
    out: dict[int, dict] = {}
    for mode, goals in pooled.items():
        for side in ("for", "against"):
            c = _counts(goals, side)
            for team in c["team"].unique().to_list():
                mine = {r["source"]: r["n"] for r in c.filter(pl.col("team") == team).iter_rows(named=True)}
                tot = sum(mine.values())
                rows = {}
                for src in SOURCES:
                    n, p0, kk = mine.get(src, 0), lg.get(src, 0.0), k[src]
                    a, b = kk * p0 + n, kk * (1 - p0) + tot - n
                    rows[src] = {"n": n, "share": round(100 * n / tot, 1) if tot else None, "est": round(100 * a / (a + b), 1),
                                 "lo": round(100 * float(beta.ppf(0.1, a, b)), 1), "hi": round(100 * float(beta.ppf(0.9, a, b)), 1),
                                 "band": [round(100 * float(binom.ppf(q, tot, p0)) / tot, 1) if tot else None for q in (0.1, 0.9)]}
                out.setdefault(team, {}).setdefault(mode, {})[side] = {"goals": tot, "sources": rows}
    return {"teams": out, "league": {s: round(100 * lg.get(s, 0.0), 1) for s in SOURCES}, "k": k}
