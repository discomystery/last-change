"""How a finished game unfolded: every unblocked shot in game time, the power plays, and every shift.

One small file per finished regular-season game, `flow/{game_id}.json`, read by the post-game page's
"How it unfolded" section in the visitor's browser (so the numbers are not copied into every page).
Shootouts are left out. Shots are rows [sec, home, xg, goal, x, y, shooter, empty net]; shifts are flat
[start, end, start, end, ...] lists in game seconds, merged across line changes within a period.
Lines and pairs are tonight's: the trios and pairs that played most together at 5-on-5 in this game,
picked the same way as the Swing section's line tags.
"""
import json

import polars as pl

from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import pending
from pipeline.metrics import units as U

OUT = SITE_DATA / "flow"
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
MIN_PP = 10  # seconds; shorter manpower edges are line-change blips, not power plays


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


def game(gid: int, g: dict, ev: pl.DataFrame, xg: pl.DataFrame, st: pl.DataFrame, gu: pl.DataFrame | None, roster: pl.DataFrame) -> dict:
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
        shots.append([int(r["sec"]), h, round(r["xg"], 3), int(r["type"] == "goal"), round(x, 1), round(y, 1), r["p1"], en])
    pp = power_plays([(int(s), c) for s, c in ev.select("sec", "situation_code").iter_rows()], length)

    # Tonight's lines and pairs by 5-on-5 time together; anyone else is listed under "other".
    group: dict[int, str] = {}
    if gu is not None:
        for team_id in (g["away_id"], g["home_id"]):
            mine = gu.filter(pl.col("team_id") == team_id)
            for kind, prefix, count in (("F", "L", 4), ("D", "P", 3)):
                for i, u in enumerate(U._pick(mine.filter(pl.col("kind") == kind), count), 1):
                    for pid in u["unit"].split("-"):
                        group[int(pid)] = f"{prefix}{i}"

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
    # Shooters not on a shift chart (rare) still need a name.
    names = {s[6]: name(s[6]) for s in shots if s[6] is not None and s[6] not in skaters and s[6] not in goalies}
    return {"game_id": gid, "len": length, "shots": shots, "pp": pp, "players": players, "goalies": gks, **({"names": names} if names else {})}


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
        out = game(gid, g, ev.filter(pl.col("game_id") == gid), xg.filter(pl.col("game_id") == gid).drop("game_id"), sub,
                   gu.filter(pl.col("game_id") == gid) if gu is not None else None, roster.filter(pl.col("game_id") == gid))
        (OUT / f"{gid}.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        n += 1
    return {"games": n}


if __name__ == "__main__":
    print(run())
