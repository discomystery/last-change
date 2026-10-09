#!/bin/sh
# Download and process past seasons. Safe to re-run: finished games are never fetched twice.
cd "$(dirname "$0")/.." || exit 1
UV=/Users/katherine/Library/Python/3.9/bin/uv
rm -f data/backfill.done
for y in 2025 2024 2023; do
  $UV run --project pipeline python -m pipeline.run ingest --season $y &&
  $UV run --project pipeline python -c "from pipeline.ingest import moneypuck as mp; print('moneypuck', $y, mp.download_shots($y))" &&
  $UV run --project pipeline python -m pipeline.run build --season $y &&
  $UV run --project pipeline python -c "from pipeline.build import join_xg; import json; print(json.dumps(join_xg.run($y)))" &&
  $UV run --project pipeline python -m pipeline.run validate --season $y || { echo "FAILED on $y"; echo failed > data/backfill.done; exit 1; }
done
echo ok > data/backfill.done
