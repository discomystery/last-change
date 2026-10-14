"""Command line entry point: python -m pipeline.run <step> [options]."""
import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

from pipeline.config import CURRENT_SEASON, FINAL_STATES, FULL_SEASONS, RAW, SITE_DATA, TABLES


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["ingest", "build", "validate", "export", "update", "replays", "publish", "quick", "mode"])
    p.add_argument("--season", type=int, default=CURRENT_SEASON)
    p.add_argument("--limit", type=int)
    p.add_argument("--requested", default="full", help="mode: what the workflow was asked for (full or quick)")
    a = p.parse_args()
    if a.step == "ingest":
        from pipeline.ingest import nhl
        print(json.dumps(nhl.ingest_season(a.season, refresh_schedule=a.season == CURRENT_SEASON, limit=a.limit)))
    elif a.step == "build":
        from pipeline.build import games
        print(json.dumps(games.build_season(a.season)))
    elif a.step == "validate":
        from pipeline.build import validate
        print(json.dumps(validate.run(a.season), indent=1))
    elif a.step == "export":
        from pipeline.export import site_json
        print(json.dumps(site_json.run(a.season)))
    elif a.step == "replays":
        from pipeline.metrics import replays
        print(json.dumps(replays.build(a.season)))
    elif a.step == "update":
        print(json.dumps(update(a.season)))
    elif a.step == "publish":
        print(json.dumps(publish(a.season)))
    elif a.step == "quick":
        print(json.dumps(quick(a.season)))
    elif a.step == "mode":
        print(mode(a.requested))


MONEYPUCK_CHECK = os.environ.get("LC_MONEYPUCK") == "1"  # set to download MoneyPuck's shots for a private comparison


def update(season: int) -> dict:
    """Everything a new game needs, in order: fetch, build tables, rate shots, team and unit tables, site JSON."""

    from pipeline.build import games, join_xg, shot_features
    from pipeline.export import site_json
    from pipeline.ingest import careers, coaches, edge, game_stats, history, moneypuck, names, nhl, nhl_previews, rosters
    from pipeline.metrics import player_game, team_game, units, xg_model

    out = {"ingest": nhl.ingest_season(season, refresh_schedule=season == CURRENT_SEASON)}
    if season == CURRENT_SEASON:
        out["wikidata"] = names.fetch_wikidata()  # accented spellings of players' names, refreshed weekly
        out["rosters"] = rosters.snapshot(season)  # who each club lists today, kept as a dated history
    if MONEYPUCK_CHECK:  # private cross-check only: nothing on the site reads MoneyPuck's shots
        moneypuck.download_shots(season)
    out["shift_checks"] = check_saved_shifts(season)
    out["build"] = games.build_season(season)
    out["names"] = respell(season)
    if season == CURRENT_SEASON:
        out["coaches"] = coaches.update(season)  # who was behind each bench, for matchup calls
        from pipeline.ingest import standings_daily
        out["standings_days"] = standings_daily.update(season)  # the table after each game day, for the playoff race
        out["game_stats"] = game_stats.update(season)  # the league's own power-play line, for post-game pages
        out["availability"] = availability_update(season)
    if MONEYPUCK_CHECK:
        out["moneypuck_match_pct"] = join_xg.run(season)["match_rate_pct"]  # shots_xg.parquet, for xg_model.evaluate
    shot_features.build(season)
    out["shots_rated"] = xg_model.score_season(xg_model.saved(), season)
    refresh_saved(season)
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)
    player_game.build(season)
    out["edge"] = edge.update(season)  # tracking numbers for players who played since the last fetch
    if season == CURRENT_SEASON:
        out["careers"] = careers.update(season)  # NHL career history for the page intros, a capped batch per run
        out["history"] = history.update()  # team standings and playoff brackets since 1997-98, fetched once
        out["nhl_previews"] = nhl_previews.update(season)  # NHL.com's projected starting goalies for today's games
    out["export"] = site_json.run(season)
    from pipeline.metrics import drift, rapm
    rec = drift.check(season)  # has the league changed how it records plays? the workflow opens an issue if so
    drift.save(rec)
    out["recording_check"] = rec["status"]
    out["rapm_cache_pruned"] = rapm.prune_cache()
    _save_state(season)
    return out


# Game-night fast lane (user decision 2026-10-11, thread "Why the site rebuilds"). A watcher workflow follows each
# night's games and asks for quick runs: before puck drop to pick up scratches, NHL.com's projected starters and the
# league's injury news for tonight's previews, and after each final to put its post-game page up within minutes. The
# full update (every rating, team and player page) still runs on its schedule, after merges, and once the night's last
# game is done. All runs share one queue, where a newer waiting run replaces an older one, so a quick request turns
# itself into a full update whenever one is owed (see `mode`): a merge or a scheduled update is never lost.

STATE = RAW / "run_state.json"  # when the last full update ran and on which commit; saved on the data branch
FULL_SLOTS = ((10, 0), (16, 0), (20, 30))  # the nightly workflow's scheduled full updates, UTC


def _commit() -> str | None:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _save_state(season: int) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"full_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "full_sha": _commit(), "season": season}))


def mode(requested: str, now: datetime | None = None) -> str:
    """"quick" only when a quick run was asked for and no full update is owed: the code hasn't changed since the last
    full update and no scheduled full update has come due since it ran."""
    if requested != "quick" or not STATE.exists():
        return "full"
    state = json.loads(STATE.read_text())
    sha = _commit()
    if sha and state.get("full_sha") != sha:
        return "full"
    now = now or datetime.now(timezone.utc)
    slots = [datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc) for d in (now - timedelta(days=1), now) for h, m in FULL_SLOTS]
    due = max(t for t in slots if t <= now)
    return "full" if datetime.fromisoformat(state["full_at"]) < due else "quick"


def quick(season: int = CURRENT_SEASON) -> dict:
    """The game-night pass: only what tonight's previews and just-finished games need, in a minute or two. Fetches new
    finals (and shift charts the NHL was still filling in), rebuilds the game tables, refreshes rosters, scratches,
    injury news and projected starters, rewrites previews for games starting in the next 18 hours, and writes post-game
    pages, report cards and Swing for finished games that don't have them yet. Ratings, team and player pages wait for
    the next full update."""
    from pipeline.build import games, shot_features
    from pipeline.export import flow, game_scores, jobs, pending, previews, recaps, roster, site_json, stories, track
    from pipeline.ingest import game_rosters, game_stats, injuries, nhl, nhl_previews, rosters, standings_daily, status_report, transactions
    from pipeline.metrics import availability, player_game, team_game, units, xg_model
    import polars as pl

    out = {"ingest": nhl.ingest_season(season, refresh_schedule=True)}
    out["build"] = games.build_season(season)
    out["names"] = respell(season)
    out["game_stats"] = game_stats.update(season)  # the league's own power-play line, for post-game pages
    out["standings_days"] = standings_daily.update(season)  # the table after tonight's games, for the playoff race
    shot_features.build(season)
    out["shots_rated"] = xg_model.score_season(xg_model.saved(), season)
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)  # team numbers join it: without it tonight's finals have no team rows to grade
    player_game.build(season)
    out["rosters"] = rosters.snapshot(season)
    out["game_rosters"] = game_rosters.update(season)  # tonight's scratches, posted before puck drop
    try:  # only today's injury news: earlier days were read by the full update
        status_report.fetch(season, status_report.today())
    except Exception as e:
        print(f"status report unavailable: {e!r}")
    status_report.build(season)
    out["transactions"] = transactions.update(season)  # today's roster moves (keeps the saved copy if unreachable)
    out["injuries"] = injuries.update(season)  # ESPN's injury list as it stands now
    out["availability"] = availability.build(season)
    out["nhl_previews"] = nhl_previews.update(season)
    out["teams_and_schedule"] = site_json.export_teams_and_schedule(season)  # standings, and which games are final
    roster.run(season)
    out["previews"] = previews.run(within=timedelta(hours=18))
    pending.games.cache_clear()
    since = (datetime.now(timezone.utc).date() - timedelta(days=pending.DAYS)).isoformat()
    built = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("state").is_in(list(FINAL_STATES)) & (pl.col("date") >= since))
    recent = set(built["game_id"].to_list())
    todo = {g for g in recent - pending.games(season)
            if not (SITE_DATA / "jobs" / f"{g}.json").exists() or not (SITE_DATA / "scores" / f"{g}.json").exists()}
    out["recaps"] = recaps.run(season)
    out["jobs"] = jobs.run(season, only=todo)
    out["scores"] = game_scores.run(season, only=todo)
    out["flow"] = flow.run(season)
    out["stories"] = stories.run_recaps(season)
    track.run(season)
    out["waiting_on_shift_charts"] = sorted(pending.games(season))
    return out


def respell(season: int) -> dict:
    """Accented player names (ingest/names.py) in every saved season's player and roster tables."""
    from pipeline.ingest import names

    return names.respell(sorted(int(p.name) for p in TABLES.iterdir() if p.name.isdigit()), again=(season,))


def availability_update(season: int) -> dict:
    """Game-day rosters and scratches, NHL.com injury news, then who was available to each club each day. Last
    season's game rosters and reports are filled in once, so absences can be studied over a full season."""
    from pipeline.ingest import game_rosters, injuries, status_report, transactions
    from pipeline.metrics import availability

    out = {}
    for s in (season - 1, season):
        if s == season or not game_rosters.path(s).exists():
            out[f"game_rosters_{s}"] = game_rosters.update(s)
        if s == season or not status_report.path(s).exists():
            out[f"status_{s}"] = status_report.update(s)
    out["transactions"] = transactions.update(season)  # injured reserve, minors, suspensions: all 32 clubs
    out["injuries"] = injuries.update(season)  # ESPN's injury list as it stands today, saved day by day
    out["table"] = availability.build(season)
    return out


TABLE_RULES = 5  # bump when a change to how these tables are built must also reach seasons saved earlier
# 2 (2026-10-10): penalty shots are no longer counted as 5-on-5 or on-ice play
# 3 (2026-10-11): a shot that hits the shooter's own teammate is no longer a block for the other team
# 4 (2026-10-11): expected-goals model v2; earlier seasons' shots are rebuilt and re-rated
# 5 (2026-10-14): a turnover ends the other side's off-turnover window; goalie breakdowns use the team rule; depth tables


def check_saved_shifts(season: int) -> dict:
    """Once per season (2026-10-11 data audit): fetch the HTML shift reports for saved games whose API shift chart
    disagrees with the play-by-play or boxscore. An earlier season with new reports gets its game tables rebuilt and
    shots re-rated here, and its team, unit and player tables rebuilt by refresh_saved."""

    from pipeline.build import games, shot_features
    from pipeline.ingest import nhl
    from pipeline.metrics import xg_model

    out = {}
    for s in [*FULL_SEASONS, season]:
        fetched = nhl.check_saved(s)
        out[s] = len(fetched)
        if fetched and s != season:
            games.build_season(s)
            shot_features.build(s)
            xg_model.score_season(xg_model.saved(), s)
            (TABLES / str(s) / "rules.json").unlink(missing_ok=True)
    return out


def refresh_saved(season: int) -> list[int]:
    """Rebuild the team, unit and player tables of earlier seasons once after a rule change, so blends, priors and
    league baselines follow the same rules as the current season. A small marker file records the rules each was built with."""
    from pipeline.build import shot_features
    from pipeline.metrics import player_game, team_game, units, xg_model

    def stale(s: int) -> bool:
        d = TABLES / str(s)
        marker = d / "rules.json"
        return (d / "events.parquet").exists() and not (marker.exists() and json.loads(marker.read_text()).get("rules", 0) >= TABLE_RULES)

    done = [s for s in range(FULL_SEASONS[0], season) if stale(s)]
    for s in done:  # shots first (a rule may change the shot model), then every team table: player tables read league baselines pooled over all of them
        shot_features.build(s)
        xg_model.score_season(xg_model.saved(), s)
    for s in done:
        team_game.build(s)
        units.game_units(s)
        units.depth_table(s)
    for s in done:
        player_game.build(s)
        (TABLES / str(s) / "rules.json").write_text(json.dumps({"rules": TABLE_RULES}))
    return done


def publish(season: int) -> dict:
    """Recompute every table and number from data already saved, without fetching anything new: for publishing
    changes to the site or the maths between nightly runs. Run with LC_OFFLINE=1 so nothing can reach the network."""

    from pipeline.build import games, shot_features
    from pipeline.export import site_json
    from pipeline.metrics import player_game, team_game, units, xg_model

    out = {"build": games.build_season(season)}
    out["names"] = respell(season)
    shot_features.build(season)
    out["shots_rated"] = xg_model.score_season(xg_model.saved(), season)
    refresh_saved(season)
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)
    player_game.build(season)
    if season == CURRENT_SEASON:
        from pipeline.ingest import injuries, status_report, transactions
        from pipeline.metrics import availability
        status_report.build(season)  # re-read the saved reports with the current rules
        transactions.build(season)  # and the saved roster moves
        injuries.build(season)  # and ESPN's saved injury lists
        out["availability"] = availability.build(season)
    out["export"] = site_json.run(season)
    return out


if __name__ == "__main__":
    main()
