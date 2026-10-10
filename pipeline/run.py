"""Command line entry point: python -m pipeline.run <step> [options]."""
import argparse
import json

from pipeline.config import CURRENT_SEASON


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
    from pipeline.ingest import edge, moneypuck, nhl
    from pipeline.metrics import player_game, team_game, units, xg_model

    out = {"ingest": nhl.ingest_season(season, refresh_schedule=season == CURRENT_SEASON)}
    moneypuck.download_shots(season)  # cross-check only; the export's match-rate check reads it
    out["build"] = games.build_season(season)
    out["moneypuck_match_pct"] = join_xg.run(season)["match_rate_pct"]  # cross-check table; export reads it
    shot_features.build(season)
    out["shots_rated"] = xg_model.score_season(pickle.loads(xg_model.MODEL_PATH.read_bytes()), season)
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)
    player_game.build(season)
    out["edge"] = edge.update(season)  # tracking numbers for players who played since the last fetch
    out["export"] = site_json.run(season)
    return out


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
    team_game.build(season)
    units.game_units(season)
    units.depth_table(season)
    player_game.build(season)
    out["export"] = site_json.run(season)
    return out


if __name__ == "__main__":
    main()
