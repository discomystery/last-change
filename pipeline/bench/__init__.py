"""The test bench (plan item D, Sora approved 2026-10-31, thread "Improve game previews and automate tuning").

`calls` replays every graded preview call kind over past seasons and this one, with what was known the morning of
each game, through the site's own claim and grading code (export/previews.py, export/recaps.py), and sets each
beside a "by chance" rate: the same check made for every team or game, with no reason behind it. The gap between the
two is what a call actually knows.

`samples` rebuilds the previews and post-game pages of a fixed set of games and writes out every sentence a reader
sees, so a pull request can show its wording changes side by side (plan item E).

`compare` turns two runs of either into a Markdown report, posted on pull requests by .github/workflows/bench.yml.

    uv run --project pipeline python -m pipeline.bench calls --out bench.json
    uv run --project pipeline python -m pipeline.bench samples --out samples.json
    uv run --project pipeline python -m pipeline.bench compare base.json head.json

Nothing here writes to the site or the data folders.
"""
