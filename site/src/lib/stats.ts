// Plain counting stats as the league records them (pipeline/export/stats.py): every skater's, goalie's and team's
// season line for this season and last. Player pages, team pages and the Leaders page all read them through here,
// so a stat has one name, one tooltip and one format everywhere.
export * from './statDefs';

type Kind = 'skaters' | 'goalies' | 'teams';
type File = { generated_at: string; cols: string[]; seasons: Record<string, (string | number | null)[][]> };
// Read with a glob so a build without the files (before the first update writes them) still works.
const files = import.meta.glob('../../public/data/stats/*.json', { eager: true, import: 'default' }) as Record<string, File>;
const file = (kind: Kind) => Object.entries(files).find(([f]) => f.endsWith(`/${kind}.json`))?.[1];

import type { Row } from './statDefs';

/** Every row of one kind for one season, as objects. */
export function rows(kind: Kind, season: number | string): Row[] {
  const f = file(kind);
  const data = f?.seasons[String(season)] ?? [];
  return data.map((r) => Object.fromEntries(f!.cols.map((c, i) => [c, r[i]])));
}

/** The seasons on file, latest first. */
export function seasons(kind: Kind = 'skaters'): number[] {
  return Object.keys(file(kind)?.seasons ?? {}).map(Number).sort((a, b) => b - a);
}
export const seasonName = (s: number) => `${s}-${String((s + 1) % 100).padStart(2, '0')}`;
export const asOf = (kind: Kind = 'skaters') => file(kind)?.generated_at;

/** One player's line for each season on file (latest first), or nothing. */
export function lines(kind: 'skaters' | 'goalies', id: number): { season: number; row: Row }[] {
  return seasons(kind).flatMap((s) => {
    const row = rows(kind, s).find((r) => r.id === id);
    return row ? [{ season: s, row }] : [];
  });
}

