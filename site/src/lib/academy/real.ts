// Real numbers for the Academy, read at build time from the site's own data files (written by the nightly update), so
// lessons quote this season's league with nothing typed by hand. Pages embed what they need as JSON; the browser picks
// the visitor's team, or a league example when they haven't picked one.
import { playerHref } from '../playerLinks';
import { rows, seasons } from '../stats';

type Unit = { label: string; players: string[]; ids?: number[] };
type LinesFile = { teams: Record<string, { units: Unit[] }> };
type Person = { id: number; name: string; last: string; team: string; pos: string; number?: number };
type Team = { abbr: string; name: string; place: string; nick: string; league_rank?: number; pts?: number };

const glob = import.meta.glob('../../../public/data/{lines,teams}.json', { eager: true, import: 'default' }) as Record<string, unknown>;
const pick = <T,>(name: string) => Object.entries(glob).find(([f]) => f.endsWith(`/${name}.json`))?.[1] as T | undefined;
const people = import.meta.glob('../../../public/data/players/index.json', { eager: true, import: 'default' }) as Record<string, Person[]>;
const logs = import.meta.glob('../../../public/data/gamelog/teams.json', { eager: true, import: 'default' }) as Record<string, Record<string, unknown[][]>>;

const byId = new Map((Object.values(people)[0] ?? []).map((p) => [p.id, p]));
const teams = (pick<Team[]>('teams') ?? []).slice().sort((a, b) => a.abbr.localeCompare(b.abbr));
const POS: Record<string, string> = { C: 'Center', L: 'Left wing', R: 'Right wing', D: 'Defenseman', G: 'Goalie' };

export type Named = { name: string; pos: string; href: string | null };
const named = (id: number | undefined, fallback: string): Named => {
  const p = id != null ? byId.get(id) : undefined;
  return { name: p?.name ?? fallback, pos: POS[p?.pos ?? ''] ?? '', href: playerHref(id) };
};
// A trio in the order fans say it: left wing, center, right wing (when the three positions are there to sort by).
const ORDER: Record<string, number> = { 'Left wing': 0, Center: 1, 'Right wing': 2 };
const unit = (u: Unit) => {
  const ps = u.players.map((n, i) => named(u.ids?.[i], n));
  return ps.length === 3 && ps.every((p) => p.pos in ORDER) ? ps.sort((a, b) => ORDER[a.pos] - ORDER[b.pos]) : ps;
};

export type TeamLines = { abbr: string; nick: string; lines: Named[][]; pairs: Named[][]; goalie: Named | null };
/** Every club's usual lines and pairs (lines.json) and the goalie who started its last game (gamelog/teams.json). */
export function teamLines(): TeamLines[] {
  const lines = pick<LinesFile>('lines')?.teams ?? {};
  const log = Object.values(logs)[0] ?? {};
  return teams.flatMap((t) => {
    const us = lines[t.abbr]?.units ?? [];
    const L = us.filter((u) => /^L\d/.test(u.label)).map(unit), P = us.filter((u) => /^P\d/.test(u.label)).map(unit);
    if (!L.length || !P.length) return [];
    const last = log[t.abbr]?.[0];
    const gid = typeof last?.[7] === 'number' ? (last[7] as number) : undefined;
    return [{ abbr: t.abbr, nick: t.nick, lines: L, pairs: P, goalie: gid ? named(gid, '') : null }];
  });
}
/** The club shown when the visitor hasn't picked a team: the league leader. */
export const example = () => teams.slice().sort((a, b) => (a.league_rank ?? 99) - (b.league_rank ?? 99))[0]?.abbr ?? 'CAR';

/** This season's average NHL shift, in seconds (time on ice over shifts, all skaters). */
export function averageShift(): number | null {
  const s = seasons('skaters')[0];
  if (s == null) return null;
  let toi = 0, shifts = 0;
  for (const r of rows('skaters', s)) {
    const gp = Number(r.gp) || 0;
    toi += (Number(r.toi) || 0) * gp; shifts += (Number(r.shifts) || 0) * gp;
  }
  return shifts ? Math.round(toi / shifts) : null;
}

export type Leader = Named & { team: string; gp: number; g: number; a: number; p: number };
/** The league's points leaders this season (ties broken by goals). */
export function pointsLeaders(n = 5): Leader[] {
  const s = seasons('skaters')[0];
  if (s == null) return [];
  return rows('skaters', s)
    .map((r) => ({ ...named(Number(r.id), String(r.name)), team: String(r.team), gp: Number(r.gp), g: Number(r.g), a: Number(r.a), p: Number(r.p) }))
    .sort((a, b) => b.p - a.p || b.g - a.g || a.gp - b.gp)
    .slice(0, n);
}
