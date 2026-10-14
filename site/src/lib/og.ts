// Link previews: the card each page shows when its link is pasted into Twitter/X, a text message, Slack or Discord.
// Pages build an OgCard here from the same data they draw from and pass it to Base, which writes it into the page
// (with the og:/twitter: tags pointing at /og/{page}.png); og/integration.mjs draws the PNGs after the build with
// og/render.mjs. Facts only: no hidden win chance, no relevancy score, nothing a page itself doesn't show.
import teamsData from '../../public/data/teams.json';
import fpData from '../../public/data/fingerprints.json';
import { cardColours } from './teams';
import { fpInfo, ordinal } from './traits';
import { standouts, posWord, type Skater } from './players';
import { clock, styleWords } from './format';
import { goalieCard } from './goalieShare';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

// The drawing code's own fingerprint, part of each image's ?v=, so a change of design reaches apps that cache previews.
export const OG_REV = (() => {
  try { return createHash('sha1').update(readFileSync(`${process.cwd()}/og/render.mjs`)).digest('hex').slice(0, 8); } catch { return ''; }
})();

type Side = { ab: string; nick: string; rec?: string; color: string; score?: number };
type Lane = { label: string; pct: number; rank?: string; ends?: [string, string] };
export type OgCard =
  | { kind: 'preview' | 'final'; date: string; venue?: string; end?: string; away: Side; home: Side; head: string; note?: string }
  | { kind: 'team'; abbr: string; name: string; kick: string; color: string; words: string; lanes: Lane[]; note?: string }
  | { kind: 'player'; name: string; kick: string; sub: string; number: number | null; color: string; stats: [string | number, string][]; lanes: Lane[]; note2?: string; note?: string }
  | { kind: 'page'; kick?: string; title: string; text?: string; note?: string }
  | { kind: 'home' };

const T = Object.fromEntries(teamsData.map((t) => [t.abbr, t]));
const day = (iso: string) => new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York' }).format(new Date(iso));
const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function sides(away: string, home: string, rec: (a: string) => string | undefined, score?: Record<string, number>) {
  const cc = cardColours(away, home);
  const s = (a: string): Side => ({ ab: a, nick: T[a]?.nick ?? a, rec: rec(a), color: cc.matchup[a].light, ...(score ? { score: score[a] } : {}) });
  return { away: s(away), home: s(home) };
}

/** Preview: the matchup, records and the story's headline (or the first call). */
export function previewOg(snap: { start: string; away: string; home: string; claims: { head: string }[]; story?: { head: string } | null }, records: Record<string, string>): OgCard {
  const n = snap.claims.length;
  return {
    kind: 'preview', date: day(snap.start), ...sides(snap.away, snap.home, (a) => records[a]),
    head: snap.story?.head ?? snap.claims[0]?.head ?? `${T[snap.away]?.place ?? snap.away} at ${T[snap.home]?.place ?? snap.home}`,
    note: n ? `${count(n, 'call')} to watch · graded after the game` : undefined,
  };
}

/** Post-game: the score, the story's headline and how the preview's calls did. */
export function finalOg(r: { start: string; away: string; home: string; venue: string; end: string; score: Record<string, number>; calls: { verdict: string | null }[]; story?: { head: string } | null; pending?: boolean }): OgCard {
  const graded = r.calls.filter((c) => c.verdict === 'held' || c.verdict === 'partly' || c.verdict === 'missed');
  const held = graded.filter((c) => c.verdict === 'held').length;
  return {
    kind: 'final', date: day(r.start), venue: r.venue, end: r.end, ...sides(r.away, r.home, () => undefined, r.score),
    head: r.story?.head ?? `${T[r.away]?.nick ?? r.away} ${r.score[r.away]}, ${T[r.home]?.nick ?? r.home} ${r.score[r.home]}`,
    note: r.pending ? 'Still coming in · sparelinechange.com' : graded.length ? `${held} of ${count(graded.length, 'call')} held · sparelinechange.com` : undefined,
  };
}

type Est = { pct: number | null; lo: number; hi: number; rank: number };
export type TeamLane = { label: string; rank: string; pct: number; lo: number; hi: number; ends: [string, string] };
/** A team's most distinctive traits, clear-of-the-middle first (shared with the team share card). */
export function teamLanes(abbr: string, mode: 'blend' | 'season', n = 6): TeamLane[] {
  const dims = (fpData.teams as unknown as Record<string, { dims: Record<string, Record<'blend' | 'season', Est | null>> }>)[abbr]?.dims ?? {};
  const ok = fpData.ready.filter((k) => dims[k]?.[mode]?.pct != null && fpInfo[k]).map((k) => ({ k, e: dims[k][mode]! }));
  const far = (x: { e: Est }) => Math.abs(x.e.pct! - 50);
  const clear = ok.filter((x) => x.e.lo >= 65 || x.e.hi <= 35).sort((a, b) => far(b) - far(a));
  const rest = ok.filter((x) => !clear.includes(x)).sort((a, b) => far(b) - far(a));
  return [...clear, ...rest].slice(0, n).map(({ k, e }) => ({ label: fpInfo[k].label, rank: ordinal(e.rank), pct: e.pct!, lo: e.lo, hi: e.hi, ends: [fpInfo[k].lo, fpInfo[k].hi] }));
}
/** Team: name, record and division place, the three style words, and the three most distinctive traits. */
export function teamOg(abbr: string): OgCard {
  const t = T[abbr]!;
  const lanes = teamLanes(abbr, 'blend');
  const top = [...lanes].sort((a, b) => Math.abs(b.pct - 50) - Math.abs(a.pct - 50)).slice(0, 3);
  return {
    kind: 'team', abbr, name: t.name, color: cardColours(abbr, abbr).own[abbr].light,
    kick: ['Team style', `${t.w}-${t.l}-${t.otl}`, t.div_rank ? `${ordinal(t.div_rank)} in the ${t.division}` : null].filter(Boolean).join(' · '),
    words: styleWords(lanes).map((w) => `${w}.`).join(' '),
    lanes: top.map((l) => ({ label: l.label, pct: l.pct, rank: l.rank, ends: l.ends })),
  };
}

/** Player: name, position and club, this season's line, and what he does better than most (goalies: save %). */
export function playerOg(p: any): OgCard {
  const team = T[p.team];
  const color = cardColours(p.team, p.team).own[p.team].light;
  const sub = [p.group === 'G' ? null : posWord[p.pos] ?? p.pos, team?.name ?? p.team, p.age ? String(p.age) : null].filter(Boolean).join(' · ');
  const base = { kind: 'player' as const, name: p.name, sub, number: p.number ?? null, color };
  if (p.group === 'G') {
    const g = goalieCard({ name: p.name, number: p.number, sub, team: p.team, goalie: p.goalie, asOf: '' });
    if (!g) return { ...base, kick: 'Goalie card', stats: [], lanes: [], note2: p.unplayed ? 'Hasn’t played this season.' : undefined };
    const sv = g.sv.filter((x) => x.label === 'All shots' || x.label === 'High-danger');
    return { ...base, kick: 'Goalie card · Blended', stats: g.stats, lanes: sv.map((x) => ({ label: `${x.label === 'All shots' ? 'Save %' : 'Dangerous shots'} ${x.val}`, pct: x.me, rank: x.rank })) };
  }
  const st = p.stats?.season;
  const stats: [string | number, string][] = st ? [[st.gp, 'GP'], [st.g, 'G'], [st.a, 'A'], [st.p, 'P'], [clock(st.toi), 'TOI']] : [];
  const best = p.traits ? standouts(p as Skater, 'blend').best.slice(0, 2) : [];
  return {
    ...base, kick: 'Player card', stats,
    lanes: best.map((x) => ({ label: x.info.label, pct: x.pct, rank: x.rank ? `${ordinal(x.rank)} of ${x.of}` : `${ordinal(x.pct)} pct`, ends: [x.info.lo, x.info.hi] as [string, string] })),
    note2: p.unplayed || !st ? 'Hasn’t played this season.' : undefined,
  };
}
