// Subtle team colours for two-team pages. Many clubs share reds and navies, so when the two colours in a game are
// too close the visitors fall back to a neutral slate. Dark mode lightens whichever colour is used (see .t-away/.t-home).
const COLOURS: Record<string, string> = {
  ANA: '#d9531e', BOS: '#a57b00', BUF: '#003087', CGY: '#c8102e', CAR: '#c8102e', CHI: '#cf0a2c', COL: '#6f263d', CBJ: '#002654',
  DAL: '#006847', DET: '#ce1126', EDM: '#1f4e9e', FLA: '#c8102e', LAK: '#4b4f54', MIN: '#154734', MTL: '#af1e2d', NSH: '#b58500',
  NJD: '#ce1126', NYI: '#00539b', NYR: '#0038a8', OTT: '#c52032', PHI: '#d9480f', PIT: '#b38300', SJS: '#006d75', SEA: '#0e5a6e',
  STL: '#002f87', TBL: '#002868', TOR: '#00205b', UTA: '#3a7dbb', VAN: '#00205b', VGK: '#8c7442', WSH: '#c8102e', WPG: '#1b3a6b',
};
const FALLBACK = '#6b7785';
const MIN_DISTANCE = 0.12; // in OKLab units; below this two dots are hard to tell apart

function oklab(hex: string): [number, number, number] {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  const [r, g, b] = c;
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}

export const teamColour = (abbr: string) => COLOURS[abbr] ?? FALLBACK;

/** Colours for a matchup: the home team keeps its own; the visitors give way if the two are too alike. */
export function matchupColours(away: string, home: string): { away: string; home: string } {
  const h = teamColour(home);
  let a = teamColour(away);
  const [x, y] = [oklab(a), oklab(h)];
  if (Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]) < MIN_DISTANCE) a = FALLBACK;
  return { away: a, home: h };
}
