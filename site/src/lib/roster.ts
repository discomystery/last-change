// Full rosters and who is out, from roster.json (pipeline/export/roster.py). Words for each status live here so the
// team page, previews and post-game pages say the same thing. We never say why a player is out unless NHL.com did.
import data from '../../public/data/roster.json';

const files = import.meta.glob('../../public/data/players/*.json', { eager: true, import: 'default' }) as Record<string, any>;
const byId = new Map<number, any>(Object.values(files).filter((p: any) => p?.id).map((p: any) => [p.id, p]));

export type Report = { status: string; injury: string | null; timeline: string | null; date: string; url: string };
export type RosterPlayer = { id: number; name: string; pos: string | null; number: number | null; status: string; missed: number; last: string | null; report: Report | null };
export type Row = RosterPlayer & { group: 'F' | 'D' | 'G'; out: boolean; tag: string; tip: string; line: string; toi: number | null; page: boolean };

export const asOf: string = (data as any).date;
const months = ['Jan.', 'Feb.', 'March', 'April', 'May', 'June', 'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.'];
export const day = (iso: string) => { const [, m, d] = iso.split('-').map(Number); return `${months[m - 1]} ${d}`; };

const REPORT_TAG: Record<string, string> = {
  season: 'Out for the season', long: 'Out long term', ir: 'Injured reserve', week: 'Week to week', day: 'Day to day',
  out: 'Out', suspended: 'Suspended', personal: 'Personal leave',
};
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);
const games = (n: number) => `${n} game${n === 1 ? '' : 's'}`;

/** The short tag and its tooltip for one player's status. */
export function describe(p: RosterPlayer): { tag: string; tip: string; out: boolean } {
  const r = p.report;
  const missed = p.missed > 0 ? ` Has missed ${games(p.missed)} in a row.` : '';
  const said = r ? ` NHL.com Status Report, ${day(r.date)}.` : '';
  switch (p.status) {
    case 'injured': {
      const tag = r?.status === 'long' && r.timeline ? cap(r.timeline) : REPORT_TAG[r?.status ?? 'out'];
      return { tag, out: true, tip: `${tag}${r?.injury ? `, ${r.injury}` : ''}.${missed}${said}` };
    }
    case 'suspended': return { tag: 'Suspended', out: true, tip: `Suspended.${missed}${said}` };
    case 'personal': return { tag: 'Personal leave', out: true, tip: `Away for personal or family reasons.${missed}${said}` };
    case 'scratched': return { tag: 'Scratched', out: true, tip: `On the game-day roster but did not dress. The league does not say why, and no injury has been reported.${missed}` };
    case 'off_roster': return { tag: 'Not in the lineup', out: true, tip: `Listed by the club but left off the game-day roster, which usually means injured reserve. No injury has been reported.${missed}` };
    case 'not_listed': return { tag: 'Off the NHL roster', out: true, tip: `The club holds his rights but its NHL roster does not list him: usually in the minors, sometimes a long-term injury. Nothing has been reported.${missed}` };
    case 'no_club': return { tag: 'No NHL club', out: false, tip: 'A regular here last season. No NHL club lists him now, and he has not played this season. The data cannot say why (unsigned, retired or playing abroad).' };
    default: return { tag: '', out: false, tip: '' };
  }
}

const groupOf = (pos: string | null): 'F' | 'D' | 'G' => (pos === 'D' ? 'D' : pos === 'G' ? 'G' : 'F');

/** Everyone with the club today, with season lines from the player pages where there is one. */
export function roster(abbr: string): { rows: Row[]; gone: Row[]; moves: any[] } {
  const list: RosterPlayer[] = (data as any).teams[abbr] ?? [];
  const order: string[] = (data as any).order;
  const rows: Row[] = list.map((p) => {
    const page = byId.get(p.id);
    const group = groupOf(p.pos ?? page?.pos ?? null);
    const s = page?.stats?.season;
    const gs = page?.goalie?.season;
    const line = group === 'G'
      ? (gs?.games ? `${gs.games} GP · ${(gs.sv ?? 0).toFixed(3).replace(/^0/, '')}` : '')
      : (s?.gp ? `${s.gp} GP · ${s.g} G · ${s.a} A` : '');
    return { ...p, ...describe(p), group, line, toi: s?.gp ? s.toi : null, page: !!page };
  });
  const rank = (r: Row) => order.indexOf(r.status);
  const sorted = rows.sort((a, b) => (b.toi ?? -1) - (a.toi ?? -1) || rank(a) - rank(b) || a.name.localeCompare(b.name));
  return { rows: sorted.filter((r) => r.status !== 'no_club'), gone: sorted.filter((r) => r.status === 'no_club'), moves: (data as any).moves[abbr] ?? [] };
}
