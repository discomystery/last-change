// Strength of schedule, written by pipeline/export/schedule_strength.py. Read through a glob so a build without the
// file still works: the Teams tab and the team-page card then leave themselves out.
const files = import.meta.glob('../../public/data/schedule_strength.json', { eager: true, import: 'default' }) as Record<string, any>;

export type Part = {
  games: number; rank: number; hard: number; opp: number; venue: number; rest: number;
  home: number; b2b: number; opp_b2b: number; opp_pct: number | null;
};
export type NextGame = { id: number; date: string; opp: string; home: boolean; b2b: number; opp_b2b: number; hard: number };
export type SosTeam = { team: string; past: Part | null; left: Part | null; next: Part | null; games: NextGame[] };
export type Sos = { season: number; next: number; terms: { home: number; b2b: number }; cuts: number[]; ratings: Record<string, number>; teams: SosTeam[] };

export const sos = Object.values(files)[0] as Sos | undefined;
export const sosFor = (abbr: string) => sos?.teams.find((t) => t.team === abbr) ?? null;

export type Kind = 'past' | 'left' | 'next';
export const KINDS: [Kind, string, string][] = [
  ['past', 'So far', 'the season so far'],
  ['left', 'Rest of season', 'the rest of the season'],
  ['next', 'Next 10', 'the next 10 games'],
];

const ord = (n: number) => { const s = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
/** 1 = "the hardest", 2 = "2nd hardest", 31 = "2nd easiest", 32 = "the easiest" (of `n` clubs). */
export function rankWords(rank: number, n = 32) {
  if (rank === 1) return 'the hardest';
  if (rank === n) return 'the easiest';
  return rank <= n / 2 ? `${ord(rank)} hardest` : `${ord(n + 1 - rank)} easiest`;
}

const xg = (v: number) => Math.abs(v).toFixed(2);
const pct = (v: number | null) => (v == null ? null : v.toFixed(3).replace(/^0/, ''));
/** What a schedule's opponents are like, as a sentence: "Their opponents out-chance teams by 0.06 expected goals a
 * game and have a .558 points percentage between them." Played games read in the past, as the opponents were then. */
export function opponents(p: Part, kind: Kind) {
  const past = kind === 'past';
  const lvl = Math.abs(p.opp) < 0.005 ? (past ? 'were dead even on chances at the time' : 'are dead even on chances')
    : p.opp > 0 ? `${past ? 'were out-chancing' : 'out-chance'} teams by ${xg(p.opp)} expected goals a game${past ? ' at the time' : ''}`
      : `${past ? 'were getting' : 'get'} out-chanced by ${xg(p.opp)} expected goals a game${past ? ' at the time' : ''}`;
  const rec = pct(p.opp_pct);
  return `Their opponents ${lvl}${rec ? `, and have a ${rec} points percentage between them${past ? ' today' : ''}` : ''}.`;
}
/** The games, then the opponents, then rest: the body under a schedule's heading. */
export function sentence(p: Part, kind: Kind) {
  const games = `${p.games} game${p.games === 1 ? '' : 's'}, ${p.home} at home.`;
  const rest = [p.b2b ? `${p.b2b} back-to-back${p.b2b === 1 ? '' : 's'} of their own` : 'no back-to-backs of their own',
    p.opp_b2b ? `${p.opp_b2b} game${p.opp_b2b === 1 ? '' : 's'} against a team on the second night of one` : null].filter(Boolean).join(', and ');
  return `${games} ${opponents(p, kind)} ${rest[0].toUpperCase()}${rest.slice(1)}.`;
}

/** Shade band (0-4) of one upcoming game among every game left in the league. */
export const band = (hard: number, cuts: number[]) => cuts.filter((c) => hard > c).length;
export const BAND_WORDS = ['Among the easiest fifth of games left in the league', 'Easier than most games left in the league',
  'In the middle of the games left in the league', 'Harder than most games left in the league', 'Among the hardest fifth of games left in the league'];
export const BAND_H = [3, 10, 18, 27, 40];

const DAY = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
export const day = (iso: string) => DAY.format(new Date(`${iso}T12:00:00Z`));
