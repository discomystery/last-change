// Recent games on player pages (Sora, 2026-10-18): his last ten NHL games from the league's box scores, newest first,
// written by pipeline/export/gamelog.py. Last season's games fill in when he has played fewer than ten this season.
import type { Row } from './statDefs';

type Log = { cols: string[]; rows: (string | number | null)[][] };
const files = import.meta.glob('../../public/data/gamelog/*.json', { eager: true, import: 'default' }) as Record<string, Log>;
const byId = new Map(Object.entries(files).map(([f, v]) => [Number(f.split('/').pop()!.replace('.json', '')), v]));
// Games with a post-game page to link to.
const recaps = new Set(Object.keys(import.meta.glob('../../public/data/recaps/2*.json')).map((f) => Number(f.split('/').pop()!.replace('.json', ''))));

export type Game = Row & { gid: number; date: string; yr: number; gt: number; team: string; opp: string; home: number; us: number; them: number; end: string; recap: boolean };

export function gamesOf(id: number): Game[] {
  const l = byId.get(id);
  if (!l) return [];
  return l.rows.map((r) => {
    const g = Object.fromEntries(l.cols.map((c, i) => [c, r[i]])) as Game;
    g.recap = recaps.has(g.gid);
    return g;
  });
}

/** "W 4–2", "OTL 2–3 OT", "W 3–2 SO". In the playoffs an overtime loss is a loss. */
export function result(g: Game) {
  const res = g.us > g.them ? 'W' : g.end === 'REG' || g.gt === 3 ? 'L' : 'OTL';
  return `${res} ${g.us}–${g.them}${g.end !== 'REG' ? ` ${g.end}` : ''}`;
}

/** "2026-27", or "2025-26 playoffs". */
export const seasonLabel = (yr: number, gt: number) => `${yr}-${String((yr + 1) % 100).padStart(2, '0')}${gt === 3 ? ' playoffs' : ''}`;

// Who has been in net for each club this season, newest game first (gamelog/teams.json).
type NetRow = [number, string, string, number, number, number, string, number | null, number[]];
const netFile = Object.values(import.meta.glob('../../public/data/gamelog/teams.json', { eager: true, import: 'default' }))[0] as Record<string, NetRow[]> | undefined;
export type NetGame = { gid: number; date: string; opp: string; home: number; us: number; them: number; end: string; starter: number | null; relief: number[] };
export const netOf = (team: string, before?: string): NetGame[] => (netFile?.[team] ?? [])
  .map(([gid, date, opp, home, us, them, end, starter, relief]) => ({ gid, date, opp, home, us, them, end, starter, relief }))
  .filter((g) => !before || g.date < before);
