"""Pattern breaks: things a team is doing differently from its own habits.

First detector here: a regular who is still with the team but has stopped dressing, who is playing in his spot,
who in turn is covering for that player, and how the affected line's ice time has changed.
The data cannot say why a player is out (injury, suspension, scratch), so the wording never does either.
"""
import gzip
import json
from collections import defaultdict

import polars as pl

from pipeline.config import NHL_WEB, RAW, REGULAR, TABLES
from pipeline.ingest.client import get

LOOKBACK = 25  # team games at the end of last season that define a "regular"
MIN_SHARE = 0.6
MIN_MISSED = 3


def _current_team(player_id: int, today: str) -> str | None:
    """Which club the league lists the player with now (cached per day)."""
    path = RAW / "players" / f"{player_id}.json.gz"
    if path.exists():
        with gzip.open(path, "rt") as f:
            cached = json.load(f)
        if cached.get("checked") == today:
            return cached.get("team")
    data = get(f"{NHL_WEB}/v1/player/{player_id}/landing").json()
    # A player no longer active in the league (retired, overseas) counts as having no club.
    out = {"checked": today, "team": data.get("currentTeamAbbrev") if data.get("isActive") else None, "active": data.get("isActive")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        json.dump(out, f)
    return out["team"]


def _team_frame(season: int):
    d = TABLES / str(season)
    games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).sort("date", "game_id")
    built = set(pl.read_parquet(d / "toi_check.parquet")["game_id"].unique().to_list())
    games = games.filter(pl.col("game_id").is_in(list(built)))
    players = pl.read_parquet(d / "players.parquet")
    toi = pl.read_parquet(d / "toi_check.parquet").filter(pl.col("shift_toi") > 0).select("game_id", "player_id", "shift_toi")
    dressed = players.join(toi, on=["game_id", "player_id"])
    units = pl.read_parquet(d / "game_units.parquet")
    return games, dressed, units


def _mates(units: pl.DataFrame, tid: int, game_ids: list[int], pid: int, kind: str) -> tuple[tuple[int, ...], float, int] | None:
    """The group a player was in most over some games: (his partners, seconds a game together, games together)."""
    rows = units.filter((pl.col("team_id") == tid) & (pl.col("kind") == kind) & pl.col("game_id").is_in(game_ids))
    total, games = defaultdict(float), defaultdict(set)
    for r in rows.iter_rows(named=True):
        members = tuple(int(x) for x in r["unit"].split("-"))
        if pid in members:
            total[members] += r["sec"]
            games[members].add(r["game_id"])
    if not total:
        return None
    best = max(total, key=total.get)
    return tuple(m for m in best if m != pid), total[best] / len(games[best]), len(games[best])


def _with(units: pl.DataFrame, tid: int, game_ids: list[int], partners: tuple[int, ...], kind: str) -> list[tuple[int, float, int]]:
    """Who has played alongside these partners (all of them at once): [(player, seconds a game, games)]."""
    rows = units.filter((pl.col("team_id") == tid) & (pl.col("kind") == kind) & pl.col("game_id").is_in(game_ids))
    total, games = defaultdict(float), defaultdict(set)
    for r in rows.iter_rows(named=True):
        members = {int(x) for x in r["unit"].split("-")}
        if set(partners) <= members:
            for other in members - set(partners):
                total[other] += r["sec"]
                games[other].add(r["game_id"])
    return sorted(((p, total[p] / len(games[p]), len(games[p])) for p in total), key=lambda x: -x[1] * x[2])


def absences(season: int, today: str) -> dict[int, list[dict]]:
    """Per team: regulars who are still with the club but not dressing, and the knock-on changes."""
    g_now, d_now, u_now = _team_frame(season)
    g_old, d_old, u_old = _team_frame(season - 1)
    abbr = {**dict(zip(g_now["home_id"], g_now["home"])), **dict(zip(g_now["away_id"], g_now["away"]))}
    out = {}
    for tid, team in abbr.items():
        now_games = g_now.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid))["game_id"].to_list()
        old_games = g_old.filter((pl.col("home_id") == tid) | (pl.col("away_id") == tid))["game_id"].to_list()[-LOOKBACK:]
        if len(now_games) < MIN_MISSED or not old_games:
            continue
        old = d_old.filter((pl.col("team_id") == tid) & pl.col("game_id").is_in(old_games) & (pl.col("pos") != "G"))
        regular = old.group_by("player_id").agg(pl.len().alias("gp"), pl.col("pos").first(), pl.col("shift_toi").mean().alias("toi"))
        # Rank last season's forwards and defensemen by ice time, to describe where a fill-in used to play.
        rank = {}
        for group in (["C", "L", "R"], ["D"]):
            ordered = regular.filter(pl.col("pos").is_in(group) & (pl.col("gp") >= 0.4 * len(old_games))).sort("toi", descending=True)
            for i, pid in enumerate(ordered["player_id"].to_list()):
                rank[pid] = i + 1
        recent = set(d_now.filter((pl.col("team_id") == tid) & pl.col("game_id").is_in(now_games[-MIN_MISSED:]))["player_id"].to_list())
        played_now = d_now.filter((pl.col("team_id") == tid) & pl.col("game_id").is_in(now_games)).group_by("player_id").len()
        gp_now = dict(zip(played_now["player_id"], played_now["len"]))
        notes = []
        for r in regular.filter(pl.col("gp") >= MIN_SHARE * len(old_games)).iter_rows(named=True):
            pid = r["player_id"]
            if pid in recent or _current_team(pid, today) != team:
                continue
            kind = "D" if r["pos"] == "D" else "F"
            usual = _mates(u_old, tid, old_games, pid, kind)
            if usual is None:
                continue
            partners, old_sec, _ = usual
            missed = len(now_games) - gp_now.get(pid, 0)
            note = {"player": pid, "missed": missed, "of": len(now_games), "partners": list(partners), "kind": kind, "old_min": round(old_sec / 60, 1), "old_sec": round(old_sec), "rank": rank.get(pid)}
            cover = [c for c in _with(u_now, tid, now_games, partners, kind) if c[0] != pid]
            if cover:
                sub, sec, n = cover[0]
                note |= {"fill_in": sub, "now_min": round(sec / 60, 1), "now_sec": round(sec), "fill_games": n, "fill_rank": rank.get(sub)}
                # Second link in the chain: who is covering the fill-in's own old spot?
                was = _mates(u_old, tid, old_games, sub, kind)
                if was and set(was[0]) != set(partners):
                    chain = [c for c in _with(u_now, tid, now_games, was[0], kind) if c[0] not in (sub, pid)]
                    if chain:
                        note |= {"fill_old_partners": list(was[0]), "chain": chain[0][0]}
            else:
                note["split"] = True
            notes.append(note)
        if notes:
            out[tid] = sorted(notes, key=lambda n: rank.get(n["player"], 99))
    return out
