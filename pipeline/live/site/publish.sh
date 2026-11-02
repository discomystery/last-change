#!/usr/bin/env bash
# Put the live feed's files on this repository's `feed` branch (one fresh commit, so the history never grows), then
# start deploy.yml to publish them. Called by pipeline/live/loop.py each time a file changed. Usage: publish.sh FEED_DIR
set -euo pipefail
feed="$(cd "$1" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"
cp "$feed"/*.json "$tmp"/
cp "$here/index.html" "$tmp"/
cd "$tmp"
git init -q -b feed
git add .
git -c user.name="live feed" -c user.email="live@users.noreply.github.com" commit -qm "Live feed $(date -u +%H:%M)"
git push -qf "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" feed
curl -fsS -X POST -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/deploy.yml/dispatches" -d '{"ref":"main"}'
rm -rf "$tmp"
