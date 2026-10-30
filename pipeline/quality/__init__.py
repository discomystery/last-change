"""The self-check (Sora, 2026-10-30, thread "Improve game previews and automate tuning": one system for previews and
post-game pages that checks itself against our own data and, regularly, against outside sources).

`python -m pipeline.quality` runs every check after an update and saves one report a day; `--alarm` prints the
problems (nothing when all is well), which the nightly workflow turns into a GitHub issue, as it does for the
recording-change and roster alarms. A check never changes a page or stops an update; the one thing it does is ask
for games whose official totals moved to be fetched again on the next update (official.py).

Checks (each in its own module, listed in `run.CHECKS`):
  text       every sentence on preview and post-game pages: definite slips (export/textcheck.py rules) are problems,
             style counts (repeats, alike openers) are reported for the trend
  hindsight  yesterday's previews against what happened: projected starting goalies, players the preview had out
             who dressed after all
  official   our own totals from the play-by-play against the NHL's official season reports (team goals and
             records, every skater's goals and assists), for clubs and players whose games played agree
  outside    an outside source: ESPN's scores for the last few game days and its standings, against our post-game
             pages and teams.json (skipped offline)

Levels: "problem" (raises the alarm), "watch" (listed, no alarm), "ok". Reports: data/raw/quality/{date}.json (the
last 60 kept, so trends can be read back) and data/raw/quality/latest.json.
"""
