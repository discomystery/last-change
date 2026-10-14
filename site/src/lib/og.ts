// Link previews: the card each page shows when its link is pasted into Twitter/X, a text message, Slack or Discord.
// Pages build an OgCard here from the same data they draw from and pass it to Base, which writes it into the page
// (with the og:/twitter: tags pointing at /og/{page}.png); og/integration.mjs draws the PNGs after the build with
// og/render.mjs.
import teamsData from '../../public/data/teams.json';
import fpData from '../../public/data/fingerprints.json';
import { cardColours } from './teams';
import { fpInfo, ordinal } from './traits';
import { posWord } from './players';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

// The drawing code's own fingerprint, part of each image's ?v=, so a change of design reaches apps that cache previews.
export const OG_REV = (() => {
  try { return createHash('sha1').update(readFileSync(`${process.cwd()}/og/render.mjs`)).digest('hex').slice(0, 8); } catch { return ''; }
})();

type Side = { ab: string; nick: string; color: string; score?: number };
// Plain facts that don't change once the page exists (Sora, 2026-10-14: no headlines, records or ratings), so a link
// shows the same picture whenever it is shared.
export type OgCard =
  | { kind: 'preview' | 'final'; date: string; venue?: string; end?: string; away: Side; home: Side }
  | { kind: 'team'; abbr: string; name: string; kick?: string; color: string }
  | { kind: 'player'; name: string; kick: string; sub: string; number: number | null; color: string }
  | { kind: 'page'; kick?: string; title: string; text?: string }
  | { kind: 'home' };

const T = Object.fromEntries(teamsData.map((t) => [t.abbr, t]));
const day = (iso: string) => new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York' }).format(new Date(iso));

function sides(away: string, home: string, score?: Record<string, number>) {
  const cc = cardColours(away, home);
  const s = (a: string): Side => ({ ab: a, nick: T[a]?.nick ?? a, color: cc.matchup[a].light, ...(score ? { score: score[a] } : {}) });
  return { away: s(away), home: s(home) };
}

/** Preview: the matchup and the date. */
export function previewOg(snap: { start: string; away: string; home: string; venue?: string }): OgCard {
  return { kind: 'preview', date: day(snap.start), venue: snap.venue, ...sides(snap.away, snap.home) };
}

/** Post-game: the final score. */
export function finalOg(r: { start: string; away: string; home: string; venue: string; end: string; score: Record<string, number> }): OgCard {
  return { kind: 'final', date: day(r.start), venue: r.venue, end: r.end, ...sides(r.away, r.home, r.score) };
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
/** Team: the club's name and colour, its abbreviation big and faint behind. */
export function teamOg(abbr: string): OgCard {
  const t = T[abbr]!;
  return { kind: 'team', abbr, name: t.name, color: cardColours(abbr, abbr).own[abbr].light, kick: t.division ? `${t.division} Division` : undefined };
}

/** Player: name, position, club and sweater number. */
export function playerOg(p: any): OgCard {
  return {
    kind: 'player', name: p.name, kick: posWord[p.pos] ?? p.pos, sub: T[p.team]?.name ?? p.team,
    number: p.number ?? null, color: cardColours(p.team, p.team).own[p.team].light,
  };
}
