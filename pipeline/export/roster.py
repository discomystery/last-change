"""Every club's full roster with who is available, for the team page: site/public/data/roster.json.

Read from availability.parquet (metrics/availability.py) on its latest day. Each player carries the one status the
site shows, NHL.com's short injury note when there is one (with a link to the report), how many of the club's games
in a row he has missed, and his sweater number. Plus each club's roster moves over the last 30 days.

The site never says why a player is out unless NHL.com said it: a scratch with nothing reported reads "scratched",
and a player left off the game-day roster reads "not in the lineup".
"""
import json
from datetime import date as Date, timedelta

import polars as pl

from pipeline.config import SITE_DATA, TABLES
from pipeline.metrics import availability

# Most serious first; the site sorts the "out" group by this.
ORDER = ["injured", "suspended", "personal", "off_roster", "not_listed", "scratched", "playing", "available", "no_club"]


def _numbers(season: int) -> dict[int, int]:
    out: dict[int, int] = {}
    for f in ("players.parquet", "rosters.parquet"):  # the listing last, so today's number wins
        t = TABLES / str(season) / f
        if t.exists():
            for pid, n in pl.read_parquet(t, columns=["player_id", "number"]).drop_nulls().iter_rows():
                out[pid] = n
    return out


def run(season: int) -> dict:
    t = availability.load(season)
    if t.is_empty():
        return {"teams": 0}
    day = t["date"].max()
    now = t.filter(pl.col("date") == day)
    nums = _numbers(season)
    teams: dict[str, list[dict]] = {}
    for r in now.sort("team", "last").iter_rows(named=True):
        rep = None
        if r["report_status"]:
            rep = {"status": r["report_status"], "injury": r["report_injury"], "timeline": r["report_timeline"],
                   "date": r["report_date"], "url": r["report_url"]}
        teams.setdefault(r["team"], []).append({
            "id": r["player_id"], "name": f"{r['first'] or ''} {r['last'] or ''}".strip(), "pos": r["pos"],
            "number": nums.get(r["player_id"]), "status": r["status"], "missed": r["games_missed"], "last": r["last_played"],
            "report": rep})
    since = (Date.fromisoformat(day) - timedelta(days=30)).isoformat()
    moves = pl.read_parquet(availability.moves_path(season)).filter(pl.col("date") >= since) if availability.moves_path(season).exists() else None
    club_moves: dict[str, list[dict]] = {}
    for m in (moves.sort("date", descending=True).iter_rows(named=True) if moves is not None else []):
        item = {"date": m["date"], "id": m["player_id"], "name": f"{m['first'] or ''} {m['last'] or ''}".strip(),
                "from": m["from_team"], "to": m["to_team"]}
        for club in {m["from_team"], m["to_team"]} - {None}:
            club_moves.setdefault(club, []).append(item)
    (SITE_DATA / "roster.json").write_text(json.dumps({"date": day, "order": ORDER, "teams": teams, "moves": club_moves},
                                                      separators=(",", ":")))
    return {"teams": len(teams), "players": now.height}
