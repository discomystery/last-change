// The live feed (Sora, 2026-11-02): while a game is on, pipeline/live/loop.py rebuilds each game's shots every two minutes
// on a small site of its own (its own repository and GitHub Pages site, since publishing this site takes minutes and
// replaces all of it). Live pages and links are only built while LIVE_FEED is set.
export const LIVE_FEED: string | null = import.meta.env.PUBLIC_LIVE_FEED || null;
// Games a build makes live pages for: from a day and a half back (a game that ended since the last build keeps its page)
// to a week ahead.
export const liveWindow = (start: string, now = Date.now()) => { const t = Date.parse(start); return t > now - 36 * 3600e3 && t < now + 8 * 24 * 3600e3; };
