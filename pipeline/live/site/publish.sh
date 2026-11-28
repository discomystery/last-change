#!/usr/bin/env bash
# Put the live feed's files on this repository's `feed` branch (one fresh commit, so the history never grows). Pages
# read the feed from raw.githubusercontent.com, so deploy.yml is no longer started here: each start cost a billed
# runner minute every 2 minutes, and nothing reads the Pages copy. Called by pipeline/live/loop.py each time a file changed. Usage: publish.sh FEED_DIR
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
rm -rf "$tmp"
