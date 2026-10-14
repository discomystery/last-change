// Link-preview images (Open Graph / Twitter cards): the 1200 x 630 picture a page shows when its link is pasted into
// Twitter/X, iMessage, WhatsApp, Slack or Discord. Each page hands over a small card description (lib/og.ts, written into
// the page as <script type="application/json" data-og>); after the build, og/integration.mjs draws one PNG per page with
// this file. Same look as the 4:5 share cards (lib/shareCard.ts): ice background, white sheet, Fraunces headlines,
// Hanken labels, team abbreviations in matchup colours, logo underneath. Always the light theme: these images show
// on every app's own background, light or dark, and the white sheet reads on both.
import satori from 'satori';
import { Resvg } from '@resvg/resvg-js';
import UPNG from 'upng-js';
import { readFileSync } from 'node:fs';

const here = new URL('.', import.meta.url);
const asset = (p) => readFileSync(new URL(p, here));
const W = 1200, H = 630;
const C = { ice: '#edf2f6', sheet: '#ffffff', ink: '#0f1c2b', ink2: '#48586a', ink3: '#6f7e8e', rule: '#d2dce5', track: '#e4ebf1' };

// Static instances of the site's three fonts (made from the variable fonts with fontTools: Fraunces at SOFT 100), Latin
// and Latin Extended so every accented name draws.
const font = (name, file, weight) => ['latin', 'latin-ext'].map((s) => ({ name, weight, style: 'normal', data: asset(`../src/assets/og-fonts/${file}-${s}.ttf`) }));
const FONTS = [
  ...font('Fraunces', 'fraunces-650', 650), ...font('Fraunces Big', 'fraunces-big-600', 600),
  ...font('Hanken', 'hanken-500', 500), ...font('Hanken', 'hanken-600', 600), ...font('Hanken', 'hanken-700', 700),
  ...font('Literata', 'literata-400', 400),
];
const svgUri = (file, ink, soft) => `data:image/svg+xml;base64,${Buffer.from(
  asset(file).toString().replace(/class="lg-ink"/g, `fill="${ink}"`).replace(/class="lg-soft"/g, `fill="${soft}"`)).toString('base64')}`;
const LOGO = svgUri('../src/assets/logo/full.svg', C.ink, C.ink3);
const LOGO_RATIO = 1959.56 / 304.23;

// A tiny element builder for satori: h('div', style, ...children).
const h = (type, style = {}, ...kids) => {
  const children = kids.flat().filter((k) => k !== null && k !== undefined && k !== false && k !== '');
  return { type, props: { style: type === 'div' ? { display: 'flex', ...style } : style, children: children.length === 1 ? children[0] : children } };
};
const img = (src, style) => ({ type: 'img', props: { src, style, width: style.width, height: style.height } });

// Mix a colour with white (for the faint jersey number and abbreviation behind a name).
const tint = (hex, k) => {
  const n = parseInt(hex.replace('#', ''), 16), mix = (c) => Math.round(c * k + 255 * (1 - k));
  return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255].map(mix).map((x) => x.toString(16).padStart(2, '0')).join('')}`;
};
// A leading + or minus is set in the sans: the static Fraunces instance draws its + badly at this size.
const signed = (v) => (/^[+\u2212\u2013-]/.test(v) ? [h('span', { fontFamily: 'Hanken', fontWeight: 600, marginRight: 2 }, v[0] === '+' ? '+' : '\u2212'), v.slice(1)] : [v]);
// Headline size from its length, so one or two lines always fit.
const fit = (s, sizes) => sizes.find(([n]) => (s ?? '').length <= n)?.[1] ?? sizes[sizes.length - 1][1];

const kicker = (s, style = {}) => h('div', { fontFamily: 'Hanken', fontWeight: 600, fontSize: 22, letterSpacing: '0.07em', textTransform: 'uppercase', color: C.ink3, ...style }, s);
const disp = (s, size, style = {}) => h('div', { fontFamily: 'Fraunces', fontWeight: 650, fontSize: size, lineHeight: 1.08, letterSpacing: '-0.01em', color: C.ink, ...style }, s);

// The page around every card: ice, the white sheet, and the logo line underneath.
function frame(body, note, extra = null) {
  return h('div', { width: W, height: H, background: C.ice, flexDirection: 'column', alignItems: 'center', fontFamily: 'Hanken', color: C.ink },
    h('div', { width: W - 72, maxWidth: W - 72, flexShrink: 0, height: H - 32 - 86, marginTop: 32, boxSizing: 'border-box', background: C.sheet, borderRadius: 32, padding: '44px 60px 40px', flexDirection: 'column', position: 'relative', overflow: 'hidden' }, extra, body),
    h('div', { width: W - 84, height: 86, alignItems: 'center', justifyContent: 'space-between' },
      img(LOGO, { width: Math.round(36 * LOGO_RATIO), height: 36 }),
      h('div', { fontSize: 24, color: C.ink3, letterSpacing: '0.02em' }, note ?? 'sparelinechange.com')));
}

// One side of a game: colour bar, big abbreviation, nickname and record (or the score beside it).
function side(t, right, score) {
  const ab = h('div', { flexDirection: 'column', alignItems: right ? 'flex-end' : 'flex-start' },
    h('div', { width: 112, height: 10, borderRadius: 5, background: t.color }),
    h('div', { fontFamily: 'Hanken', fontWeight: 700, fontSize: 116, lineHeight: 1, letterSpacing: '-0.02em', marginTop: 14 }, t.ab),
    h('div', { fontSize: 26, color: C.ink2, marginTop: 8 }, [t.nick, t.rec].filter(Boolean).join(' · ')));
  if (score == null) return ab;
  const n = h('div', { fontFamily: 'Fraunces Big', fontWeight: 600, fontSize: 132, lineHeight: 1, letterSpacing: '-0.02em', margin: right ? '0 34px 0 0' : '0 0 0 34px', paddingTop: 8 }, String(score));
  return h('div', { alignItems: 'center' }, right ? [n, ab] : [ab, n]);
}

function game(d, final) {
  const mid = final
    ? kicker(d.end === 'OT' ? 'Final · OT' : d.end === 'SO' ? 'Final · SO' : 'Final', { fontSize: 24 })
    : h('div', { fontFamily: 'Fraunces', fontSize: 48, color: C.ink3, paddingBottom: 34 }, 'at');
  return frame(h('div', { flexDirection: 'column', flex: 1 },
    kicker([final ? 'Post-game' : 'Game preview', d.date, final ? d.venue : null].filter(Boolean).join(' · ')),
    h('div', { justifyContent: 'space-between', alignItems: 'center', marginTop: 26 }, side(d.away, false, final ? d.away.score : null), mid, side(d.home, true, final ? d.home.score : null)),
    h('div', { flex: 1 }),
    d.head ? disp(d.head, fit(d.head, [[36, 58], [62, 48], [200, 42]]), { maxHeight: 112, overflow: 'hidden' }) : null,
  ), d.note);
}

// A rating lane: label, track with the league-average tick and his or their dot, rank underneath.
function lane(l, colour) {
  return h('div', { flexDirection: 'column', flex: 1, minWidth: 0 },
    h('div', { fontSize: 25, fontWeight: 600, color: C.ink, whiteSpace: 'nowrap', overflow: 'hidden' }, l.label),
    h('div', { position: 'relative', height: 26, marginTop: 10, alignItems: 'center' },
      h('div', { position: 'absolute', left: 0, right: 0, top: 10, height: 6, borderRadius: 3, background: C.track }),
      h('div', { position: 'absolute', left: '50%', top: 5, width: 3, height: 16, background: C.ink3, opacity: 0.6 }),
      h('div', { position: 'absolute', left: `${Math.min(Math.max(l.pct, 3), 97)}%`, top: 0, width: 26, height: 26, marginLeft: -13, borderRadius: 13, background: C.sheet, alignItems: 'center', justifyContent: 'center' },
        h('div', { width: 20, height: 20, borderRadius: 10, background: colour }))),
    h('div', { justifyContent: 'space-between', fontSize: 19, color: C.ink3, marginTop: 8, letterSpacing: '0.04em', textTransform: 'uppercase', fontWeight: 600 },
      h('div', {}, l.ends?.[0] ?? ''), h('div', { color: C.ink2 }, l.rank ?? ''), h('div', {}, l.ends?.[1] ?? '')));
}
const lanes = (ls, colour) => (ls?.length ? h('div', { gap: 48, marginTop: 'auto' }, ls.map((l) => lane(l, colour))) : null);

// The big faint letters behind a name (a team's abbreviation, a player's sweater number), as on the page headers.
const ghost = (s, colour, size, sans = false) => h('div', { position: 'absolute', right: 44, top: sans ? -size * 0.06 : -size * 0.16, fontFamily: sans ? 'Hanken' : 'Fraunces Big', fontWeight: sans ? 700 : 600, fontSize: size, lineHeight: 1, letterSpacing: '-0.04em', color: tint(colour, sans ? 0.08 : 0.16) }, s);

function team(d) {
  return frame(h('div', { flexDirection: 'column', flex: 1 },
    kicker(d.kick),
    disp(d.name, fit(d.name, [[18, 84], [24, 72], [99, 62]]), { marginTop: 18 }),
    d.words ? disp(d.words, 46, { color: d.color, marginTop: 14 }) : null,
    lanes(d.lanes, d.color),
  ), d.note, ghost(d.abbr, d.color, 250, true));
}

function player(d) {
  const stats = h('div', { gap: 52, marginTop: 30 }, d.stats.map(([v, k]) => h('div', { flexDirection: 'column' },
    h('div', { fontFamily: 'Fraunces', fontWeight: 650, fontSize: 60, lineHeight: 1 }, ...signed(String(v))),
    h('div', { fontSize: 19, fontWeight: 600, color: C.ink3, letterSpacing: '0.07em', marginTop: 8, textTransform: 'uppercase' }, k))));
  return frame(h('div', { flexDirection: 'column', flex: 1 },
    kicker(d.kick),
    disp(d.name, fit(d.name, [[16, 84], [22, 72], [99, 60]]), { marginTop: 16 }),
    h('div', { fontSize: 26, color: C.ink2, marginTop: 10, alignItems: 'center' },
      h('div', { width: 14, height: 14, borderRadius: 7, background: d.color, marginRight: 12 }), d.sub),
    d.stats?.length ? stats : null,
    d.note2 ? h('div', { fontFamily: 'Literata', fontSize: 30, color: C.ink2, marginTop: 34 }, d.note2) : lanes(d.lanes, d.color),
  ), d.note, d.number != null ? ghost(String(d.number), d.color, 380) : null);
}

// Pages with no card of their own (Games, League, Methods...): the logo, the page's name and what it is for.
function page(d) {
  return frame(h('div', { flexDirection: 'column', flex: 1, justifyContent: 'center' },
    d.kick ? kicker(d.kick) : null,
    disp(d.title, fit(d.title, [[20, 80], [32, 64], [99, 54]]), { marginTop: d.kick ? 18 : 0 }),
    d.text ? h('div', { fontFamily: 'Literata', fontSize: 30, lineHeight: 1.4, color: C.ink2, marginTop: 22, maxHeight: 128, overflow: 'hidden' }, d.text) : null,
  ), d.note);
}

function home() {
  return h('div', { width: W, height: H, background: C.ice, alignItems: 'center', justifyContent: 'center', fontFamily: 'Hanken' },
    h('div', { width: W - 72, height: H - 64, flexShrink: 0, background: C.sheet, borderRadius: 32, flexDirection: 'column', alignItems: 'center', justifyContent: 'center' },
      img(LOGO, { width: Math.round(96 * LOGO_RATIO), height: 96 }),
      h('div', { fontFamily: 'Literata', fontSize: 32, color: C.ink2, marginTop: 40 }, 'How two NHL teams play, before they play.'),
      h('div', { fontSize: 24, fontWeight: 600, color: C.ink3, letterSpacing: '0.1em', marginTop: 26 }, 'SPARELINECHANGE.COM')));
}

const KINDS = { preview: (d) => game(d, false), final: (d) => game(d, true), team, player, page, home };

export const svgOf = (spec) => satori(KINDS[spec.kind](spec), { width: W, height: H, fonts: FONTS });

export async function render(spec) {
  const svg = await svgOf(spec);
  const px = new Resvg(svg, { fitTo: { mode: 'width', value: W }, font: { loadSystemFonts: false } }).render();
  // Down to a 256-colour palette: looks the same on these flat cards and halves the file (about 30 KB a card).
  return Buffer.from(UPNG.encode([px.pixels.buffer.slice(px.pixels.byteOffset, px.pixels.byteOffset + px.pixels.byteLength)], px.width, px.height, 256));
}
