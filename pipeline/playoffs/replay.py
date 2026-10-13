"""Replay a past spring's playoffs as the site would have shown them (the playoff test build).

    uv run --project pipeline python -m pipeline.playoffs.replay [--season 2025] [--stage all|regular|tables|previews|postgame|series]

Everything goes to its own folders, never the live ones:
  data/playoffs/{season}/data   a data folder: last season's tables copied (and rebuilt with playoff games), the rest linked
  data/playoffs/{season}/site   the site data the test build reads (site/public/data in the test site)

Stages (each runs in a child process, because the pipeline reads its folders and season from the environment at import):
  regular   the regular season as it ended: team list and standings, the schedule with the playoff games, fingerprints,
            lines, goalies, goal sources and season trend, from the season's tables cut back to the first playoff day
  tables    the per-game tables (player_game, game_units) rebuilt with the playoff games in them
  previews  each playoff game's preview, from what was known the morning of the game (tables cut to earlier days)
  postgame  post-game pages for every playoff game: graded calls, report cards, Swing, flow, story
            then the bracket and one file per series (pipeline/playoffs/pages.py)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "data"


def folders(season: int) -> tuple[Path, Path]:
    base = LIVE / "playoffs" / str(season)
    return base / "data", base / "site"


def env(season: int, data: Path, site: Path, types: str = "2,3") -> dict:
    return {**os.environ, "LC_SEASON": str(season), "LC_OFFLINE": "1", "LC_GAME_TYPES": types,
            "LAST_CHANGE_DATA": str(data), "LAST_CHANGE_SITE_DATA": str(site)}


def child(season: int, stage: str, data: Path, site: Path, types: str = "2,3", *extra: str) -> None:
    subprocess.run([sys.executable, "-m", "pipeline.playoffs.replay", "--here", stage, "--season", str(season), *extra],
                   env=env(season, data, site, types), cwd=ROOT, check=True)


def prepare(season: int) -> tuple[Path, Path]:
    """A fresh data folder: the replayed season's tables copied (they get rebuilt), everything else linked."""
    data, site = folders(season)
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    site.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "models"):
        (data / name).symlink_to(LIVE / name)
    (data / "tables").mkdir()
    for d in (LIVE / "tables").iterdir():
        if d.name == str(season):
            shutil.copytree(d, data / "tables" / d.name)
        else:
            (data / "tables" / d.name).symlink_to(d)
    return data, site


def first_playoff_day(data: Path, season: int) -> str:
    import polars as pl
    g = pl.read_parquet(data / "tables" / str(season) / "games.parquet")
    return g.filter(pl.col("game_type") == 3)["date"].min()


def cut(season: int, data: Path, day: str, into: Path) -> None:
    """A copy of `data` whose replayed season holds only games before `day` (pregame.cut_tables, pointed at `data`)."""
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    subprocess.run([sys.executable, "-c", f"from pathlib import Path; from pipeline.export import pregame; "
                    f"pregame.cut_tables({season}, {day!r}, Path({str(into)!r}))"],
                   env={**os.environ, "LAST_CHANGE_DATA": str(data)}, cwd=ROOT, check=True)


# ---------------------------------------------------------------- stages, run inside the child process


def teams_json(season: int) -> list[dict]:
    """The 32 clubs with their final regular-season records. Names, places and divisions come from the live team
    list (the same 32 clubs); records from the NHL's final standings saved by ingest/history.py."""
    from pipeline.config import RAW, SITE_DATA
    live = json.loads((LIVE / "site_data" / "teams.json").read_text())
    final = {r["teamAbbrev"]["default"]: r for r in json.loads((RAW / "history" / "standings" / f"{season + 1}.json").read_text())}
    out = []
    for t in live:
        r = final.get(t["abbr"]) or {}
        out.append({**t, "w": r.get("wins", 0), "l": r.get("losses", 0), "otl": r.get("otLosses", 0), "gp": r.get("gamesPlayed", 0),
                    "pts": r.get("points", 0), "coach": None, "coach_since": None})
    for div in {t["division"] for t in out}:
        for i, t in enumerate(sorted((t for t in out if t["division"] == div), key=lambda t: (-t["pts"], t["gp"]))):
            t["div_rank"] = i + 1
    (SITE_DATA / "teams.json").write_text(json.dumps(out, separators=(",", ":")))
    return out


def schedule_json(season: int, teams: list[dict]) -> int:
    """Every game of the season, regular season and playoffs; playoff games carry their series, game number and the
    series score before them."""
    import polars as pl
    from pipeline.config import SITE_DATA, TABLES
    from pipeline.playoffs import series
    conf = {t["abbr"]: t["conference"] for t in teams}
    info = {}
    for s in series.build(season, conf):
        for g in s["games"]:
            top_home = g["home"] == s["top"]
            info[g["id"]] = {"type": 3, "round": s["round"], "series": s["id"], "game": g["game"],
                             "sb": g["before"] if top_home else g["before"][::-1], "ot": g.get("ot", 0)}
    games = pl.read_parquet(TABLES / str(season) / "games.parquet").sort("game_id")
    out = []
    for g in games.iter_rows(named=True):
        if g["game_type"] not in (2, 3):
            continue
        row = {"id": g["game_id"], "start": g["start_utc"], "date": g["date"], "home": g["home"], "away": g["away"], "venue": g["venue"],
               "final": g["state"] in ("OFF", "FINAL"), "hs": g["home_score"], "as": g["away_score"], "end": g["last_period"]}
        row.update(info.get(g["game_id"], {}))
        out.append(row)
    (SITE_DATA / "schedule.json").write_text(json.dumps({"season": season, "games": out}, separators=(",", ":")))
    return len(out)


def stage_regular(season: int) -> None:
    from pipeline.config import SITE_DATA
    from pipeline.export import site_json, trend
    teams = teams_json(season)
    site_json.export_fingerprints(season)
    site_json.export_lines(season)
    site_json.export_goalies(season)
    site_json.export_goal_sources(season)
    trend.run(season)
    for name in ("arena_factors.json", "xg_grid.json"):  # league-wide reference files: the same for any season
        src = LIVE / "site_data" / name
        if src.exists():
            shutil.copy(src, SITE_DATA / name)
    print(json.dumps({"teams": len(teams)}))


def stage_schedule(season: int) -> None:
    from pipeline.config import SITE_DATA
    teams = json.loads((SITE_DATA / "teams.json").read_text())
    print(json.dumps({"schedule": schedule_json(season, teams)}))


def stage_tables(season: int) -> None:
    from pipeline.metrics import player_game, units
    player_game.build(season)
    units.game_units(season)
    print("tables rebuilt with playoff games")


def stage_previews(season: int, day: str) -> None:
    """Previews for the playoff games on `day`, from tables holding only earlier games (this process's data folder).
    The fingerprints, lines, goalies and goal sources are the regular season's: they do not move in the playoffs."""
    from datetime import datetime
    from pipeline.config import SITE_DATA, TABLES
    from pipeline.export import previews, site_json
    from pipeline.playoffs import context
    # Lines, units and matching habits move with the series (the window now holds the playoff games so far); the
    # fingerprints, goalies and goal sources stay the regular season's.
    from pipeline.metrics import units
    if (TABLES / str(season) / "game_units.parquet").exists():  # cut from the full table already: no need to rebuild it
        units.game_units = lambda _season: None
    site_json.export_lines(season)
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    site = previews.site_files(SITE_DATA)
    todo = [g for g in sched if g.get("type") == 3 and g["date"] == day]
    chances = previews._chances(todo)
    out_dir = SITE_DATA / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    import polars as pl
    n = pl.read_parquet(TABLES / str(season) / "games.parquet").height
    through = max((g["date"] for g in sched if g["final"] and g["date"] < day), default=None)
    for g in todo:
        now = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        snap = previews.snapshot(g, site, places, sched, now, chances.get(g["id"]), rebuilt=True, games_before=n,
                                 data_through=through, methods=previews.METHODS)
        context.add_to_preview(snap, g, sched, season)
        (out_dir / f"{g['id']}.json").write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
    print(json.dumps({"day": day, "previews": len(todo)}))


def stage_postgame(season: int) -> None:
    """Post-game pages for every playoff game: graded calls, report cards, Swing, flow and story. The tables hold the
    whole season (regular season and playoffs); pages are written for the playoff games only (LC_PAGE_TYPES=3)."""
    import polars as pl
    from pipeline.config import SITE_DATA, TABLES
    from pipeline.export import flow, game_scores, jobs, previews, recaps, stories, track
    from pipeline.playoffs import context
    context.dedupe(json.loads((SITE_DATA / "schedule.json").read_text())["games"], SITE_DATA / "previews")
    g = pl.read_parquet(TABLES / str(season) / "games.parquet")
    ids = set(g.filter((pl.col("game_type") == 3) & pl.col("state").is_in(["OFF", "FINAL"]))["game_id"].to_list())
    out = {"index": previews.write_index(SITE_DATA / "previews"), "recaps": recaps.run(season), "jobs": jobs.run(season, only=ids),
           "scores": game_scores.run(season, only=ids), "flow": flow.run(season), "stories": stories.run_recaps(season)}
    track.run(season)
    from pipeline.playoffs import pages
    out["series"] = pages.run(season)
    print(json.dumps(out, default=str))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=2025)
    p.add_argument("--stage", default="all")
    p.add_argument("--only-day", dest="only_day", default=None)
    p.add_argument("--here", default=None)  # internal: run one stage in this process
    p.add_argument("--day", default=None)
    p.add_argument("--workers", type=int, default=3)
    a, rest = p.parse_known_args()
    if a.here == "previews":
        stage_previews(a.season, a.day)
        return
    if a.here:
        {"regular": stage_regular, "schedule": stage_schedule, "tables": stage_tables, "postgame": stage_postgame}[a.here](a.season)
        return
    season = a.season
    data, site = folders(season)
    if a.stage in ("all", "regular"):
        data, site = prepare(season)
        day = first_playoff_day(data, season)
        pre = data.parent / "cut"
        cut(season, data, day, pre)
        child(season, "regular", pre, site)  # 2,3: no playoff games in the cut, but end-of-season rest nights get left out of lines (units.rest_games)
        shutil.rmtree(pre)
        child(season, "schedule", data, site)
    if a.stage in ("all", "tables"):
        child(season, "tables", data, site)
    if a.stage in ("all", "previews"):
        sched = json.loads((site / "schedule.json").read_text())["games"]
        days = sorted({g["date"] for g in sched if g.get("type") == 3})
        if a.only_day:
            days = [a.only_day]

        def one(day: str) -> None:  # each day from its own cut, so days can run side by side
            pre = data.parent / f"cut-{day}"
            cut(season, data, day, pre)
            child(season, "previews", pre, site, "2,3", "--day", day)
            shutil.rmtree(pre)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            list(pool.map(one, days))
    if a.stage in ("all", "postgame"):
        subprocess.run([sys.executable, "-m", "pipeline.playoffs.replay", "--here", "postgame", "--season", str(season)],
                       env={**env(season, data, site), "LC_PAGE_TYPES": "3"}, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
