# Hockey Matchup Analysis Site

Fan-facing site that explains how NHL teams differ in style and strategy and previews upcoming matchups. The site is for fans of any team: every metric and page must work for all 32 teams, and nothing in the product is Hurricanes-specific.

**Full spec: `private/PROJECT_BRIEF.md`, kept on the user's computer only (gitignored, and scrubbed from the public history on 2026-10-09 because it contains the user's private theories). Never commit it or quote those theories in anything public.** Read the relevant section before building any metric, page, or pipeline step. This file holds only the durable rules.

## Working agreement

- The user does not code and will not. Do all engineering. Never ask them to write, read, or run code or terminal commands.
- The user is the product owner and hockey expert (Hurricanes fan). Ask them hockey and product questions. Make technical decisions yourself and explain each in one plain-English sentence.
- Their eye test is part of validation. When a number contradicts what they see, check the data for a bug first, then report honestly. Their beliefs (H1-H6, brief Section 1) are hypotheses. Never tune a model to agree with them. Those beliefs are private: use them only as checks in conversation and never publish them, or anything framed as testing them, on the site.
- Show, don't describe: deliver a deployed page (a URL), not code or diffs. Keep updates short and non-technical.
- Things only they can do (account settings, approvals) need click-by-click instructions, one step at a time.
- End of each phase: deploy, send the URL with a 3-5 line plain-English summary, ask at most one or two hockey or product questions.

## Architecture

- Nightly batch pipeline (Python) pulls games, builds derived tables, computes metrics, exports JSON to `site/public/data/`.
- Static Astro site reads that JSON. No server or database at runtime.
- GitHub hosts everything: repo, Actions (scheduled pipeline, ~10:00 and ~16:00 UTC), Pages (site).
- Processed Parquet and gzipped raw responses live on an orphan `data` branch, partitioned by season. Files under 50 MB.
- Pipeline is idempotent and incremental; never refetch final games.

## Stack

- Pipeline: Python 3.12 via `uv`, `httpx`, `polars`, `duckdb`, `pyarrow`, `scikit-learn`, `statsmodels`, `pydantic`, `pytest`.
- Site: Astro (static) with TypeScript, Observable Plot for charts, D3 only where Plot can't do it.
- Layout: `pipeline/{ingest,build,metrics,export,tests}`, `content/{scouting,overrides}`, `site/`, `.github/workflows/`.

## Environment notes

- Checked 2026-10-09 from this machine: `api-web.nhle.com`, `api.nhle.com`, and the MoneyPuck download host are all reachable, so fixtures-via-Actions is not required for local development.
- GitHub CLI is signed in as `discomystery`.
- Repo: `discomystery/last-change` (public). Live site: https://discomystery.github.io/last-change/ ("Last Change" is a working title the user has not confirmed).
- Deploy today: `npm --prefix site run build`, then force-push `site/dist` (plus `.nojekyll`) to the `gh-pages` branch. The GitHub token lacks the `workflow` scope, so Actions workflow files cannot be pushed yet; the user must approve that scope before the nightly pipeline (Phase 2).
- Phase 1 mock: real names, records, date and venue come from `site/scripts/fetch-mock-context.mjs`; every statistic in `site/src/data/mock.ts` is invented.
- `uv` is installed at `/Users/katherine/Library/Python/3.9/bin/uv` (not on PATH). Run pipeline steps from the repo root: `uv run --project pipeline python -m pipeline.run {ingest|build|validate|export} --season YYYY`; tests with `uv run --project pipeline pytest -q pipeline/tests`.
- Local data cache is `data/` (gitignored): `data/raw/{season}/{pbp,box,shifts,shifts_html}/` and `data/tables/{season}/*.parquet`. It is not yet published to a `data` branch.
- The shift-chart API returns nothing for some games (11 of the first 65 in 2026-27). `pipeline/ingest/html_shifts.py` falls back to the official HTML TOI reports (URL pattern verified); with it, 100% of player-games match boxscore TOI within 5 s.
- MoneyPuck shot columns in the brief are all confirmed present; `game_id` + season*1,000,000 gives the NHL game id. Join match rate is 99.6% overall. The brief's 99%-per-game target is not reachable (one re-scored shot in an 80-shot game is 98.75%), so the check is on the season-wide rate.
- Running `astro build` can knock over the local dev preview; restart it with preview_start before screenshots.

## Data rules

- NHL API is unofficial: verify field names against live responses. Rate-limit to ~2 requests/second, descriptive User-Agent, cache everything.
- MoneyPuck: non-commercial use only, download only files listed on their data page, and credit MoneyPuck.com wherever xG-derived numbers appear.
- Do not scrape Natural Stat Trick or Evolving-Hockey. Manual spot checks only.
- Never hardcode rosters or player names; pull from the API.
- Exclude shootouts from everything; exclude empty-net time from 5v5 and goalie stats; exclude penalty shots from on-ice stats.
- Derive shooting team for blocked shots from `shootingPlayerId`, and zones from normalized coordinates, not from `eventOwnerTeamId` / `zoneCode`.

## Analytical conventions

- Score-and-venue adjust shot metrics; use game-state filters (tied, close, pre-first-goal) for behavioral metrics. Never compare raw full-game rates.
- Rates per 60; show for and against separately, not just percentages.
- Split every deployment and matchup metric by home vs. road (last change).
- Every displayed metric carries sample size, shrinkage toward a prior, and an 80% interval. Grey out tiny samples.
- Rink-adjust hits, giveaways, takeaways, blocks.
- Recipe findings are tendencies. Never write that something causes wins.

## Site conventions

- Audience is fans: every number gets a plain-English tooltip, every chart a one-line takeaway.
- Mobile-first, light and dark mode. Percentile bars, not radar charts.
- No NHL or team logos; abbreviations and subtle team colors only.
- Footer on every page: "Expected goals data from MoneyPuck.com", "Game data: NHL", and a "not affiliated with the NHL" disclaimer.
- "What to watch" insights are rules-based templates citing metric and sample size. No free-text generation at runtime.

## Decisions since the brief (user feedback, 2026-10-09)

- Look: bookish rounded serif (Fraunces display with SOFT 100, Literata body, Hanken Grotesk for data and labels) on the cool "ice" palette, keeping the rink motifs. Bold Fraunces headings are approved; the big team abbreviations in the rink header are set in the sans (Hanken Grotesk 700), not the serif. The score numbers beside them on the recap stay in Fraunces (the user likes them). Keep pages airy; the first mock was too dense.
- The explainer line under each section heading stays in the serif (Literata, 18px); the user tried sans there and preferred the serif. On desktop, short text should sit on one line: avoid narrow max-widths and narrow cards that force a wrap (fingerprint cards are two per row for this reason).
- No full-width bar charts. Fingerprint dimensions are cards: a short two-lane scale (one dot per team, likely-range whisker, league-average tick), a one-word label at each extreme (e.g. Sieve / Wall; the user may rename these), and a plain-language sentence per team with the actual stat and league rank.
- Arena-scorer adjustment needs a real explainer: what it is, the exact calculation, and a table of all 32 arenas with their factors per event type.
- Season switch: every metric is exported twice (blended with prior seasons, and current season only) and the site toggles between them client-side. Blended is the default.
- Post-game page compares the preview with what happened. Each "What to watch" insight must be stored as a structured, checkable claim (metric, team, baseline, direction, threshold) in a preview snapshot frozen at puck drop; the recap grades each claim by rule (held up / partly / didn't happen) and never rewrites the preview. Keep a season-long tally of how often claims hold. This moves the recap's preview-vs-result part into Phase 3; player report cards stay in Phase 4.

## Agreed scope and site map (2026-10-09)

- Home: the visitor's team's next game, upcoming previews, latest post-game pages, the league slate.
- Preview (one per game, every matchup): the FULL preview with all sections (short version, complete fingerprints, lines and matchups, special teams, goalies, how they win, key players, data notes). Do not slim it; solve density with layout.
- Post-game (one per game): graded preview calls, surprises, "did he do his job", goalies.
- Team (all 32): fingerprint, usual lines and deployment home/road, special teams, goalies, goal sources, what's true when they win, schedule.
- Player and goalie pages; League page (all 32 on every scale, league goalie chart); Track record (how often calls hold); Methods (glossary, arena-scorer table, sources and blind spots).
- Every page: season switch, home-team picker, arena-adjusted/raw switch, credits footer.
- Home-team picker: any visitor chooses their team; the choice is remembered in the browser (localStorage) and drives the home page and defaults. No team is assumed for a new visitor.
- Arena-adjusted stats (hits, giveaways, takeaways, blocks and anything built on them) are shown as a SCORE (an index against league average), never as an adjusted count, so the site never appears to report events that were not recorded. A site-wide switch shows raw recorded numbers instead; arena-adjusted is the default.
- Team page order: the team's next game is at the very top, then form and schedule, then fingerprint, lines, special teams, goalies, goal sources, win conditions.
- Player pages: before designing them, talk with the user about showing what a player entering or leaving the lineup does to the team (with-or-without-him results, who moves up or down the lineup, which matchups change). The user raised this on 2026-10-09 and wants a discussion first, not a finished design. They want all three angles, with the team's results when he is out as the most important.
- Lineup-impact work is parked (2026-10-09): the user wants to think about it more and return to the agreed scope. Do not raise it again unless they do.
- Quality is shown without red/green: a five-pip ink meter (more filled = better than more of the comparison group, always oriented so more is better) with a tooltip. Lines and pairs are compared with the same-numbered unit on other teams (first lines with first lines, top pairs with top pairs). Neutral "how much" stats (ice time, zone starts, competition) use a tick on a short line instead, so they are not read as good or bad.
- Player names are styled as links everywhere (class `plink`) so they can open player pages later.
- Folds that will be opened often are a full-width pill bar with clear show/hide wording (class `fold`), never a small text toggle.
- Special teams is organised by matchup, not by team: "When X is on the power play" pairs X's power-play units with the opponent's penalty killers at equal weight (three columns: power-play units, shot map, penalty-kill units), under a row of stat tiles.
- Special teams has a two-step picker with short, even labels: a team pill (the two teams), then a situation sub-pill (All special teams / On the power play / On the penalty kill). "On the penalty kill" shows the opponent's power play against that team. It opens on the visitor's own team if it is playing, otherwise the away team, on the power play. The user dislikes buttons of very uneven length. All four views share one layout. Cards in a row must end level: stretch them to equal height and split each units card evenly so PP2 and PK2 line up.
- Matchup grids run the full page width, forwards and defense pairs side by side.
- Preview density is handled by layout, not removal: section links stay pinned while scrolling; fingerprints show the six biggest differences first with all traits behind a tap; secondary tables (defense-pair matchups, previous meetings, backups, penalty killers) start collapsed.
- On phones the settings strip (team, seasons, arena switch) collapses behind a single Settings button.
- Build order: launch with home, preview, post-game (calls and goalies), team, methods and the switches, verified on Hurricanes games first, then all teams. Second wave: player/goalie pages, league, track record, report cards, win conditions. Optional later: EDGE, AllThreeZones, in-house xG.
- Out of scope: live in-game updates, win predictions or odds, accounts, comments, logos, anything commercial.

## Phases

0. Setup. 1. Mock preview with placeholder numbers. 2. Data pipeline and validation suite. 3. Real preview. 4. Deeper analysis. Details in brief Section 9; validation targets in Section 8.
