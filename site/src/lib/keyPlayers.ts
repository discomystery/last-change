// Key players: the forwards and defensemen a team leans on most, what it leans on them for, and what they do better
// than others at their position. Shared by team pages (and ready for previews).
import { standouts, type Skater, type Mode } from './players';

const files = import.meta.glob('../../public/data/players/*.json', { eager: true, import: 'default' }) as Record<string, any>;
const skaters = Object.entries(files)
  .filter(([f]) => /\/\d+\.json$/.test(f))
  .map(([, p]) => p)
  .filter((p) => p.group === 'F' || p.group === 'D') as Skater[];

/** How many of each group make the list. */
export const KEY_COUNT = { F: 4, D: 2 } as const;

export type Job = { text: string; tip?: string; tag?: string; tagTip?: string };
export type KeyPlayer = { p: Skater; jobs: Job[]; strengths: Record<Mode, ReturnType<typeof standouts>> };

const nth = ['first', 'second', 'third', 'fourth'];
const lineName = (label: string) => `${nth[Number(label.slice(1)) - 1] ?? ''} ${label[0] === 'L' ? 'line' : 'pair'}`.trim();
const lastName = (n: string) => n.split(' ').slice(1).join(' ') || n;
const and = (a: string[]) => (a.length > 1 ? `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}` : a[0] ?? '');

/** The jobs his coach gives him, from how he has actually been used lately. Not good or bad in itself. */
function jobs(p: Skater, place: string): Job[] {
  const r = p.role;
  const out: Job[] = [];
  const grp = p.group === 'D' ? 'defensemen' : 'forwards';
  if (r.toi && r.toi_rank && r.toi_rank <= 2)
    out.push({ text: `${r.toi_rank === 1 ? 'Most' : 'Second-most'} ice time of ${place}’s ${grp}` });
  if (r.line) out.push({ text: `${lineName(r.line.label)[0].toUpperCase()}${lineName(r.line.label).slice(1)}, with ${and(r.line.mates.map((m) => lastName(m.name)))}` });
  if (r.pp) {
    const unit = r.pp.label === 'PP1' ? 'First power-play unit' : 'Second power-play unit';
    out.push({ text: unit, tag: r.pp.role ?? undefined });
  }
  if (r.pk?.role === 'starter') out.push({ text: r.pk.draw ? 'Starts penalty kills and takes the draw' : 'Starts penalty kills', tip: `Out at the start of ${r.pk.starts} of ${place}’s ${r.pk.kills} kills lately.` });
  else if (r.pk?.role === 'second') out.push({ text: 'Second wave on the penalty kill', tip: `On for ${r.pk.kills_in} of ${place}’s ${r.pk.kills} kills lately, usually after the first change.` });
  if (r.faceoff) out.push({ text: 'Takes power-play faceoffs, then changes', tip: r.faceoff });
  return out;
}

/** His standouts for a card: our own ratings first, then NHL EDGE tracking to fill. Time in the offensive zone is left
 *  out here because linemates and usage drive it, so it would crowd the top of nearly every top-line card. */
function strengths(p: Skater, mode: Mode) {
  const { best, worst } = standouts(p, mode);
  const keep = (x: (typeof best)[number]) => x.key !== 'oz';
  const order = (a: (typeof best)[number], b: (typeof best)[number]) => Number(!!a.edge) - Number(!!b.edge);
  return { best: best.filter(keep).sort(order).slice(0, 3), worst: worst.filter(keep).sort(order).slice(0, 1) };
}

/** The players a team leans on most: the forwards and defensemen with the most ice time a game this season,
 *  among those who have played at least half the team's games. */
export function keyPlayers(abbr: string, place: string): KeyPlayer[] {
  const mine = skaters.filter((p) => p.team === abbr && p.role?.toi_rank);
  return (['F', 'D'] as const).flatMap((g) =>
    mine.filter((p) => p.group === g).sort((a, b) => a.role.toi_rank! - b.role.toi_rank!).slice(0, KEY_COUNT[g]),
  ).map((p) => ({
    p,
    jobs: jobs(p, place),
    strengths: { blend: strengths(p, 'blend'), season: strengths(p, 'season') },
  }));
}

/** Competition faced, when it is notably tough: part of the job, at 5-on-5. */
export const toughMinutes = (p: Skater, mode: Mode) => {
  const c = p.competition?.[mode]?.all;
  return c && c.pct >= 70 ? c.pct : null;
};
