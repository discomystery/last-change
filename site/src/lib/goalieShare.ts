// The goalie share card's data (lib/shareCard.ts goalieCard), built at build time from goalies.json for one goalie:
// his blended stats, the league chart, save % by kind of shot, workload, rebound control and his nights. Used by the
// player page (SharePlayer.astro) and the team page's Goalies section (ShareGoalies.astro).
import { cardColours } from './teams';
import { ordinal } from './players';
import goalieData from '../../public/data/goalies.json';
import type { GoalieCardData, ScaleLane } from './shareCard';

type Args = { name: string; number: number | null; sub: string; team: string; goalie: { name: string; blend: any } | null | undefined; asOf: string };

export function goalieCard(a: Args): GoalieCardData | null {
  const g = a.goalie?.blend;
  if (!g || !(g.starts > 0)) return null;
  type G = { name: string; blend: any };
  const gd = goalieData as unknown as { min_starts: { blend: number }; league: { x: number; y: number; name: string; team: string }[]; teams: Record<string, G[]> };
  const mates = (gd.teams[a.team] ?? []).filter((x) => x.name !== a.goalie!.name && x.blend.xga60 != null && x.blend.gsax60 != null);
  // The league chart, scaled exactly as on team pages (Quadrant.astro): ranges from the league's goalies, the median
  // goalie's team in the middle across, zero up and down, the vertical range padded by 22% of its span. A goalie
  // outside the league's range (too few starts to be in it) is kept on the chart's edge.
  const lg = gd.league;
  const gx = lg.map((q) => q.x), gy = lg.map((q) => Math.abs(q.y));
  const x0 = Math.min(...gx) - 0.1, x1 = Math.max(...gx) + 0.1, yMax = Math.max(...gy) + 0.1;
  const xMid = [...gx].sort((a, b) => a - b)[Math.floor(lg.length / 2)];
  const yHi = yMax + 0.22 * 2 * yMax, yLo = -yHi;
  const edge = (v: number) => Math.min(100, Math.max(0, v));
  const X = (v: number) => edge(Math.round((1000 * (v - x0)) / (x1 - x0)) / 10), Y = (v: number) => edge(Math.round((1000 * (yHi - v)) / (yHi - yLo)) / 10);
  const lastName = (n: string) => n.split('. ').slice(-1)[0];
  const quad = g.xga60 != null && g.gsax60 != null ? {
    mid: [X(xMid), Y(0)] as [number, number],
    league: lg.filter((q) => q.team !== a.team).map((q) => [X(q.x), Y(q.y)] as [number, number]),
    mates: mates.map((m) => [X(m.blend.xga60), Y(m.blend.gsax60), lastName(m.name)] as [number, number, string]),
    me: [X(g.xga60), Y(g.gsax60), lastName(a.goalie!.name)] as [number, number, string],
  } : null;
  // Save % by kind of shot against every goalie with enough starts: league dots, teammates as rings, him as the dot.
  const pool = Object.entries(gd.teams).flatMap(([t, list]) => list.map((x) => ({ ...x, team: t }))).filter((x) => x.blend.starts >= gd.min_starts.blend);
  const svOf = (x: any, key: string) => (key === 'all' ? x.sv : x.tiers?.[key]?.sv) as number | null;
  const f3 = (v: number) => v.toFixed(3).replace(/^0/, '');
  const sv = ([['all', 'All shots'], ['low', 'Low-danger'], ['medium', 'Medium-danger'], ['high', 'High-danger']] as const).flatMap(([key, label]) => {
    const mine = svOf(g, key);
    if (mine == null) return [];
    const others = pool.filter((x) => !(x.team === a.team && x.name === a.goalie!.name)).map((x) => ({ v: svOf(x.blend, key), team: x.team })).filter((x) => x.v != null) as { v: number; team: string }[];
    const vals = [...others.map((x) => x.v), mine];
    const mu = vals.reduce((a, b) => a + b, 0) / vals.length;
    const span = Math.max(...vals.map((v) => Math.abs(v - mu))) * 1.04 || 1;
    const P = (v: number) => Math.round(10 * (50 + (50 * (v - mu)) / span)) / 10;
    return [{ label, val: f3(mine), rank: `${ordinal(1 + others.filter((x) => x.v > mine).length)} of ${vals.length}`,
      dots: others.filter((x) => x.team !== a.team).map((x) => P(x.v)), mates: others.filter((x) => x.team === a.team).map((x) => P(x.v)), me: P(mine) }];
  });
  const pct = (k: string) => g.pct?.[k] as number | null | undefined;
  const lane = (k: string, label: string, ends: [string, string]): ScaleLane[] => (pct(k) == null ? [] : [{ label, rank: `${ordinal(pct(k)!)} pct`, pct: pct(k)!, ends }]);
  const b = g.bins as number[];
  const sg = (v: number) => (Math.abs(v) < 0.05 ? '0.0' : `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}`);
  return {
    name: a.name, number: a.number, sub: a.sub, own: cardColours(a.team, a.team).own[a.team], asOf: a.asOf,
    stats: [[g.starts, 'Starts'], [g.sv == null ? '–' : f3(g.sv), 'Sv%'], [sg(g.gsax), 'GSAx'], ...(g.qs == null ? [] : [[`${g.qs}%`, 'Quality starts'] as [string, string]])],
    quad, sv,
    work: [...lane('busy', 'How busy he is', ['Quiet', 'Under siege']), ...lane('danger', 'Shot danger', ['Sheltered', 'Exposed'])],
    reb: lane('rebounds', 'Rebound control', ['Loose', 'Smothers it'])[0] ?? null,
    night: b?.length === 6 ? [b[0] + b[1], b[2] + b[3], b[4] + b[5]] : null, starts: g.starts,
  };
}
