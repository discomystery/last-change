// The career ledger on player pages (Sora, 2026-10-15): every NHL season he has played, regular season and playoffs,
// from pipeline/export/ledger.py. Seasons split by a trade are combined into one line with each club's line kept
// underneath; highs, totals and per-82 figures are worked out here so the page and the data stay small.
import type { Row } from './statDefs';

export type Ledger = { cols: string[]; rows: (string | number | null)[][]; awards: { name: string; short: string; yrs: number[] }[]; complete: boolean };
export type Season = Row & { yr: number; teams: string[]; main: string; parts: Row[] };

const files = import.meta.glob('../../public/data/careers/*.json', { eager: true, import: 'default' }) as Record<string, Ledger>;
const byId = new Map(Object.entries(files).map(([f, v]) => [Number(f.split('/').pop()!.replace('.json', '')), v]));
export const ledgerOf = (id: number) => byId.get(id);

const SUM = ['gp', 'g', 'a', 'p', 'pm', 'pim', 'ppg', 'ppp', 'shg', 'shp', 'gwg', 'otg', 's', 'gs', 'w', 'l', 'otl', 'sa', 'ga', 'so'];

/** Add up club lines (or seasons) into one line, with the rates worked out from the totals. */
export function combine(parts: Row[], goalie: boolean): Row {
  const r: Row = {};
  for (const k of SUM) if (parts.some((p) => p[k] != null)) r[k] = parts.reduce((t, p) => t + (p[k] ?? 0), 0);
  const gp = r.gp || 0;
  if (goalie) {
    r.toi = parts.reduce((t, p) => t + (p.toi ?? 0), 0);
    r.sv = r.sa != null && r.ga != null ? r.sa - r.ga : null;
    r.svpct = r.sa ? r.sv / r.sa : null;
    r.gaa = r.toi ? (r.ga * 3600) / r.toi : null;
  } else {
    const timed = parts.filter((p) => p.toi != null);
    const tgp = timed.reduce((t, p) => t + p.gp, 0);
    r.toi = tgp ? timed.reduce((t, p) => t + p.toi * p.gp, 0) / tgp : null;
    // the NHL gives a faceoff share per season, not the draws, so seasons are weighted by games; 0 means he took none
    const fo = parts.filter((p) => p.fopct);
    const fgp = fo.reduce((t, p) => t + p.gp, 0);
    r.fopct = fgp ? fo.reduce((t, p) => t + p.fopct * p.gp, 0) / fgp : null;
    r.spct = r.s ? r.g / r.s : null;
  }
  return r;
}

/** His seasons of one game type (2 regular season, 3 playoffs), latest first. */
export function seasons(l: Ledger, gt: 2 | 3, goalie: boolean): Season[] {
  const by = new Map<number, Row[]>();
  for (const raw of l.rows) {
    const r: Row = Object.fromEntries(l.cols.map((c, i) => [c === 'fo' ? 'fopct' : c, raw[i]]));
    if (r.gt !== gt) continue;
    if (!by.has(r.yr)) by.set(r.yr, []);
    by.get(r.yr)!.push(r);
  }
  return [...by.entries()].sort((a, b) => b[0] - a[0]).map(([yr, parts]) => {
    const teams = parts.flatMap((p) => String(p.team).split(', '));
    const main = parts.slice().sort((a, b) => b.gp - a.gp)[0].team.split(', ')[0];
    return { ...combine(parts, goalie), yr, teams, main, parts: parts.length > 1 ? parts : [] };
  });
}

/** Trophies of a season for one game type: the Cup on both, the Conn Smythe with the playoffs, the rest with the
 *  regular season. */
export function awardsIn(l: Ledger, yr: number, gt: 2 | 3) {
  const all = l.awards.filter((a) => a.yrs.includes(yr));
  const cup = all.some((a) => a.name === 'Stanley Cup');
  const other = all.filter((a) => a.name !== 'Stanley Cup' && (a.short === 'Conn Smythe') === (gt === 3));
  return { cup, other };
}
