#!/bin/sh
# Publish the local data cache to the orphan `data` branch as a single fresh commit (history is not kept, so the
# branch does not grow with every nightly run; unchanged files are not re-uploaded).
#
# Includes: data/raw (NHL responses), data/tables, data/models, data/README.md, and a copy of site/public/data as
# site_data/ (the preview snapshots in site_data/previews must survive between runs: they are frozen at puck drop).
# Leaves out MoneyPuck's own files and the tables built from them: their data is not ours to republish.
#
# Run from the repo root. Usage: pipeline/save_data.sh "commit message"
set -eu
msg="${1:-Data cache}"
stage="$(mktemp -d)"
index="$(mktemp)"
trap 'rm -rf "$stage" "$index"' EXIT

mkdir -p "$stage/raw" "$stage/tables"
for d in data/raw/*; do
  [ "$(basename "$d")" = moneypuck ] || cp -R "$d" "$stage/raw/"
done
cp -R data/tables/. "$stage/tables/"
find "$stage/tables" -name 'shots_xg.parquet' -delete
cp -R data/models "$stage/models"
[ -f data/README.md ] && cp data/README.md "$stage/README.md"
cp -R site/public/data "$stage/site_data"

# Build the commit in this repository (so the push only sends what changed), without touching the working tree.
export GIT_INDEX_FILE="$index"
rm -f "$index"
git --work-tree="$stage" add -A
tree="$(git write-tree)"
commit="$(git -c user.name="${GIT_AUTHOR_NAME:-last-change-bot}" -c user.email="${GIT_AUTHOR_EMAIL:-last-change-bot@users.noreply.github.com}" commit-tree "$tree" -m "$msg")"
unset GIT_INDEX_FILE
git fetch --depth=1 origin data >/dev/null 2>&1 || true  # lets the push skip files the branch already has
git push -f origin "$commit:refs/heads/data"
echo "data branch -> $commit"
