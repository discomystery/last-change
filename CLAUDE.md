# Hockey Matchup Analysis Site

Fan-facing site that explains how NHL teams differ in style and strategy and previews upcoming matchups. Default home team is the Carolina Hurricanes (CAR); every metric must work for all 32 teams.

**Full spec: [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md).** Read the relevant section before building any metric, page, or pipeline step. This file holds only the durable rules.

## Working agreement

- The user does not code and will not. Do all engineering. Never ask them to write, read, or run code or terminal commands.
- The user is the product owner and hockey expert (Hurricanes fan). Ask them hockey and product questions. Make technical decisions yourself and explain each in one plain-English sentence.
- Their eye test is part of validation. When a number contradicts what they see, check the data for a bug first, then report honestly. Their beliefs (H1-H6, brief Section 1) are hypotheses. Never tune a model to agree with them.
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
- `uv` is not installed yet; system Python is 3.9. Install `uv` before pipeline work (Phase 2).

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

## Phases

0. Setup. 1. Mock preview with placeholder numbers. 2. Data pipeline and validation suite. 3. Real preview. 4. Deeper analysis. Details in brief Section 9; validation targets in Section 8.
