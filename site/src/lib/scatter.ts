// League scatter charts: all 32 teams on two measures at once, one dot each, named by abbreviation. Worked out when
// the page is built (points, scales, where each name goes) and drawn as one SVG string, so the page
// (components/TeamScatter.astro) and the share card (lib/shareCard.ts scatterCard) draw exactly the same chart.
// Better is always up and to the right.
import teamsData from '../../public/data/teams.json';
import fpData from '../../public/data/fingerprints.json';
import { teamColour, darkShade } from './teams';
import { ordinal } from './format';
import { W0, H0, BL, BB, FONT, CHAR, type Mode, type Pt, type View, type Chart } from './scatterSvg';
export { scatterSvg, type Mode, type View, type Chart } from './scatterSvg';

type Raw = { t: string; x: number; y: number; tip: string };
type Axis = { lowBetter?: boolean };

/** Places 32 teams: scales with room for the corner names, then puts each abbreviation beside its dot where it fits. */
function layout(rows: Raw[], ax: { x: Axis; y: Axis }, corners: View['corners'], xAxis: string, yAxis: string, from: string, size: [number, number] = [W0, H0]): View {
  const [W, H] = size, PH = H - BB;
  const xs = rows.map((r) => r.x), ys = rows.map((r) => r.y);
  const span = (v: number[]) => Math.max(...v) - Math.min(...v) || 1;
  const xPad = span(xs) * 0.08, yPad = span(ys) * 0.24;
  let [x0, x1] = [Math.min(...xs) - xPad, Math.max(...xs) + xPad];
  let [y0, y1] = [Math.min(...ys) - yPad, Math.max(...ys) + yPad];
  if (ax.x.lowBetter) [x0, x1] = [x1, x0];
  if (ax.y.lowBetter) [y0, y1] = [y1, y0];
  const mean = (v: number[]) => v.reduce((a, b) => a + b, 0) / v.length;
  const X = (v: number) => +(BL + ((v - x0) / (x1 - x0)) * (W - BL)).toFixed(1);
  const Y = (v: number) => +(PH - ((v - y0) / (y1 - y0)) * PH).toFixed(1);
  type Box = { x0: number; x1: number; y0: number; y1: number };
  const hit = (a: Box, b: Box) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
  const area = (a: Box, b: Box) => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  const pts = rows.map((r) => ({ ...r, px: X(r.x), py: Y(r.y) }));
  const dots: Box[] = pts.map((p) => ({ x0: p.px - 5.5, x1: p.px + 5.5, y0: p.py - 5.5, y1: p.py + 5.5 }));
  // The corner names take two lines.
  const cw = (s: string) => Math.max(...s.split('|').map((l) => l.length)) * 6.4 + 4;
  const blocked: Box[] = [
    { x0: BL, x1: BL + 10 + cw(corners[0]), y0: 0, y1: 36 }, { x0: W - 10 - cw(corners[1]), x1: W, y0: 0, y1: 36 },
    { x0: BL, x1: BL + 10 + cw(corners[2]), y0: PH - 36, y1: PH }, { x0: W - 10 - cw(corners[3]), x1: W, y0: PH - 36, y1: PH },
  ];
  // Crowded dots pick their spot first.
  const near = (i: number) => pts.filter((q, j) => j !== i && Math.hypot(q.px - pts[i].px, q.py - pts[i].py) < 30).length;
  const order = pts.map((_, i) => i).sort((a, b) => near(b) - near(a));
  const placed: Box[] = [];
  const leads: [number, number, number, number][] = [];
  const out: Pt[] = new Array(pts.length);
  for (const i of order) {
    const p = pts[i], w = p.t.length * CHAR, h = FONT;
    // Beside the dot first, then above, below and the diagonals, then the same a step further out.
    const at = (dx: number, dy: number, a: Pt['a']) => {
      const lx = p.px + dx, x0 = a === 'start' ? lx : a === 'end' ? lx - w : lx - w / 2;
      return { lx, ly: p.py + dy + 3.5, a, far: Math.hypot(dx, dy), b: { x0, x1: x0 + w, y0: p.py + dy - h / 2, y1: p.py + dy + h / 2 } };
    };
    const tries = [1, 1.9].flatMap((k) => [
      at(7 * k, 0, 'start'), at(-7 * k, 0, 'end'), at(0, -11 * k, 'middle'), at(0, 11 * k, 'middle'),
      at(5 * k, -9 * k, 'start'), at(5 * k, 9 * k, 'start'), at(-5 * k, -9 * k, 'end'), at(-5 * k, 9 * k, 'end'),
    ]);
    const inside = (b: Box) => b.x0 >= BL + 2 && b.x1 <= W - 2 && b.y0 >= 2 && b.y1 <= PH - 2;
    const cost = (b: Box) => (inside(b) ? 0 : 1e6) + placed.reduce((s, o) => s + area(o, b), 0) * 3
      + dots.reduce((s, d, j) => s + (j === i ? 0 : area(d, b)), 0) * 2 + blocked.reduce((s, o) => s + (hit(o, b) ? area(o, b) * 0.5 : 0), 0);
    // A name a step out gets a line from the edge of its dot to the nearest edge of the name.
    const leadOf = (b: Box): [number, number, number, number] => {
      const nx = Math.min(Math.max(p.px, b.x0), b.x1), ny = Math.min(Math.max(p.py, b.y0), b.y1);
      const d = Math.hypot(nx - p.px, ny - p.py) || 1, r = 5.5 / d;
      return [+(p.px + (nx - p.px) * r).toFixed(1), +(p.py + (ny - p.py) * r).toFixed(1), +(nx - (nx - p.px) * (1.5 / d)).toFixed(1), +(ny - (ny - p.py) * (1.5 / d)).toFixed(1)];
    };
    // A line may not run through another name or dot: it is checked at a few points along its length.
    const crosses = (l: [number, number, number, number]) => [0.2, 0.4, 0.6, 0.8, 1].reduce((n, k) => {
      const x = l[0] + (l[2] - l[0]) * k, y = l[1] + (l[3] - l[1]) * k;
      const inBox = (o: Box) => x > o.x0 && x < o.x1 && y > o.y0 && y < o.y1;
      return n + placed.filter(inBox).length + dots.filter((o, j) => j !== i && inBox(o)).length;
    }, 0);
    let best = tries[0], bc = Infinity;
    for (const t of tries) {
      const c = cost(t.b) + (t.far > 12 ? 4 + 40 * crosses(leadOf(t.b)) : 0) + leads.reduce((n, l) => n + ([0.25, 0.5, 0.75].some((k) => { const x = l[0] + (l[2] - l[0]) * k, y = l[1] + (l[3] - l[1]) * k; return x > t.b.x0 && x < t.b.x1 && y > t.b.y0 && y < t.b.y1; }) ? 40 : 0), 0);
      if (c < bc - 0.01) { best = t; bc = c; }
      if (c === 0) break;
    }
    placed.push(best.b);
    const lead = best.far > 12 ? leadOf(best.b) : undefined;
    if (lead) leads.push(lead);
    out[i] = { t: p.t, x: p.x, y: p.y, tip: p.tip, px: p.px, py: p.py, lx: +best.lx.toFixed(1), ly: +best.ly.toFixed(1), a: best.a, lead };
  }
  return { corners, xAxis, yAxis, mid: [X(mean(xs)), Y(mean(ys))], pts: out, from, ...(W !== W0 || H !== H0 ? { w: W, h: H } : {}) };
}

/** Each club's own colour for both card themes (share cards draw outside the page's light-dark CSS). */
export const cardPaints = () => Object.fromEntries((teamsData as { abbr: string }[]).map((t) => [t.abbr, { light: teamColour(t.abbr), dark: darkShade(teamColour(t.abbr), t.abbr) }]));

// ---------- Special teams: power play against penalty kill ----------
type Est = { v: number; rank: number };
const fp = fpData.teams as unknown as Record<string, { dims: Record<string, Record<Mode, Est>> }>;
const place = Object.fromEntries((teamsData as { abbr: string; place: string }[]).map((t) => [t.abbr, t.place]));
// The league's official team totals (pipeline/export/stats.py); a checkout without them simply has no goals view.
type Stats = { cols: string[]; seasons: Record<string, (string | number)[][]> };
const stats = Object.values(import.meta.glob('../../public/data/stats/teams.json', { eager: true, import: 'default' }))[0] as Stats | undefined;

function chances(m: Mode, size?: [number, number]): View {
  const rows = Object.keys(place).flatMap((t) => {
    const pp = fp[t]?.dims.pp?.[m], pk = fp[t]?.dims.pk?.[m];
    if (!pp || !pk) return [];
    return [{ t, x: pp.v, y: pk.v,
      tip: `${place[t]}: their power play creates ${pp.v.toFixed(1)} expected goals per 60 (${ordinal(pp.rank)} of 32), and their penalty kill allows ${pk.v.toFixed(1)} (${pk.rank === 1 ? 'the fewest' : `${ordinal(pk.rank)} fewest`}).` }];
  });
  const from = m === 'blend' ? 'Expected goals per 60 at 5-on-4 and 4-on-5, this season blended with last.' : 'Expected goals per 60 at 5-on-4 and 4-on-5, this season only.';
  return layout(rows, { x: {}, y: { lowBetter: true } }, ['Kill carries|them', 'Strong|both ways', 'Struggling|both ways', 'Power play|carries them'],
    'Power play: fewer chances ← → more', 'Kill: allows more ← → fewer', from, size);
}

function goals(stats: Stats, m: Mode, size?: [number, number]): View {
  const c = (k: string) => stats.cols.indexOf(k);
  const seasons = Object.keys(stats.seasons).sort();
  const use = m === 'blend' ? seasons.slice(-2) : seasons.slice(-1);
  const sum: Record<string, { ppg: number; ppo: number; ppga: number; tsh: number }> = {};
  for (const s of use) for (const r of stats.seasons[s]) {
    const t = String(r[c('team')]);
    if (!place[t]) continue;
    const o = (sum[t] ??= { ppg: 0, ppo: 0, ppga: 0, tsh: 0 });
    o.ppg += +r[c('ppg')]; o.ppo += +r[c('ppo')]; o.ppga += +r[c('ppga')]; o.tsh += +r[c('tsh')];
  }
  const raw = Object.entries(sum).filter(([, o]) => o.ppo > 0 && o.tsh > 0).map(([t, o]) => ({ t, o, x: (100 * o.ppg) / o.ppo, y: 100 - (100 * o.ppga) / o.tsh }));
  const rank = (k: 'x' | 'y') => { const s = [...raw].sort((a, b) => b[k] - a[k]); return (t: string) => 1 + s.findIndex((r) => r[k] === raw.find((q) => q.t === t)![k]); };
  const rx = rank('x'), ry = rank('y');
  const rows = raw.map(({ t, o, x, y }) => ({ t, x, y,
    tip: `${place[t]}: power play ${x.toFixed(1)}% (${o.ppg} goals on ${o.ppo} power plays, ${ordinal(rx(t))}), penalty kill ${y.toFixed(1)}% (${o.ppga} allowed on ${o.tsh} times shorthanded, ${ordinal(ry(t))}).` }));
  const from = m === 'blend' ? 'The league’s official power-play and penalty-kill percentages, this season and last added together.' : 'The league’s official power-play and penalty-kill percentages, this season only.';
  return layout(rows, { x: {}, y: {} }, ['Kill carries|them', 'Strong|both ways', 'Struggling|both ways', 'Power play|carries them'],
    'Power play %: lower ← → higher', 'Penalty kill %: lower ← → higher', from, size);
}

const special: Record<string, Chart> = {};
/** `size` draws the chart wider than the usual square (in chart units). */
export const specialTeams = (size?: [number, number]): Chart => (special[String(size ?? '')] ??= {
  key: 'special', title: 'Power play against penalty kill', kick: 'Special teams',
  sub: 'Every team’s power play against its penalty kill',
  metrics: [
    { key: 'chances', word: 'chances', views: { blend: chances('blend', size), season: chances('season', size) } },
    ...(stats ? [{ key: 'goals', word: 'goals', views: { blend: goals(stats, 'blend', size), season: goals(stats, 'season', size) } }] : []),
  ],
});
