#!/bin/sh
# Publish the built site (site/dist) to the gh-pages branch as a single fresh commit; GitHub Pages serves it.
# Run from the repo root after `npm --prefix site run build`. Usage: pipeline/deploy_site.sh "commit message"
set -eu
msg="${1:-Deploy site}"
[ -f site/dist/index.html ] || { echo "site/dist is missing: build the site first" >&2; exit 1; }
stage="$(mktemp -d)"
index="$(mktemp)"
trap 'rm -rf "$stage" "$index"' EXIT
cp -R site/dist/. "$stage/"
touch "$stage/.nojekyll"
export GIT_INDEX_FILE="$index"
rm -f "$index"
git --work-tree="$stage" add -A
tree="$(git write-tree)"
commit="$(git -c user.name="${GIT_AUTHOR_NAME:-last-change-bot}" -c user.email="${GIT_AUTHOR_EMAIL:-last-change-bot@users.noreply.github.com}" commit-tree "$tree" -m "$msg")"
unset GIT_INDEX_FILE
git push -f origin "$commit:refs/heads/gh-pages"
echo "gh-pages -> $commit"
