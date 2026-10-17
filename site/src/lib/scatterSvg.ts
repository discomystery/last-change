// The league scatter chart's drawing (lib/scatter.ts works out the points): kept apart from the data so the share
// card, which loads in the browser, draws the same SVG without carrying the season's numbers.
export type Mode = 'blend' | 'season';
/** px/py: the dot; lx/ly: its name; `lead`: a short line from the dot to a name that had to sit further out. */
export type Pt = { t: string; x: number; y: number; tip: string; px: number; py: number; lx: number; ly: number; a: 'start' | 'end' | 'middle'; lead?: [number, number, number, number] };
export type View = {
  /** Corner names, top left / top right / bottom left / bottom right, each "line one|line two". */
  corners: [string, string, string, string];
  xAxis: string; yAxis: string;
  /** Where the league-average lines cross, in chart units. */
  mid: [number, number];
  pts: Pt[];
  /** One line under the chart saying what it is from. */
  from: string;
  /** The chart's size in its own units (330 x 320 unless a page asks for a wider one). */
  w?: number; h?: number;
};
export type Chart = { key: string; title: string; kick: string; sub: string; metrics: { key: string; word: string; views: Record<Mode, View> }[] };

// The chart's own units: a 330 x 320 box, the bottom 22 for the across label and the left 18 for the up label.
export const W0 = 330, H0 = 320;
export const BL = 18, BB = 22;
export const FONT = 10, CHAR = 6.6;

/** The chart as SVG. `hi` names one team to stand out; `paint` gives each team's dot colour (CSS values). */
export function scatterSvg(v: View, paint: (t: string) => string, hi: string | null = null, label = 'League chart') {
  const W = v.w ?? W0, H = v.h ?? H0, PH = H - BB;
  // Corner names come as "first line|second line".
  const two = (s: string) => [...s.split('|'), ''].slice(0, 2);
  const corner = (x: number, y: number, a: string, s: string, up: boolean) => {
    const [l1, l2] = two(s);
    return `<text class="sx-corner" x="${x}" y="${up ? y : y - 12}" text-anchor="${a}"><tspan x="${x}">${l1}</tspan><tspan x="${x}" dy="12">${l2}</tspan></text>`;
  };
  const order = [...v.pts].sort((a, b) => (a.t === hi ? 1 : 0) - (b.t === hi ? 1 : 0));
  return `<svg class="sx" viewBox="0 0 ${W} ${H}" role="img" aria-label="${label}">
<rect class="sx-box" x="${BL}" y="0" width="${W - BL}" height="${PH}" rx="12"/>
<line class="sx-avg" x1="${BL}" x2="${W}" y1="${v.mid[1]}" y2="${v.mid[1]}"/><line class="sx-avg" x1="${v.mid[0]}" x2="${v.mid[0]}" y1="0" y2="${PH}"/>
${corner(BL + 10, 18, 'start', v.corners[0], true)}${corner(W - 10, 18, 'end', v.corners[1], true)}${corner(BL + 10, PH - 10, 'start', v.corners[2], false)}${corner(W - 10, PH - 10, 'end', v.corners[3], false)}
<text class="sx-axis" x="${(BL + W) / 2}" y="${H - 6}" text-anchor="middle">${v.xAxis}</text>
<text class="sx-axis" transform="translate(11 ${PH / 2}) rotate(-90)" text-anchor="middle">${v.yAxis}</text>
${order.map((p) => `<g class="sx-pt${p.t === hi ? ' on' : ''}" data-t="${p.t}" data-tip="${p.tip.replace(/"/g, '&quot;')}" tabindex="0"><circle class="sx-hit" cx="${p.px}" cy="${p.py}" r="9"/>${p.lead ? `<line class="sx-lead" x1="${p.lead[0]}" y1="${p.lead[1]}" x2="${p.lead[2]}" y2="${p.lead[3]}"/>` : ''}<circle class="sx-dot" cx="${p.px}" cy="${p.py}" r="4" style="fill:${paint(p.t)}"/><text class="sx-name" x="${p.lx}" y="${p.ly}" text-anchor="${p.a}">${p.t}</text></g>`).join('')}
</svg>`;
}

