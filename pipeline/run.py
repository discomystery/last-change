"""Command line entry point: python -m pipeline.run <step> [options]."""
import argparse
import json

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, TABLES


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["ingest", "build", "validate", "export", "update", "replays", "publish"])
    p.add_argument("--season", type=int, default=CURRENT_SEASON)
    p.add_argument("--limit", type=int)
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


def update(season: int) -> dict:
    """Everything a new game needs, in order: fetch, build tables, rate shots, team and unit tables, site JSON."""
    import pickle

    from pipeline.build import games, join_xg, shot_features
    from pipeline.export import site_json
    from pipeline.ingest import coaches, edge, moneypuck, nhl, rosters
    from pipeline.metrics import player_game, team_game, units, xg_model

    out = {"ingest": nhl.ingest_season(season, refresh_schedule=season == CURRENT_SEASON)}
    if season == CURRENT_SEASON:
        out["rosters"] = rosters.snapshot(season)  # who each club lists today, kept as a dated history
    moneypuck.download_shots(season)  # cross-check only; the export's match-rate check reads it
    out["build"] = games.build_season(season)
    if season == CURRENT_SEASON:
        out["coaches"] = coaches.update(season)  # who was behind each bench, for matchup calls
        out["availability"] = availability_update(season)
    out["moneypuck_match_pct"] = join_xg.run(season)["match_rate_pct"]  # cross-check table; export reads it
    shot_features.build(season)
    out["shots_rated"] = xg_model.score_season(pickle.loads(xg_model.MODEL_PATH.read_bytes()), season)
    refresh_saved(season)
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)
    player_game.build(season)
    out["edge"] = edge.update(season)  # tracking numbers for players who played since the last fetch
    out["export"] = site_json.run(season)
    return out


def availability_update(season: int) -> dict:
    """Game-day rosters and scratches, NHL.com injury news, then who was available to each club each day. Last
    season's game rosters and reports are filled in once, so absences can be studied over a full season."""
    from pipeline.ingest import game_rosters, status_report
    from pipeline.metrics import availability

    out = {}
    for s in (season - 1, season):
        if s == season or not game_rosters.path(s).exists():
            out[f"game_rosters_{s}"] = game_rosters.update(s)
        if s == season or not status_report.path(s).exists():
            out[f"status_{s}"] = status_report.update(s)
    out["table"] = availability.build(season)
    return out


TABLE_RULES = 2  # bump when a change to how these tables are built must also reach seasons saved earlier
# 2 (2026-10-10): penalty shots are no longer counted as 5-on-5 or on-ice play


def refresh_saved(season: int) -> list[int]:
    """Rebuild the team, unit and player tables of earlier seasons once after a rule change, so blends, priors and
    league baselines follow the same rules as the current season. A small marker file records the rules each was built with."""
    from pipeline.metrics import player_game, team_game, units

    def stale(s: int) -> bool:
        d = TABLES / str(s)
        marker = d / "rules.json"
        return (d / "events.parquet").exists() and not (marker.exists() and json.loads(marker.read_text()).get("rules", 0) >= TABLE_RULES)

    done = [s for s in range(FULL_SEASONS[0], season) if stale(s)]
    for s in done:  # every team table first: player tables read league baselines pooled over all of them
        team_game.build(s)
        units.game_units(s)
    for s in done:
        player_game.build(s)
        (TABLES / str(s) / "rules.json").write_text(json.dumps({"rules": TABLE_RULES}))
    return done


def publish(season: int) -> dict:
    """Recompute every table and number from data already saved, without fetching anything new: for publishing
    changes to the site or the maths between nightly runs. Run with LC_OFFLINE=1 so nothing can reach the network."""
    import pickle

    from pipeline.build import games, shot_features
    from pipeline.export import site_json
    from pipeline.metrics import player_game, team_game, units, xg_model

    out = {"build": games.build_season(season)}
    shot_features.build(season)
    out["shots_rated"] = xg_model.score_season(pickle.loads(xg_model.MODEL_PATH.read_bytes()), season)
    refresh_saved(season)
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)
    player_game.build(season)
    if season == CURRENT_SEASON:
        from pipeline.ingest import status_report
        from pipeline.metrics import availability
        status_report.build(season)  # re-read the saved reports with the current rules
        out["availability"] = availability.build(season)
    out["export"] = site_json.run(season)
    return out


if __name__ == "__main__":
    main()
