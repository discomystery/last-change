// Skater rankings: every rated skater on one rating at a time. Built once at build time from the player files and
// served as one small JSON file per rating, so the rankings page only loads the list being looked at.
import { TRAITS, EDGE, GROUPS, peerWord, type Mode } from './players';

export const modes: Mode[] = ['blend', 'season'];
/** Ratings in the order the player page shows them, then the NHL EDGE measures. */
export const RATINGS = [
  ...GROUPS.flatMap((g) => Object.entries(TRAITS).filter(([, t]) => t.group === g).map(([key, info]) => ({ key, info, edge: false }))),
  ...Object.entries(EDGE).map(([key, info]) => ({ key, info, edge: true })),
];
export const ratingGroups = [...GROUPS, 'Skating and shot'];
export const groupOf = (r: (typeof RATINGS)[number]) => (r.edge ? 'Skating and shot' : r.info.group);

const files = import.meta.glob('../../public/data/players/*.json', { eager: true, import: 'default' }) as Record<string, any>;
export const skaters = Object.entries(files)
  .filter(([f]) => /\/\d+\.json$/.test(f))
  .map(([, p]) => p)
  .filter((p) => p.group === 'F' || p.group === 'D');

/** One row per rated player: rank, percentile, likely range, the number in words; raw recorded counts where the rating is arena-adjusted. */
export type Row = { id: number; v: number; r: number; p: number; lo: number; hi: number; vs: string; s: string; t: string; rr?: number; rp?: number; rs?: string; rt?: string };
const cap = (x: string) => x[0].toUpperCase() + x.slice(1);

export function ratingRows(key: string): Record<Mode, Row[]> {
  const rating = RATINGS.find((r) => r.key === key)!;
  const out = { blend: [] as Row[], season: [] as Row[] };
  for (const p of skaters) {
    for (const m of modes) {
      const e = rating.edge ? p.edge?.[m]?.[key] : p.traits?.[key]?.[m];
      if (!e || !e.ok || e.rank == null) continue;
      const v = e.index ?? e.v;
      const row: Row = { id: p.id, v: e.v, r: e.rank, p: e.pct, lo: e.lo ?? e.pct, hi: e.hi ?? e.pct, vs: e.vs ?? p.group, s: rating.info.short(v), t: `${cap(rating.info.say(v))}.` };
      if (e.raw !== undefined && rating.info.raw) Object.assign(row, { rr: e.raw, rp: e.raw_pct, rs: rating.info.rawShort!(e.raw), rt: `${cap(rating.info.raw(e.raw))}.` });
      out[m].push(row);
    }
  }
  for (const m of modes) out[m].sort((a, b) => a.r - b.r || b.p - a.p);
  return out;
}

/** Everything the page needs to name a player: name, team, position and the season line. */
export const roster = () => skaters.map((p) => ({ id: p.id, n: p.name, l: p.last, t: p.team, pos: p.pos, g: p.group, sub: p.sub, gp: p.stats?.season?.gp ?? 0, pts: p.stats?.season?.p ?? 0 }));
export const peer = peerWord;

/** Players a list compares: forwards, centers, wingers or defensemen. */
export const GROUP_PICK: [string, string][] = [['F', 'forwards'], ['C', 'centers'], ['W', 'wingers'], ['D', 'defensemen']];
const inGroup = (g: string, p: { g: string; sub: string }) => (g === 'F' ? p.g === 'F' : g === 'D' ? p.g === 'D' : p.sub === g);
/**
 * The list for one comparison group, best first. When the rating already compares exactly this group, its own league
 * rank stands; otherwise (forwards on a rating that splits centers from wingers, or centers alone on one that does not)
 * players are ordered by where each sits among his own peers and numbered down the list.
 */
export function ordered(rows: Row[], g: string, who: Map<number, { g: string; sub: string }>) {
  const xs = rows.filter((r) => { const p = who.get(r.id); return p && inGroup(g, p); });
  const native = xs.every((r) => r.vs === g);
  if (native) return xs.map((r) => ({ ...r, n: r.r }));
  return [...xs].sort(mixed(xs)).map((r, i) => ({ ...r, n: i + 1 }));
}
/** Mixed groups are ordered by the number itself, in whichever direction counts as better on this rating. */
export const mixed = (xs: Row[]) => {
  const best = xs.reduce((a, b) => (b.p > a.p ? b : a), xs[0]), worst = xs.reduce((a, b) => (b.p < a.p ? b : a), xs[0]);
  const up = !best || best.v >= worst.v ? 1 : -1;
  return (a: Row, b: Row) => up * (b.v - a.v) || b.p - a.p;
};
