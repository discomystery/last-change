// Playoff series, when the season's data has them (the playoff build: pipeline/playoffs/pages.py). The live regular
// season has no series files, so everything here comes back empty and pages show nothing playoff-shaped.
export type SGame = { id: number; game: number; date: string; home: string; away: string; final: boolean; hs: number | null; as: number | null; ot: number; winner: string | null };
export type SeriesRow = { id: string; letter: string; round: number; round_name: string; conference: string | null; top: string; bottom: string; wins: [number, number]; winner: string | null; loser: string | null; games: SGame[]; head: string; tally: Record<string, number> };
export type SeriesIndex = { season: number; spring: number; series: SeriesRow[] };

const idx = Object.values(import.meta.glob<{ default: SeriesIndex }>('../../public/data/series/index.json', { eager: true }))[0]?.default ?? null;

export const seriesIndex: SeriesIndex | null = idx;
export const ROUND_SHORT: Record<number, string> = { 1: 'First round', 2: 'Second round', 3: 'Conference final', 4: 'Stanley Cup Final' };

/** The series a game belongs to, with the score before and after it ([top, bottom]). */
export function seriesOf(gameId: number): { s: SeriesRow; g: SGame; before: [number, number]; after: [number, number] } | null {
  for (const s of idx?.series ?? []) {
    const i = s.games.findIndex((g) => g.id === gameId);
    if (i < 0) continue;
    const before: [number, number] = [0, 0];
    for (const g of s.games.slice(0, i)) if (g.winner) before[g.winner === s.top ? 0 : 1] += 1;
    const g = s.games[i];
    const after: [number, number] = [...before];
    if (g.winner) after[g.winner === s.top ? 0 : 1] += 1;
    return { s, g, before, after };
  }
  return null;
}

/** "CAR lead 2-1", "Tied 1-1", "CAR win 4-2". */
export function scoreWords(s: SeriesRow, w: [number, number], done = false): string {
  const [a, b] = w;
  if (a === b) return `Series tied ${a}-${b}`;
  const [lead, hi, lo] = a > b ? [s.top, a, b] : [s.bottom, b, a];
  return `${lead} ${done || hi === 4 ? 'won' : 'lead'} ${hi}-${lo}`;
}

/** "OT", "2OT" for overtime games. */
export const otLabel = (ot: number) => (ot <= 0 ? '' : ot === 1 ? 'OT' : `${ot}OT`);
/** "Final in overtime", "Final in double overtime". */
export const otWords = (ot: number) => ['', 'overtime', 'double overtime', 'triple overtime', 'quadruple overtime', 'quintuple overtime'][ot] ?? `${ot} overtimes`;
