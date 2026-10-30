# Write-and-compare voice loop

A way to keep improving how the generated preview and post-game text reads. A Claude session sees only a game's facts,
writes the story it thinks is best, and lines it up against what the site wrote. Differences that keep showing up turn
into generator changes. Started 2026-10-30 (pilot run `runs/2026-10-30-pilot/`).

## A round, step by step

Run each command from the repository root, with site data extracted from the data branch. For example:
`LAST_CHANGE_SITE_DATA=/path/to/site_data uv run --project pipeline python -m pipeline.voice ...`

1. **Sample.** Run `python -m pipeline.voice sample --run {name}`. It picks about 12 varied finished games (one each of
   shootout, overtime, comeback, shutout, rout, one-goal, hat trick, high- and low-scoring) and the next 3 previews.
   It writes each game's `{gid}.packet.json` (the facts) and `{gid}.generator.json` (the site's text).
   To choose games yourself, use `packet --games 2026020127,2026020211:p` instead (`:p` marks a preview).
2. **Write, blind.** Read only the packets. Do **not** open `generator.json` until every draft is written. Save each
   draft as `{gid}.claude.md`:
   - a `# Headline` line, then paragraphs, then `- Tag: text` items
   - previews may add calls in the same way
   - use the house voice and rules in CLAUDE.md: plural team verbs, no betting words, never "deserve", never why a
     player was out, and no win chance in previews.
   - Notes in a packet's `notes` are facts the generator found. Use them in your own words or leave them out.
3. **Guard.** Run `python -m pipeline.voice check --run {name}`. It lists every number and name in a draft that the
   packet does not contain. Fix the draft. A value that the packet backs in another form can be allowed with
   `<!-- ok: value -->`. The guard checks facts, not judgements, so reread every "because of him" claim against the
   numbers yourself. The pilot caught one overstatement this way.
4. **Compare and log.** Open `report.md` and `report.html` (`report --run {name}`). They show the site's text and the
   draft side by side, with automatic measures: length, sentence shape, repeated phrases, stock wording, and house-rule
   slips. Add one line to `findings.jsonl` per difference per game:
   - fields: `run`, `game_id`, `page` (recap/preview), `category` (lead, order, emphasis, omission, overstatement,
     repetition, phrasing, tone, structure, accuracy), `key`, `note`, `generator_text`, `claude_text`
   - reuse an existing `key` when it is the same difference.
5. **Patterns.** Run `python -m pipeline.voice patterns`. A key seen in 3+ games (or a quarter of a run's games) is a
   pattern. A one-off stays logged and can grow into a pattern in a later round.
6. **Fix.** Change the generator (`export/stories.py`, `timeline.py`, `previews.py`, `outlook.py`) for each pattern.
   First check CLAUDE.md: if a fix would undo one of Sora's decisions, record the pattern as `kept` or `proposed` in
   `patterns.json` and ask her instead. Run `after --run {name}` to rewrite the run's games with the changed generator
   (`{gid}.after.json`, site data untouched). Check that the change did what you meant, then run pytest.
7. **Record.** In `patterns.json`, set each key's `status` (open, fixed, partly, kept, proposed), `since` (run) and
   `fix` (PR), with a one-line `note`. A later run that logs a `fixed` key again is a regression; `patterns` shows it.
8. **Show Sora.** Run `python -m pipeline.voice.page voice/runs/{name} page.html` and publish the page as an Artifact.
   It shows the site's text, the draft, and the site after the fixes, with the pattern table. Open a PR with the
   generator changes and the run folder.

Previews rebuild only when `previews.RULES_VERSION` goes up (about 60 s per game day), so bump it only when a preview
change should reach past games.

## Files

- `runs/{name}/`: packets, drafts, the site's text as it was, the text after, and the reports
- `findings.jsonl`: every logged difference, across all runs
- `patterns.json`: the status of each key
- `pipeline/voice/`: `packet.py` (facts), `compare.py` (guard, measures, report, patterns), `page.py` (the page for Sora)
