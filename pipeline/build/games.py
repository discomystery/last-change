"""Build the per-season tables (games, players, shifts check, stints, events) from raw responses."""
import statistics

import polars as pl

from pipeline.config import FINAL_STATES, REGULAR, TABLES
from pipeline.build.stints import Shift, build_stints, clean_shifts, clock_to_seconds, game_seconds, on_ice
from pipeline.ingest import nhl

SHOT_TYPES = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}
UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
# (primary, secondary, tertiary) player fields per event type
PLAYER_FIELDS = {
    "shot-on-goal": ("shootingPlayerId", None, None),
    "missed-shot": ("shootingPlayerId", None, None),
    "blocked-shot": ("shootingPlayerId", "blockingPlayerId", None),
    "goal": ("scoringPlayerId", "assist1PlayerId", "assist2PlayerId"),
    "hit": ("hittingPlayerId", "hitteePlayerId", None),
    "giveaway": ("playerId", None, None),
    "takeaway": ("playerId", None, None),
    "faceoff": ("winningPlayerId", "losingPlayerId", None),
    "penalty": ("committedByPlayerId", "drawnByPlayerId", "servedByPlayerId"),
}


def build_game(season: int, game: dict) -> dict:
    gid = game["game_id"]
    pbp, box = (nhl.read_raw(season, k, gid) for k in ("pbp", "box"))
    shift_rows, shift_source = nhl.read_shifts(season, gid)
    home_id, away_id = pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]
    roster = {r["playerId"]: r for r in pbp["rosterSpots"]}
    team_of = {pid: r["teamId"] for pid, r in roster.items()}
    goalies = {pid for pid, r in roster.items() if r["positionCode"] == "G"}
    # Regular-season period 5 is the shootout; it is excluded from everything.
    max_period = 4 if game["game_type"] == REGULAR else 99
    shifts = clean_shifts(shift_rows, home_id, goalies, max_period=max_period)

    players = [
        {"game_id": gid, "player_id": pid, "team_id": r["teamId"], "is_home": r["teamId"] == home_id, "pos": r["positionCode"],
         "first": r["firstName"]["default"], "last": r["lastName"]["default"], "number": r.get("sweaterNumber")}
        for pid, r in roster.items()
    ]

    # Ice time from shifts against the official boxscore.
    shift_toi: dict[int, int] = {}
    for s in shifts:
        shift_toi[s.player_id] = shift_toi.get(s.player_id, 0) + s.end - s.start
    toi_rows = []
    for side in ("homeTeam", "awayTeam"):
        for group in ("forwards", "defense", "goalies"):
            for p in box["playerByGameStats"][side][group]:
                official = clock_to_seconds(p["toi"])
                if official == 0 and p["playerId"] not in shift_toi:
                    continue
                toi_rows.append({"game_id": gid, "player_id": p["playerId"], "goalie": group == "goalies", "box_toi": official, "shift_toi": shift_toi.get(p["playerId"], 0)})

    stints = []
    for st in build_stints(shifts):
        hs, as_ = st["home_skaters"], st["away_skaters"]
        stints.append({"game_id": gid, "period": st["start"] // 1200 + 1, **st, "duration": st["end"] - st["start"],
                       "n_home": len(hs), "n_away": len(as_), "flag": not (3 <= len(hs) <= 6 and 3 <= len(as_) <= 6)})

    events, hs_, as_ = [], 0, 0
    for p in sorted(pbp["plays"], key=lambda x: x["sortOrder"]):
        pd, typ, d = p["periodDescriptor"], p["typeDescKey"], p.get("details") or {}
        if pd["periodType"] == "SO":
            continue
        sec = game_seconds(pd["number"], p["timeInPeriod"])
        f1, f2, f3 = PLAYER_FIELDS.get(typ, (None, None, None))
        p1, p2, p3 = (d.get(f) if f else None for f in (f1, f2, f3))
        # Blocked shots: the owner field is unreliable, so take the shooter's team from the roster.
        team_id = team_of.get(p1) if typ in SHOT_TYPES and p1 in team_of else d.get("eventOwnerTeamId")
        is_home = None if team_id is None else team_id == home_id
        on = on_ice(shifts, sec, faceoff=typ == "faceoff")
        home_on = sorted(s.player_id for s in on if s.home and not s.goalie)
        away_on = sorted(s.player_id for s in on if not s.home and not s.goalie)
        hg = next((s.player_id for s in on if s.home and s.goalie), None)
        ag = next((s.player_id for s in on if not s.home and s.goalie), None)
        x, y = d.get("xCoord"), d.get("yCoord")
        side = p.get("homeTeamDefendingSide")
        flip = None if (x is None or is_home is None or side is None) else (side == "right") == is_home
        events.append({
            "game_id": gid, "event_id": p["eventId"], "sort": p["sortOrder"], "period": pd["number"], "period_type": pd["periodType"],
            "sec": sec, "type": typ, "team_id": team_id, "is_home": is_home, "p1": p1, "p2": p2, "p3": p3,
            "goalie_id": d.get("goalieInNetId"), "shot_type": d.get("shotType"), "reason": d.get("reason"),
            "pen_type": d.get("typeCode") if typ == "penalty" else None, "pen_desc": d.get("descKey") if typ == "penalty" else None,
            "pen_min": d.get("duration") if typ == "penalty" else None,
            "x": x, "y": y, "x_norm": None if flip is None else (-x if flip else x), "y_norm": None if flip is None else (-y if flip else y),
            "home_score": hs_, "away_score": as_, "situation_code": p.get("situationCode"),
            "home_on": home_on, "away_on": away_on, "home_goalie": hg, "away_goalie": ag,
        })
        if typ == "goal":
            hs_, as_ = d.get("homeScore", hs_), d.get("awayScore", as_)

    # Check the attack direction empirically: a team's shots should sit in the +x half. Trust the data.
    flips = 0
    periods = {e["period"] for e in events}
    for per in periods:
        for home in (True, False):
            xs = [e["x_norm"] for e in events if e["period"] == per and e["is_home"] is home and e["type"] in UNBLOCKED and e["x_norm"] is not None]
            if len(xs) >= 3 and statistics.median(xs) < 0:
                flips += 1
                for e in events:
                    if e["period"] == per and e["is_home"] is home and e["x_norm"] is not None:
                        e["x_norm"], e["y_norm"] = -e["x_norm"], -e["y_norm"]
    for e in events:
        xn = e["x_norm"]
        e["zone"] = None if xn is None else ("O" if xn > 25 else "D" if xn < -25 else "N")

    return {"players": players, "toi": toi_rows, "stints": stints, "events": events, "direction_flips": flips, "shift_source": shift_source}


EVENT_SCHEMA = {"p1": pl.Int64, "p2": pl.Int64, "p3": pl.Int64, "goalie_id": pl.Int64, "home_goalie": pl.Int64, "away_goalie": pl.Int64,
                "x": pl.Int64, "y": pl.Int64, "x_norm": pl.Int64, "y_norm": pl.Int64, "pen_min": pl.Int64, "team_id": pl.Int64,
                "home_on": pl.List(pl.Int64), "away_on": pl.List(pl.Int64), "shot_type": pl.String, "reason": pl.String,
                "pen_type": pl.String, "pen_desc": pl.String, "zone": pl.String, "is_home": pl.Boolean}
STINT_SCHEMA = {"home_goalie": pl.Int64, "away_goalie": pl.Int64, "home_skaters": pl.List(pl.Int64), "away_skaters": pl.List(pl.Int64)}


def build_season(season: int) -> dict:
    games = [g for g in nhl.season_games(season) if g["state"] in FINAL_STATES and nhl.raw_path(season, "pbp", g["game_id"]).exists()]
    acc = {"players": [], "toi": [], "stints": [], "events": []}
    flips, failed, sources = 0, [], {}
    for g in games:
        try:
            out = build_game(season, g)
        except Exception as exc:  # keep going; report every game that would not build
            failed.append({"game_id": g["game_id"], "error": repr(exc)})
            continue
        flips += out.pop("direction_flips")
        src = out.pop("shift_source")
        sources[src] = sources.get(src, 0) + 1
        for k, rows in out.items():
            acc[k].extend(rows)
    out_dir = TABLES / str(season)
    out_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(games).write_parquet(out_dir / "games.parquet")
    pl.DataFrame(acc["players"]).write_parquet(out_dir / "players.parquet")
    pl.DataFrame(acc["toi"]).write_parquet(out_dir / "toi_check.parquet")
    pl.DataFrame(acc["stints"], schema_overrides=STINT_SCHEMA, infer_schema_length=None).write_parquet(out_dir / "stints.parquet")
    pl.DataFrame(acc["events"], schema_overrides=EVENT_SCHEMA, infer_schema_length=None).write_parquet(out_dir / "events.parquet")
    return {"season": season, "games": len(games), "events": len(acc["events"]), "stints": len(acc["stints"]), "direction_flips": flips, "shift_sources": sources, "failed": failed}
