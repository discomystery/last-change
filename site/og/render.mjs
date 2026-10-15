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
  ...font('Literata', 'literata-400', 400), ...font('Literata', 'literata-600', 600),
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

// Preview and post-game: the game page's rink header (Rink.astro) on the card: faint boards, goal, blue and red lines,
// the centre circle with "at" or "final", each team set in from its own goal line, the abbreviation in team colour with
// the score beside it at the same size and on the same baseline, the full name underneath. The date and arena go in the
// line under the card. Nothing that changes with the story, so a link looks the same however late it is shared.
const RINK = { boards: '#b0b8c1', red: 'rgba(200,16,46,0.32)', blue: 'rgba(31,78,158,0.32)' };
function rinkSide(t, right, size) {
  const ab = h('div', { color: t.color }, t.ab);
  const n = t.score == null ? null : h('div', { fontFamily: 'Fraunces Big', fontWeight: 600, letterSpacing: '-0.02em', color: C.ink }, String(t.score));
  return h('div', { flex: 1, flexDirection: 'column', alignItems: right ? 'flex-end' : 'flex-start', justifyContent: 'center', padding: right ? '0 128px 0 0' : '0 0 0 128px' },
    h('div', { alignItems: 'baseline', gap: size * 0.24, fontFamily: 'Hanken', fontWeight: 700, fontSize: size, lineHeight: 0.9, letterSpacing: '-0.035em' }, right ? [n, ab] : [ab, n]),
    h('div', { fontFamily: 'Literata', fontWeight: 600, fontSize: 28, color: C.ink, marginTop: 22 }, t.name));
}
function game(d, final) {
  const longest = Math.max(...[d.away, d.home].map((t) => t.ab.length + (t.score == null ? 0 : String(t.score).length + 0.6)));
  const size = longest <= 3 ? 150 : longest <= 4.6 ? 128 : longest <= 5 ? 112 : 96;
  const line = (left, width, colour, right = false) => h('div', { position: 'absolute', top: 0, bottom: 0, [right ? 'right' : 'left']: left, width, background: colour });
  const rink = h('div', { width: W - 72, height: H - 32 - 86, marginTop: 32, flexShrink: 0, background: C.sheet, border: `2px solid ${RINK.boards}`, borderRadius: 120, position: 'relative', overflow: 'hidden' },
    line('5.5%', 1.5, RINK.red), line('5.5%', 1.5, RINK.red, true), line('37.5%', 6, RINK.blue), line('37.5%', 6, RINK.blue, true), line(W / 2 - 36 - 3, 6, RINK.red),
    h('div', { position: 'absolute', left: (W - 72) / 2 - 2 - 64, top: (H - 32 - 86) / 2 - 2 - 64, width: 128, height: 128, borderRadius: 64, border: `3px solid ${RINK.blue}`, background: C.sheet, alignItems: 'center', justifyContent: 'center', fontFamily: 'Fraunces', fontSize: 38, color: C.ink2 }, final ? 'final' : 'at'),
    rinkSide(d.away, false, size), rinkSide(d.home, true, size));
  const end = d.end === 'OT' ? 'Final in overtime' : d.end === 'SO' ? 'Final in a shootout' : 'Final';
  const note = [final ? end : 'Preview', d.date, d.venue].filter(Boolean).join(' · ');
  return h('div', { width: W, height: H, background: C.ice, flexDirection: 'column', alignItems: 'center', fontFamily: 'Hanken', color: C.ink }, rink,
    h('div', { width: W - 84, height: 86, alignItems: 'center', justifyContent: 'space-between' },
      img(LOGO, { width: Math.round(36 * LOGO_RATIO), height: 36 }),
      h('div', { fontSize: 24, color: C.ink3, letterSpacing: '0.02em' }, note)));
}

// The big faint letters behind a name (a team's abbreviation, a player's sweater number), as on the page headers.
const ghost = (s, colour, size, sans = false, k = null) => h('div', { position: 'absolute', right: 44, top: sans ? -size * 0.04 : -size * 0.14, fontFamily: sans ? 'Hanken' : 'Fraunces Big', fontWeight: sans ? 700 : 600, fontSize: size, lineHeight: 1, letterSpacing: '-0.04em', color: tint(colour, k ?? (sans ? 0.1 : 0.2)) }, s);

function team(d) {
  return frame(h('div', { flexDirection: 'column', flex: 1, justifyContent: 'flex-end' },
    h('div', { width: 132, height: 12, borderRadius: 6, background: d.color }),
    disp(d.name, fit(d.name, [[18, 104], [24, 88], [99, 74]]), { marginTop: 26 }),
    d.kick ? h('div', { fontSize: 30, color: C.ink2, marginTop: 14 }, d.kick) : null,
  ), d.note, ghost(d.abbr, d.color, 300, true));
}

// Player: name, position and club, his sweater number huge in the club's colour, running off the card's right and
// bottom edges; with a portrait he stands on the sheet's bottom edge in front of a faint number, as on his page.
function player(d) {
  const num = d.number != null ? String(d.number) : '';
  const big = h('div', { position: 'absolute', right: num.length > 1 ? -70 : -20, bottom: -250, fontFamily: 'Fraunces Big', fontWeight: 600, fontSize: 720, lineHeight: 1, letterSpacing: '-0.05em', color: d.photo ? tint(d.color, 0.16) : d.color }, num);
  const photo = d.photo ? img(d.photo, { position: 'absolute', right: 70, bottom: -2, width: 400, height: 400 }) : null;
  return frame(h('div', { flexDirection: 'column', flex: 1, justifyContent: 'flex-end', maxWidth: num.length > 1 ? 560 : 680 },
    kicker(d.kick),
    disp(d.name, fit(d.name, [[12, 92], [17, 80], [22, 66], [99, 56]]), { marginTop: 16 }),
    h('div', { fontSize: 30, color: C.ink2, marginTop: 16, alignItems: 'center' },
      h('div', { width: 16, height: 16, borderRadius: 8, background: d.color, marginRight: 14 }), d.sub),
  ), d.note, [num ? big : null, photo]);
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
