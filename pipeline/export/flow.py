"""How a finished game unfolded: every unblocked shot in game time, the power plays, and every shift.

One small file per finished regular-season game, `flow/{game_id}.json`, read by the post-game page's
"How it unfolded" section in the visitor's browser (so the numbers are not copied into every page).
Shots are rows [sec, home, xg, goal, x, y, shooter, empty net, strength, shot type, assist, assist]; shifts are flat
[start, end, start, end, ...] lists in game seconds, merged across line changes within a period. Also (Sora, 2026-10-14,
thread on "how it unfolded"): blocked shots [sec, shooter's side, x, y, blocker, shooter] in the shooter's attacking
direction like shots; penalties [sec, side that took it, player, drawn by, minutes, type code, infraction, served by];
skaters on each side through the game from the shifts, `str` rows [sec, away skaters, home skaters, away goalie in,
home goalie in]; and the shootout, if any, as attempts in order [side, shooter, goalie, 1 scored / 0 saved / -1 missed,
shot type], read from the raw play-by-play because the event table leaves shootouts out (no chance figures for them).
Lines and pairs are tonight's: the trios and pairs that played most together at 5-on-5 in this game,
picked the same way as the Swing section's line tags.
"""
import json

import polars as pl

from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import pending
from pipeline.ingest import nhl
from pipeline.metrics import units as U

OUT = SITE_DATA / "flow"
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
MIN_PP = 10  # seconds; shorter manpower edges are line-change blips, not power plays
MIN_STRENGTH = 5  # seconds; shorter skater counts in the shifts are line changes, folded into what came before
SO_RESULT = {"goal": 1, "shot-on-goal": 0, "missed-shot": -1, "failed-shot-attempt": -1}


def merge(spans: list[tuple[int, int, int]]) -> list[int]:
    """(period, start, end) stretches on the ice -> flat shifts, joined when they touch within one period."""
    out: list[list[int]] = []
    last_period = None
    for period, a, b in sorted(spans, key=lambda s: s[1]):
        if out and period == last_period and out[-1][1] >= a:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
        last_period = period
    return [int(v) for s in out for v in s]


def power_plays(codes: list[tuple[int, str]], end: int) -> list[list[int]]:
    """[home side, start, end] for each stretch where both goalies are in and one team has more skaters."""
    out, cur = [], None
    for sec, c in codes:
        if not c or len(c) != 4:
            continue
        a, h = int(c[1]), int(c[2])
        side = (1 if h > a else 0) if c[0] == "1" and c[3] == "1" and a != h else None
        if side != (cur[0] if cur else None):
            if cur:
                out.append([cur[0], cur[1], sec])
            cur = [side, sec] if side is not None else None
    if cur:
        out.append([cur[0], cur[1], end])
    return [p for p in out if p[2] - p[1] >= MIN_PP]


def strength(c: str, h: int) -> int:
    """1 when the shooting side had more skaters (power play), -1 when it had fewer (shorthanded), else 0.
    Only with both goalies in, so empty nets and penalty shots read 0."""
    if len(c) != 4 or c[0] != "1" or c[3] != "1":
        return 0
    own, other = (int(c[2]), int(c[1])) if h else (int(c[1]), int(c[2]))
    return (own > other) - (own < other)


def skaters_on(st: pl.DataFrame) -> list[list[int]]:
    """[start, away skaters, home skaters, away goalie in, home goalie in] each time the count changes, from the shift
    stints; stretches under MIN_STRENGTH seconds (players crossing on a change) count as what came before."""
    out: list[list[int]] = []  # [start, away, home, away goalie, home goalie, end]
    for r in st.sort("start").iter_rows(named=True):
        v = [int(r["n_away"]), int(r["n_home"]), int(r["away_goalie"] is not None), int(r["home_goalie"] is not None)]
        if out and (out[-1][1:5] == v or r["end"] - r["start"] < MIN_STRENGTH):
            out[-1][5] = r["end"]
        else:
            out.append([r["start"], *v, r["end"]])
    return [s[:5] for s in out]


def shootout(pbp: dict) -> list[list]:
    """The shootout's attempts in order: [side (1 home), shooter, goalie, result, shot type]."""
    home = pbp["homeTeam"]["id"]
    out = []
    for p in sorted(pbp["plays"], key=lambda x: x["sortOrder"]):
        if p["periodDescriptor"].get("periodType") != "SO" or p["typeDescKey"] not in SO_RESULT:
            continue
        d = p.get("details") or {}
        out.append([int(d.get("eventOwnerTeamId") == home), d.get("scoringPlayerId") or d.get("shootingPlayerId"), d.get("goalieInNetId"),
                    SO_RESULT[p["typeDescKey"]], d.get("shotType")])
    return out


def game(gid: int, g: dict, ev: pl.DataFrame, xg: pl.DataFrame, st: pl.DataFrame, gu: pl.DataFrame | None, roster: pl.DataFrame,
         so: list[list] | None = None, season: int = CURRENT_SEASON) -> dict:
    ev = ev.filter(pl.col("period_type") != "SO").sort("sort")
    length = int(ev["sec"].max() or 0)
    shots = []
    for r in ev.filter(pl.col("type").is_in(UNBLOCKED)).join(xg, on="event_id").iter_rows(named=True):
        c = r["situation_code"] or "1551"
        h = int(bool(r["is_home"]))
        en = int(c[0] == "0") if h else int(c[3] == "0")
        x, y = r["x_norm"], r["y_norm"]
        if x is None or y is None:
            continue
        goal = r["type"] == "goal"
        shots.append([int(r["sec"]), h, round(r["xg"], 3), int(goal), round(x, 1), round(y, 1), r["p1"], en, strength(c, h),
                      r["shot_type"], r["p2"] if goal else None, r["p3"] if goal else None])
    blocks = []
    for r in ev.filter((pl.col("type") == "blocked-shot") & (pl.col("reason").fill_null("") != "teammate-blocked")).iter_rows(named=True):
        if r["x_norm"] is None or r["y_norm"] is None or r["is_home"] is None or r["p2"] is None:
            continue
        blocks.append([int(r["sec"]), int(r["is_home"]), round(r["x_norm"], 1), round(r["y_norm"], 1), r["p2"], r["p1"]])
    pens = [[int(r["sec"]), int(bool(r["is_home"])), r["p1"], r["p2"], r["pen_min"], r["pen_type"], r["pen_desc"], r["p3"]]
            for r in ev.filter((pl.col("type") == "penalty") & pl.col("is_home").is_not_null()).iter_rows(named=True)]
    pp = power_plays([(int(s), c) for s, c in ev.select("sec", "situation_code").iter_rows()], length)

    # Tonight's lines and pairs by 5-on-5 time together, numbered as team and preview pages number them; anyone
    # else is listed under "other".
    group: dict[int, str] = {}
    if gu is not None:
        for team_id in (g["away_id"], g["home_id"]):
            for _, label, u in U.game_lines(season, gu, gid, team_id):
                for pid in u["unit"].split("-"):
                    group.setdefault(int(pid), label)  # a forward on two lines (11 dressed) keeps the higher one

    skaters: dict[int, list] = {}
    goalies: dict[int, list] = {}
    side_of: dict[int, int] = {}
    for r in st.iter_rows(named=True):
        for h, sk, gk in ((0, "away_skaters", "away_goalie"), (1, "home_skaters", "home_goalie")):
            for pid in r[sk] or []:
                skaters.setdefault(pid, []).append((r["period"], r["start"], r["end"]))
                side_of[pid] = h
            if r[gk] is not None:
                goalies.setdefault(r[gk], []).append((r["period"], r["start"], r["end"]))
                side_of[r[gk]] = h
    info = {r["player_id"]: r for r in roster.iter_rows(named=True)}
    name = lambda pid: f'{info[pid]["first"][0]}. {info[pid]["last"]}' if pid in info else str(pid)
    players = []
    for pid, spans in skaters.items():
        pos = info.get(pid, {}).get("pos")
        grp = group.get(pid) or ("XD" if pos == "D" else "XF")
        players.append({"id": pid, "n": name(pid), "h": side_of[pid], "grp": grp, "s": merge(spans)})
    gks = [{"id": pid, "n": name(pid), "h": side_of[pid], "s": merge(spans)} for pid, spans in goalies.items()]
    # Shooters, passers, penalty takers and shootout shooters not on a shift chart still need a name.
    ids = {s[i] for s in shots for i in (6, 10, 11)} | {b[i] for b in blocks for i in (4, 5)} | {p[i] for p in pens for i in (2, 3, 7)}
    ids |= {a[i] for a in (so or []) for i in (1, 2)}
    names = {pid: name(pid) for pid in ids if pid is not None and pid not in skaters and pid not in goalies}
    return {"game_id": gid, "len": length, "shots": shots, "pp": pp, "players": players, "goalies": gks, "blocks": blocks, "pens": pens,
            "str": skaters_on(st), **({"so": so} if so else {}), **({"names": names} if names else {})}


def run(season: int = CURRENT_SEASON) -> dict:
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
    ids = [g for g in games["game_id"].to_list() if g not in pending.games(season)]  # unfinished shift charts wait
    games = games.filter(pl.col("game_id").is_in(ids))
    ev = pl.read_parquet(d / "events.parquet").filter(pl.col("game_id").is_in(ids))
    xg = pl.read_parquet(d / "shots_xg_own.parquet").filter(pl.col("game_id").is_in(ids))
    st = pl.read_parquet(d / "stints.parquet").filter(pl.col("game_id").is_in(ids))
    gu = pl.read_parquet(d / "game_units.parquet").filter(pl.col("game_id").is_in(ids)) if (d / "game_units.parquet").exists() else None
    roster = pl.read_parquet(d / "players.parquet").filter(pl.col("game_id").is_in(ids))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    n = 0
    for g in games.iter_rows(named=True):
        gid = g["game_id"]
        sub = st.filter(pl.col("game_id") == gid).sort("start")
        if sub.is_empty():
            continue
        so = shootout(nhl.read_raw(season, "pbp", gid)) if g.get("last_period") == "SO" else None
        out = game(gid, g, ev.filter(pl.col("game_id") == gid), xg.filter(pl.col("game_id") == gid).drop("game_id"), sub,
                   gu.filter(pl.col("game_id") == gid) if gu is not None else None, roster.filter(pl.col("game_id") == gid), so, season)
        (OUT / f"{gid}.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        n += 1
    return {"games": n}


if __name__ == "__main__":
    print(run())
