// Key players: the forwards and defensemen a team leans on most and the goalie who starts the most, what it leans on
// them for, and what they do better than others at their position. Shared by team pages (and ready for previews).
import { standouts, roleTip, type Skater, type Mode } from './players';
import goalieData from '../../public/data/goalies.json';
import rosterData from '../../public/data/roster.json';
import schedule from '../../public/data/schedule.json';
import { goalieTraitInfo } from './traits';
import { describe, type RosterPlayer } from './roster';

const files = import.meta.glob('../../public/data/players/*.json', { eager: true, import: 'default' }) as Record<string, any>;
const skaters = Object.entries(files)
  .filter(([f]) => /\/\d+\.json$/.test(f))
  .map(([, p]) => p)
  .filter((p) => p.group === 'F' || p.group === 'D') as Skater[];
const onClub = Object.fromEntries(Object.entries(files).filter(([f]) => /\/\d+\.json$/.test(f)).map(([, p]) => [p.id, p]));
const previews = import.meta.glob('../../public/data/previews/*.json', { eager: true, import: 'default' }) as Record<string, any>;

/** How many of each group make the list. */
export const KEY_COUNT = { F: 3, D: 2, G: 1 } as const;

export type Job = { text: string; tip: string };
export type KeyPlayer = { p: Skater; jobs: Job[]; strengths: Record<Mode, ReturnType<typeof standouts>> };

const nth = ['first', 'second', 'third', 'fourth'];
const lineName = (label: string) => `${nth[Number(label.slice(1)) - 1] ?? ''} ${label[0] === 'L' ? 'line' : 'pair'}`.trim();
const lastName = (n: string) => n.split(' ').slice(1).join(' ') || n;

/** The jobs his coach gives him, from how he has actually been used lately, as short tags. Not good or bad in itself. */
function jobs(p: Skater, place: string): Job[] {
  const r = p.role;
  const out: Job[] = [];
  const grp = p.group === 'D' ? 'defensemen' : 'forwards';
  if (r.toi && r.toi_rank && r.toi_rank <= 2)
    out.push({ text: r.toi_rank === 1 ? 'Most ice time' : '2nd in ice time', tip: `${r.toi_rank === 1 ? 'The most' : 'The second-most'} ice time a game of ${place}’s ${grp} who have played at least half the games.` });
  if (r.line) out.push({ text: `${r.line.label} · ${r.line.mates.map((m) => lastName(m.name)).join(', ')}`, tip: `${lineName(r.line.label)[0].toUpperCase()}${lineName(r.line.label).slice(1)} at 5-on-5, picked by ice time together over the last ten games and numbered by its players’ ice time.` });
  if (r.pp) out.push({ text: r.pp.label, tip: `${r.pp.label === 'PP1' ? 'First' : 'Second'} power-play unit.${r.pp.role ? ' ' + roleTip[r.pp.role] : ''}` });
  if (r.pk?.role === 'starter' || r.pk?.role === 'second')
    out.push({ text: 'Kills penalties', tip: r.pk.role === 'starter'
      ? `Starts penalty kills: out at the start of ${r.pk.starts} of ${place}’s ${r.pk.kills} kills lately${r.pk.draw ? ', usually to take the faceoff' : ''}.`
      : `Second wave: on for ${r.pk.kills_in} of ${place}’s ${r.pk.kills} kills lately, usually after the first change.` });
  if (r.faceoff) out.push({ text: 'PP draw man', tip: r.faceoff });
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

type GMode = { starts: number; shots: number; gsax: number; sv: number | null; pct: Record<string, number | null> };
type GoalieRow = { id: number; name: string; blend: GMode; season: GMode | null };
export type GoalieStrength = { key: string; label: string; tip: string; pct: number };
export type KeyGoalie = {
  id: number; name: string; number: number | null; starts: number; teamStarts: number;
  season: GMode | null; jobs: Job[]; strengths: Record<Mode, { best: GoalieStrength[]; worst: GoalieStrength[]; few: boolean }>;
};

/** Goalie ratings that are his own doing. How busy he is, how dangerous the shots are, breakdowns and waits describe
 *  what his team asks of him, so they never count as strengths or weak spots. */
const OWN = ['gsax', 'rebounds', 'steady'];
/** Starts needed before a rating shows on his card, in either mode: goalie ratings have no likely ranges, so a handful of
 *  starts this season would show extremes that are mostly bounces. */
const MIN_STARTS = (goalieData.min_starts as Record<Mode, number>).blend;

function goalieStrengths(g: GMode | null) {
  const out: GoalieStrength[] = [];
  if (!g || g.starts < MIN_STARTS) return { best: out, worst: out, few: true };
  for (const key of OWN) {
    const pct = g.pct[key];
    const info = goalieTraitInfo.find((t) => t.key === key);
    if (pct == null || !info || (pct > 25 && pct < 75)) continue;
    out.push({ key, label: info.label, tip: info.tip, pct });
  }
  return {
    best: out.filter((x) => x.pct >= 75).sort((a, b) => b.pct - a.pct).slice(0, 3),
    worst: out.filter((x) => x.pct <= 25).sort((a, b) => a.pct - b.pct).slice(0, 1),
    few: false,
  };
}

/** The goalie who has started the most of the team's games this season, among goalies the club has and who are not out
 *  (injured, suspended, scratched, sent down). If the busiest is out, the busiest of the rest is shown with a tag saying
 *  whom he is covering for. A tie goes to whoever played most recently, then to whoever started more over this season
 *  and last. */
export function keyGoalie(abbr: string, place: string): KeyGoalie | null {
  const all = ((goalieData.teams as Record<string, GoalieRow[]>)[abbr] ?? []);
  const roster = ((rosterData as any).teams?.[abbr] ?? []) as RosterPlayer[];
  const rosterOf = (id: number) => roster.find((r) => r.id === id);
  const starts = (g: GoalieRow) => g.season?.starts ?? 0;
  const nameOf = (g: GoalieRow) => onClub[g.id]?.name ?? g.name;
  const order = (a: GoalieRow, b: GoalieRow) => starts(b) - starts(a)
    || (rosterOf(b.id)?.last ?? '').localeCompare(rosterOf(a.id)?.last ?? '')
    || b.blend.starts - a.blend.starts;
  // Still with the club: on its roster today (or, with no roster saved, his player page names this team).
  const mine = all.filter((g) => starts(g) > 0 && (roster.length ? rosterOf(g.id) && rosterOf(g.id)!.status !== 'no_club' : (onClub[g.id]?.team ?? abbr) === abbr)).sort(order);
  if (!mine.length) return null;
  const isOut = (g: GoalieRow) => { const r = rosterOf(g.id); return r ? describe(r).out : false; };
  const pick = mine.find((g) => !isOut(g)) ?? mine[0];
  const teamStarts = all.reduce((n, g) => n + starts(g), 0);
  const name = nameOf(pick);
  const ahead = mine.filter((g) => g !== pick && isOut(g) && order(g, pick) < 0);
  const tied = mine.filter((g) => g !== pick && !ahead.includes(g) && starts(g) === starts(pick));
  const jobs: Job[] = [];
  if (ahead.length) {
    const g = ahead[0], r = rosterOf(g.id)!;
    jobs.push({
      text: `In for ${(onClub[g.id]?.last as string) ?? nameOf(g).split(' ').slice(1).join(' ')}`,
      tip: `${nameOf(g)} has started the most, ${starts(g)} of ${place}’s ${teamStarts} games, but is out for now (${describe(r).tag[0].toLowerCase()}${describe(r).tag.slice(1)}). ${name} has started ${starts(pick)}.`,
    });
  } else jobs.push({
    text: tied.length ? 'Shares the net' : 'Most starts',
    tip: tied.length
      ? `${starts(pick)} of ${place}’s ${teamStarts} starts so far, level with ${tied.map(nameOf).join(' and ')}. Shown here because ${(rosterOf(pick.id)?.last ?? '') > (rosterOf(tied[0].id)?.last ?? '') ? 'he played more recently' : 'he has more starts over this season and last'}.`
      : `${starts(pick)} of ${place}’s ${teamStarts} starts so far, the most of any ${place} goalie.`,
  });
  // Next game's projected starter, from the saved preview: NHL.com's pick once its game-day preview is up, else ours.
  const next = schedule.games.find((x) => !x.final && (x.home === abbr || x.away === abbr));
  const proj = next && previews[`../../public/data/previews/${next.id}.json`]?.projected?.[abbr];
  if (proj?.id === pick.id)
    jobs.push({ text: proj.from === 'nhl' ? 'Projected to start next' : 'Likely to start next', tip: proj.from === 'nhl' ? 'NHL.com lists him as the projected starter for the next game.' : 'Our guess for the next game, from who has started lately and the schedule. Not confirmed.' });
  return {
    id: pick.id, name, number: onClub[pick.id]?.number ?? rosterOf(pick.id)?.number ?? null,
    starts: starts(pick), teamStarts, season: pick.season, jobs,
    strengths: { blend: goalieStrengths(pick.blend), season: goalieStrengths(pick.season) },
  };
}

/** Competition faced, when it is notably tough: part of the job, at 5-on-5. */
export const toughMinutes = (p: Skater, mode: Mode) => {
  const c = p.competition?.[mode]?.all;
  return c && c.pct >= 70 ? c.pct : null;
};
