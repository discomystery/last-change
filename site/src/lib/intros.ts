// Plain-English intros written by pipeline/export/intros.py. Read through a glob so a build without them still works.
import teams from '../../public/data/teams.json';
import previewIndex from '../../public/data/previews/index.json';

type Game = { date: string; opp: string; id: number; home: boolean };
type TeamIntro = { text: string[] };
type PlayerIntro = { text: string[]; next: string[]; game?: Game };
const files = import.meta.glob('../../public/data/intros/*.json', { eager: true, import: 'default' }) as Record<string, any>;
const pick = (name: string) => Object.entries(files).find(([f]) => f.endsWith(`/${name}.json`))?.[1];

export const teamIntro = (abbr: string): TeamIntro | null => pick('teams')?.teams?.[abbr] ?? null;
export const playerIntro = (id: number): PlayerIntro | null => pick('players')?.players?.[id] ?? null;

/** "Sat, Oct 10 · at San Jose", linked to the preview when one exists. */
export function nextGame(g: Game | undefined, base: string) {
  if (!g) return { label: undefined, href: null };
  const opp = teams.find((t) => t.abbr === g.opp);
  const day = new Date(`${g.date}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
  const has = previewIndex.some((p) => p.id === g.id);
  return { label: `Next game · ${day} · ${g.home ? 'vs' : 'at'} ${opp?.place ?? g.opp}`, href: has ? `${base}preview/${g.id}/` : null };
}
