// Subtle team colours for two-team pages. Many clubs share reds and navies, so when the two colours in a game are
// too close the visitors switch to a second colour from their own palette, and to a neutral slate only if that is too
// close as well. Dark mode has its own shades of the same colours (see `darkShade` and `paint`).
const COLOURS: Record<string, string> = {
  ANA: '#d9531e', BOS: '#a57b00', BUF: '#003087', CGY: '#c8102e', CAR: '#c8102e', CHI: '#cf0a2c', COL: '#6f263d', CBJ: '#002654',
  DAL: '#006847', DET: '#ce1126', EDM: '#1f4e9e', FLA: '#c8102e', LAK: '#4b4f54', MIN: '#154734', MTL: '#af1e2d', NSH: '#b58500',
  NJD: '#ce1126', NYI: '#00539b', NYR: '#0038a8', OTT: '#c52032', PHI: '#d9480f', PIT: '#b38300', SJS: '#006d75', SEA: '#0e5a6e',
  STL: '#002f87', TBL: '#002868', TOR: '#00205b', UTA: '#3a7dbb', VAN: '#00205b', VGK: '#8c7442', WSH: '#c8102e', WPG: '#1b3a6b',
};
// Second colours, from each club's own palette and muted to sit beside the first. Clubs that wear one colour plus
// white get black, grey or a lighter shade of their blue.
const SECOND: Record<string, string> = {
  ANA: '#a08a55', BOS: '#3a3a3a', BUF: '#c9961a', CGY: '#c99a2e', CAR: '#3a3a3a', CHI: '#3a3a3a', COL: '#2f6a9a', CBJ: '#c8102e',
  DAL: '#5c6670', DET: '#6b7785', EDM: '#e0602a', FLA: '#b9975b', LAK: '#8a9196', MIN: '#a6192e', MTL: '#22307a', NSH: '#1f3a5f',
  NJD: '#3a3a3a', NYI: '#e0702a', NYR: '#c8102e', OTT: '#b79257', PHI: '#3a3a3a', PIT: '#3a3a3a', SJS: '#d9731a', SEA: '#c8384a',
  STL: '#d6a020', TBL: '#3a3a3a', TOR: '#5b84c4', UTA: '#3a3a3a', VAN: '#1f7a4a', VGK: '#4b5a63', WSH: '#1f3a6b', WPG: '#ac162c',
};
const FALLBACK = '#6b7785';
// In OKLab units; below this two dots are hard to tell apart in light mode (0.12 until 2026-10-11 let Vancouver and
// Edmonton through).
const MIN_DISTANCE = 0.18;

// DARK MODE (2026-10-12). Mixing with white washed every colour out by the same 45% (reds turned salmon, black turned
// a warm smoky grey). Instead each colour keeps its hue and its full strength and only gets lighter, to one level per
// colour family so the two teams read equally bold: burgundy 0.63, reds 0.66, oranges 0.72, golds 0.80, everything else
// 0.70 (OKLab lightness; never darker than the light-mode colour). All of them clear 4.5:1 contrast on the dark page.
// Black can't be seen on a dark page, so a club's black turns into its white road sweater (Carolina and Los Angeles
// into their own silver). Dark mode picks the visitors' colour on its own, because lifting navies and royal blues to
// the same lightness brings them closer together: their first colour, then their second, then road white, then slate.
const ROAD_WHITE = '#c9d1d9';
const BLACK = '#3a3a3a';
const DARK_SWAP: Record<string, [string, string]> = { CAR: [BLACK, '#a2aaad'], LAK: ['#4b4f54', '#a2aaad'] };
const DARK_MIN_DISTANCE = 0.15;

function oklab(hex: string): [number, number, number] {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  const [r, g, b] = c;
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}

export const teamColour = (abbr: string) => COLOURS[abbr] ?? FALLBACK;

function fromOklab([L, a, b]: [number, number, number]): [number, number, number] {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
}
const toHex = (rgb: number[]) => '#' + rgb.map((v) => Math.round(255 * Math.min(1, Math.max(0, v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055))).toString(16).padStart(2, '0')).join('');

/** A colour's dark-mode shade: same hue and strength, lifted to its family's lightness (see DARK MODE above). */
export function darkShade(hex: string, abbr?: string): string {
  if (abbr && DARK_SWAP[abbr]?.[0] === hex) hex = DARK_SWAP[abbr][1];
  else if (hex === BLACK) hex = ROAD_WHITE;
  const [L0, a, b] = oklab(hex);
  const c = Math.hypot(a, b), h = (Math.atan2(b, a) * 180) / Math.PI + (b < 0 ? 360 : 0);
  const gold = h >= 60 && h < 115;
  const L = Math.max(L0, gold ? 0.8 : h >= 35 && h < 60 ? 0.72 : h < 15 || h >= 330 ? 0.63 : h < 35 ? 0.66 : 0.7);
  // Deep greens, teals and navies get a little more strength so they don't turn sage or dusty once lifted; greys and
  // golds keep their own. Then keep as much strength as the screen can show at that lightness.
  const want = c >= 0.05 && !gold ? Math.max(c, 0.11) : c;
  for (let k = want; k >= 0; k -= 0.002) {
    const rgb = fromOklab([L, (a / (c || 1)) * k, (b / (c || 1)) * k]);
    if (rgb.every((v) => v >= -1e-4 && v <= 1 + 1e-4)) return toHex(rgb);
  }
  return toHex(fromOklab([L, 0, 0]));
}

const both = (light: string, dark: string) => `light-dark(${light}, ${dark})`;
/** A club's own colour as a CSS value that follows light and dark mode. */
export const teamPaint = (abbr: string) => both(teamColour(abbr), darkShade(teamColour(abbr), abbr));

const distance = (a: string, b: string) => {
  const [x, y] = [oklab(a), oklab(b)];
  return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
};

/** Colours for a matchup: the home team keeps its own; the visitors switch to their second colour if the two are too
 * alike, and to slate if that is too alike as well. */
export function matchupColours(away: string, home: string): { away: string; home: string } {
  const h = teamColour(home);
  const a = [teamColour(away), SECOND[away], FALLBACK].find((c) => c && distance(c, h) >= MIN_DISTANCE) ?? FALLBACK;
  return { away: a, home: h };
}

function darkAway(away: string, home: string): string {
  const h = darkShade(teamColour(home), home);
  const options = [darkShade(teamColour(away), away), SECOND[away] && darkShade(SECOND[away], away), ROAD_WHITE, darkShade(FALLBACK)].filter(Boolean) as string[];
  return options.find((c) => distance(c, h) >= DARK_MIN_DISTANCE) ?? options.reduce((x, y) => (distance(x, h) >= distance(y, h) ? x : y));
}

/** `matchupColours` as CSS values that follow light and dark mode. */
export function matchupPaint(away: string, home: string): { away: string; home: string } {
  return { away: both(matchupColours(away, home).away, darkAway(away, home)), home: teamPaint(home) };
}

/** Colours for score cards and ticket stubs (Sora, 2026-10-16): the abbreviations there are labelled text, so a clash
 * only switches the visitors to their second colour when that is a real colour (not black, grey or white) and clears
 * the same bar as the matchup colours; otherwise both teams keep their own colour (PHI orange beside DET red), never the
 * grey, road white or slate fallbacks. Charts that tell teams apart by colour alone use `matchupPaint`. */
const isGrey = (hex: string) => { const [, a, b] = oklab(hex); return Math.hypot(a, b) < 0.03; };
function cardPaint(away: string, home: string): string {
  const second = SECOND[away] && !isGrey(SECOND[away]) ? SECOND[away] : null;
  const own = teamColour(away), h = teamColour(home);
  const light = distance(own, h) >= MIN_DISTANCE || !second || distance(second, h) < MIN_DISTANCE ? own : second;
  const ownD = darkShade(own, away), hD = darkShade(h, home), secD = second && darkShade(second, away);
  const dark = distance(ownD, hD) >= DARK_MIN_DISTANCE || !secD || distance(secD, hD) < DARK_MIN_DISTANCE ? ownD : secD;
  return both(light, dark);
}

/** `matchupTable` for score cards: each club's own colour, plus the visitors' second colour for the pairs where
 * `cardPaint` switches it. */
export function cardTable(): { c: Record<string, string>; s: Record<string, string> } {
  const s: Record<string, string> = {};
  const c = Object.fromEntries(Object.keys(COLOURS).map((a) => [a, teamPaint(a)]));
  for (const a of Object.keys(COLOURS)) for (const h of Object.keys(COLOURS)) {
    if (a === h) continue;
    const m = cardPaint(a, h);
    if (m !== c[a]) s[`${a}@${h}`] = m;
  }
  return { c, s };
}

/** Every matchup's colours in a small table for pages that draw games in the browser (home, team pages): each club's
 * own colour, plus the visitors' replacement for the pairs where either mode switches it (all as light-dark values). */
export function matchupTable(): { c: Record<string, string>; s: Record<string, string> } {
  const s: Record<string, string> = {};
  const c = Object.fromEntries(Object.keys(COLOURS).map((a) => [a, teamPaint(a)]));
  for (const a of Object.keys(COLOURS)) for (const h of Object.keys(COLOURS)) {
    if (a === h) continue;
    const m = matchupPaint(a, h).away;
    if (m !== c[a]) s[`${a}@${h}`] = m;
  }
  return { c, s };
}

/** Plain colours for drawing outside CSS (share cards): each club's own colour and the matchup's colours, for light
 * and dark cards. */
export function cardColours(away: string, home: string) {
  const own = (a: string) => ({ light: teamColour(a), dark: darkShade(teamColour(a), a) });
  return {
    own: { [away]: own(away), [home]: own(home) },
    matchup: { [away]: { light: matchupColours(away, home).away, dark: darkAway(away, home) }, [home]: own(home) },
  };
}
