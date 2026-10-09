"""Command line entry point: python -m pipeline.run <step> [options]."""
import argparse
import json

from pipeline.config import CURRENT_SEASON


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["ingest", "build", "validate", "export"])
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


if __name__ == "__main__":
    main()
