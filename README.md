# Data cache for last-change

Working data for the hockey site in the `main` branch. This branch has no code and shares no history with `main`.

- `raw/{season}/{pbp,box,shifts,shifts_html}/` and `raw/{season}/schedule.json.gz`: gzipped responses from the NHL's public game feeds, one file per game, untouched. Seasons are named by start year (2026 = 2026-27).
- `raw/players/`: small daily cache of which club each checked player is listed with.
- `tables/{season}/*.parquet`: tables built from the raw files by `pipeline/` on `main`.
- `models/xg_v1.pkl`: the site's own expected-goals model.
- `site_data/`: the JSON the website reads, as of the last run. `site_data/previews/` holds each game's preview calls, frozen at puck drop; the nightly run restores them before updating so frozen calls are never rewritten.

This branch is replaced by a single fresh commit on every nightly run (`pipeline/save_data.sh` on `main`), so it carries no history.

Not here on purpose: MoneyPuck's shot files and the `shots_xg.parquet` tables built from them (their data is for non-commercial use and is not ours to republish; the pipeline downloads them itself from moneypuck.com when it needs the cross-check), and hockeyR's file (no stated licence).

To use: place `raw/`, `tables/` and `models/` inside a `data/` folder at the root of a `main` checkout.

Game data: NHL. Not affiliated with the NHL or any of its teams.
