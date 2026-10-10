"""Previews for games that were played without one, rebuilt from what was known before each game.

Live previews are frozen at puck drop. Games played before snapshots began (and any game a run missed) have none, so
for each such game day this cuts the season's tables back to the games finished on earlier days, recomputes the
fingerprints, lines, goalies and goal sources from that cut in a separate process (the pipeline reads its folders at
import time), and writes the same snapshot a live run would have written that morning, marked `rebuilt`.

Nothing from the game day itself or later is used, with one exception: whether a missing regular is still listed with
his club (lines notes) comes from today's league listing, which keeps no history. Earlier seasons, the arena factors,
score-and-venue weights and the xG model are all built from 2023-24 to 2025-26 only.

Rebuilt calls are graded on the post-game page like any other but kept out of the season tally, because nobody could
read them before the game.
"""
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from pipeline.config import CURRENT_SEASON, DATA, SITE_DATA, TABLES
from pipeline.export import previews


def cut_tables(season: int, day: str, root: Path) -> int:
    """A data folder under `root` whose `season` tables hold only games played before `day`; everything else is linked."""
    for name in ("raw", "models"):
        (root / name).symlink_to(DATA / name)
    (root / "tables").mkdir()
    for d in TABLES.iterdir():
        if d.name != str(season):
            (root / "tables" / d.name).symlink_to(d)
    src, dst = TABLES / str(season), root / "tables" / str(season)
    dst.mkdir()
    games = pl.read_parquet(src / "games.parquet").filter(pl.col("date") < day)
    keep = games["game_id"].to_list()
    for f in src.glob("*.parquet"):
        t = pl.read_parquet(f)
        if "game_id" not in t.columns:
            raise ValueError(f"{f.name} has no game_id, so it can't be cut back to a date")
        t.filter(pl.col("game_id").is_in(keep)).write_parquet(dst / f.name)
    return games.height


def _export_here(season: int) -> None:
    """Runs in the child process, with the folders pointed at the cut copy."""
    from pipeline.export import site_json

    site_json.export_fingerprints(season, idle=True)
    if pl.read_parquet(TABLES / str(season) / "games.parquet").height:
        site_json.export_lines(season)
        site_json.export_goalies(season)
        site_json.export_goal_sources(season)
        return
    # Opening day: no lines, goalie numbers or goal sources yet this season, so the page shows those sections empty.
    shells = {"lines": {"window": 10, "teams": {}}, "goalies": {"min_starts": {}, "league": [], "teams": {}},
              "goal_sources": {"league_sources": {}, "teams": {}}}
    for name, body in shells.items():
        (SITE_DATA / f"{name}.json").write_text(json.dumps({"season": season, **body}))


def site_as_of(season: int, day: str) -> tuple[dict, int]:
    """Fingerprints, lines, goalies and goal sources as they stood on the morning of `day`, and the games behind them."""
    with tempfile.TemporaryDirectory(prefix=f"pregame-{day}-") as tmp:
        root = Path(tmp)
        (root / "data").mkdir()
        (root / "site").mkdir()
        n = cut_tables(season, day, root / "data")
        env = {**os.environ, "LAST_CHANGE_DATA": str(root / "data"), "LAST_CHANGE_SITE_DATA": str(root / "site")}
        subprocess.run([sys.executable, "-m", "pipeline.export.pregame", "--here", str(season)], env=env, check=True,
                       cwd=Path(__file__).resolve().parents[2], stdout=subprocess.DEVNULL)
        site = previews.site_files(root / "site")
    # The files carry the time they were computed; what matters on the page is the last day of games behind them.
    site["fingerprints"]["generated_at"] = f"{day}T00:00:00+00:00"
    return site, n


def run(season: int = CURRENT_SEASON, now: datetime | None = None) -> dict:
    """Rebuild a preview for every finished game that has none. Existing snapshots, live or rebuilt, are never touched."""
    now = now or datetime.now(timezone.utc)
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    out_dir = SITE_DATA / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = defaultdict(list)
    for g in sched:
        if g["final"] and not (out_dir / f"{g['id']}.json").exists():
            todo[g["date"]].append(g)
    written = 0
    chances = previews._chances([g for day in todo.values() for g in day])
    for day in sorted(todo):
        site, n = site_as_of(season, day)
        through = max((g["date"] for g in sched if g["final"] and g["date"] < day), default=None)
        for g in todo[day]:
            if g["away"] not in site["fingerprints"]["teams"] or g["home"] not in site["fingerprints"]["teams"]:
                continue
            snap = previews.snapshot(g, site, places, sched, now, chances.get(g["id"]), rebuilt=True, games_before=n, data_through=through)
            (out_dir / f"{g['id']}.json").write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
            written += 1
    previews.write_index(out_dir)
    return {"rebuilt": written, "days": len(todo)}


if __name__ == "__main__":
    if sys.argv[1:2] == ["--here"]:
        _export_here(int(sys.argv[2]))
    else:
        print(json.dumps(run(int(sys.argv[1]) if len(sys.argv) > 1 else CURRENT_SEASON)))
