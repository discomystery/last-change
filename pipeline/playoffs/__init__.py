"""Playoff test build: replays a past spring's Stanley Cup playoffs as the site would have shown them.

Kept apart from the live pipeline until it is ready to be added (Sora, 2026-10-13, thread "Playoffs"). Run with
`uv run --project pipeline python -m pipeline.playoffs.replay` from the repo root; it writes its own data folder
and its own site data, and never touches the live site's files or the data branch.
"""
