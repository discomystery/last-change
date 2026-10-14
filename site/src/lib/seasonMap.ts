// @ts-nocheck
// The team page's season map (components/SeasonMap.astro), drawn in the browser: a map that plays the season so far,
// the next road trip, a trip abroad, or the full season on a spinning globe, beside a list of every game.
// Shapes come from public/geo/flight-map.json (scripts/flight-map-geo.mjs); games, places and colours from the page.
import { geoConicConformal, geoOrthographic, geoPath, geoGraticule10, geoRotation, geoInterpolate, geoDistance } from 'd3-geo';

const SVGNS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}, ...kids) => {
  const n = tag.startsWith('svg:') ? document.createElementNS(SVGNS, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'text') n.textContent = v; else n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null) n.append(k);
  return n;
};
const PLANE = 'M11 0L3 -1.8L-1.5 -9.5L-4.5 -9.5L-1.6 -1.8L-7.5 -1.8L-10 -5L-12 -5L-10.6 0L-12 5L-10 5L-7.5 1.8L-1.6 1.8L-4.5 9.5L-1.5 9.5L3 1.8Z';
const WORDS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
const HOME_COUNTRIES = new Set(['United States', 'Canada']);
const dayF = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
const mdF = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const moF = new Intl.DateTimeFormat(undefined, { month: 'long' });
const tF = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
const n0 = (n) => Math.round(n).toLocaleString('en-US');
const pl = (n, one, many) => (n === 1 ? one : many);
const cap1 = (s) => s.replace(/^./, (c) => c.toUpperCase());
const ord = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th');
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const dd = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
const motion = () => document.documentElement.classList.contains('motion');
const milesLL = (A, B) => {
  const r = Math.PI / 180, [p1, l1] = [A[0] * r, A[1] * r], [p2, l2] = [B[0] * r, B[1] * r];
  return 3958.8 * 2 * Math.asin(Math.sqrt(Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((l2 - l1) / 2) ** 2));
};
// Distances follow the footer's units switch (Canadian = kilometres); the page's own text converter skips these.
const units = () => (document.documentElement.dataset.spell === 'ca'
  ? { k: 1.609344, many: 'kilometres', short: 'km', most: 'most kilometres' }
  : { k: 1, many: 'miles', short: 'mi', most: 'most miles' });

const ringsD = (rings) => rings.map((r) => { let s = `M${r[0]} ${r[1]}`; for (let i = 2; i < r.length; i += 2) s += `L${r[i]} ${r[i + 1]}`; return s; }).join('');
function quad(a, b) { // control point bulging north of the chord
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
  let nx = -dy / len, ny = dx / len; if (ny > 0) { nx = -nx; ny = -ny; }
  const k = Math.min(0.22 * len, 90);
  return [mx + nx * k, my + ny * k];
}
const qpt = (a, c, b, t) => [(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1]];
const qtan = (a, c, b, t) => [2 * (1 - t) * (c[0] - a[0]) + 2 * t * (b[0] - c[0]), 2 * (1 - t) * (c[1] - a[1]) + 2 * t * (b[1] - c[1])];
const proj = (p) => geoConicConformal().parallels(p.parallels).rotate(p.rotate).scale(p.scale).translate(p.translate);
function fitView(pts, A, minW, pad) {
  const x0 = Math.min(...pts.map((p) => p[0])) - pad, x1 = Math.max(...pts.map((p) => p[0])) + pad + 50, y0 = Math.min(...pts.map((p) => p[1])) - pad, y1 = Math.max(...pts.map((p) => p[1])) + pad * 0.7;
  let w = Math.max(x1 - x0, minW), h = Math.max(y1 - y0, minW / A);
  if (w / h > A) h = w / A; else w = h * A;
  return [(x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2, w, h];
}

export function mountSeasonMap(root) {
  const D = JSON.parse(root.querySelector('script[type="application/json"]').textContent);
  const home = D.team, base = D.base, RECAPS = new Set(D.recaps), PREVIEWS = new Set(D.previews);
  const PL = D.places; // place key -> { ll: [lat, lon], label, city, country }
  const placeOf = (key) => PL[key]?.city ?? D.teamPlaces[key] ?? key;
  const abroadOf = (key) => (PL[key] && !HOME_COUNTRIES.has(PL[key].country) ? PL[key].country : null);
  const mi = (a, b) => milesLL(PL[a].ll, PL[b].ll);
  const today = new Date().toLocaleDateString('en-CA');

  // ---- The schedule, in order, placed where each game is played ----
  const all = D.games.map((g) => {
    const isHome = g.home === home, opp = isHome ? g.away : g.home, t = new Date(g.start);
    const x = { ...g, isHome, opp, city: g.place, away: g.place !== home, t, oc: D.opp[opp] };
    if (g.final) {
      const [m, o] = isHome ? [g.hs, g.as] : [g.as, g.hs];
      x.out = m > o ? 'W' : 'L'; x.score = `${m}–${o}`; x.ot = g.end && g.end !== 'REG' ? g.end : '';
    }
    x.href = g.final && RECAPS.has(g.id) ? `${base}recap/${g.id}/` : !g.final && PREVIEWS.has(g.id) ? `${base}preview/${g.id}/` : `${base}games/#${g.date}`;
    x.link = g.final && RECAPS.has(g.id) ? 'Post-game' : !g.final && PREVIEWS.has(g.id) ? 'Preview' : null;
    x.tonight = g.date === today && !g.final;
    return x;
  });
  all.forEach((g, i) => {
    const p = all[i - 1];
    g.b2b = !!p && dd(p.date, g.date) === 1;
    g.mi = mi(p ? p.city : home, g.city);
  });
  const record = (gs) => { let w = 0, l = 0, o = 0; gs.forEach((g) => (g.out === 'W' ? w++ : g.ot ? o++ : l++)); return `${w}-${l}-${o}`; };
  const nx = all.findIndex((g) => !g.final), now = nx < 0 ? all.length : nx;
  // Road stretches: consecutive games away from home (by where they are played).
  const trips = [];
  for (let i = 0; i < all.length;) { if (!all[i].away) { i++; continue; } const a = i; while (i < all.length && all[i].away) i++; trips.push([a, i - 1]); }
  const tripOf = new Map(); trips.forEach((t) => { for (let k = t[0]; k <= t[1]; k++) tripOf.set(k, t); });
  const nextTrip = trips.find((t) => t[0] >= now && t[1] > t[0]) || trips.find((t) => t[0] >= now);
  const tripAbroad = (t) => { for (let k = t[0]; k <= t[1]; k++) { const c = abroadOf(all[k].city); if (c) return c; } return null; };
  const abroadTrip = trips.find((t) => t[0] >= now && tripAbroad(t));
  const tripMiles = (t) => { let s = mi(home, all[t[0]].city); for (let k = t[0] + 1; k <= t[1]; k++) s += all[k].mi; return s + mi(all[t[1]].city, home); };
  const stops = (gs) => { const out = []; gs.forEach((g, i) => { if (i && gs[i - 1].city === g.city) out[out.length - 1].n++; else out.push({ c: placeOf(g.city), n: 1 }); }); return out.map((o) => (o.n > 1 ? `${o.c} (${WORDS[o.n] ?? o.n} games)` : o.c)); };
  const isGlobe = (md) => md === 'full' || md === 'abroad' || (md === 'trip' && !!nextTrip && !!tripAbroad(nextTrip));

  // ---- The list: every game, by month, opening at today ----
  const host = root.querySelector('.smap');
  const sl = el('div', { class: 'sl' }), ol = el('ol'); sl.append(ol);
  const rows = [];
  const toToday = el('button', { type: 'button', text: 'Back to today' });
  function list() {
    const U = units();
    ol.replaceChildren(); rows.length = 0;
    let mon = '';
    all.forEach((g, j) => {
      const mo = moF.format(g.t);
      if (mo !== mon) {
        mon = mo; const inMo = all.filter((x) => moF.format(x.t) === mo), done = inMo.filter((x) => x.final);
        ol.append(el('li', { class: 'mo' }, mo, el('span', { text: done.length ? `${record(done)} in ${done.length}` : `${inMo.length} ${pl(inMo.length, 'game', 'games')}` })));
      }
      const t = tripOf.get(j);
      if (t && t[0] === j && (t[1] - t[0] >= 2 || tripAbroad(t))) {
        const n = t[1] - t[0] + 1, c = tripAbroad(t);
        ol.append(el('li', { class: 'trip-h', 'data-nospell': true, text: `${cap1(WORDS[n] ?? String(n))}-game ${c ? 'trip to ' + c : 'road trip'} · ${n0(tripMiles(t) * U.k)} ${U.short}` }));
      }
      if (j === now) ol.append(el('li', { class: 'today', text: 'Today' }));
      const res = g.final ? el('span', { class: 'res' }, el('b', { text: g.out }), ` ${g.score}${g.ot ? ' ' + g.ot : ''}`) : el('span', { class: 'res', text: tF.format(g.t) });
      const where = PL[g.city] && g.city.startsWith('@') ? el('span', { class: 'chip phrase', title: `Played at ${g.venue}`, text: abroadOf(g.city) ? `in ${placeOf(g.city)}` : g.venue }) : null;
      const who = el('span', { class: 'who' }, el('span', { class: 'va', text: g.isHome ? 'vs' : 'at' }),
        el('a', { href: g.href, style: `--oc:${g.oc}`, title: g.link ? `${g.link}: ${D.names[g.opp]}` : `${dayF.format(g.t)} on the Games page` }, D.teamPlaces[g.opp] ?? g.opp),
        where, g.tonight ? el('span', { class: 'chip solid', text: 'Tonight' }) : null, g.b2b ? el('span', { class: 'chip', title: 'Second game on back-to-back days', text: 'B2B' }) : null);
      const r = el('li', { class: `r${g.away ? ' road' : ''}${g.final ? ' past' : ''}${j === now ? ' now' : ''}`, 'data-j': j }, el('span', { class: 'd' }, mdF.format(g.t)), who, res);
      ol.append(r); rows.push(r);
    });
    if (now >= all.length) ol.append(el('li', { class: 'today', text: 'Today' }));
  }
  const scrollToRow = (j, smooth) => {
    const r = j >= all.length || j === now ? ol.querySelector('.today') || rows[j] : rows[j];
    if (!r) return;
    sl.scrollTo({ top: Math.max(0, r.offsetTop - (j === now ? 44 : 74)), behavior: smooth && motion() ? 'smooth' : 'auto' });
  };

  // ---- Map card (filled once the shapes arrive) ----
  const modeSel = el('select', { 'aria-label': 'Map story' },
    el('option', { value: 'so', text: 'the season so far' }),
    nextTrip ? el('option', { value: 'trip', text: tripAbroad(nextTrip) ? `the trip to ${tripAbroad(nextTrip)}` : 'the next road trip' }) : null,
    abroadTrip && abroadTrip !== nextTrip ? el('option', { value: 'abroad', text: `the trip to ${tripAbroad(abroadTrip)}` }) : null,
    el('option', { value: 'full', text: 'the full season' }));
  const again = el('button', { class: 'again', type: 'button', 'aria-label': 'Play again', title: 'Play again' });
  again.innerHTML = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M8.6 5a3.6 3.6 0 1 1-1-2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M8.9 0.9v2.4H6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const stats = el('div', { class: 'sm-stats', 'data-nospell': true }), note = el('p', { class: 'mode-note' });
  const maps = el('div', { class: 'maps' }, el('div', { class: 'map-wait' }));
  const mapCard = el('div', { class: 'card sm-map' }, el('div', { class: 'pick' }, el('span', { text: 'Show' }), modeSel, again), maps, stats, note);
  const listCard = el('div', { class: 'card sm-list' }, el('div', { class: 'sl-h' }, el('h3', { text: `All ${all.length} ${pl(all.length, 'game', 'games')}` }), toToday), sl);
  host.replaceChildren(mapCard, listCard);
  list();
  const fit = () => { sl.style.maxHeight = host.clientWidth > 700 ? `${Math.max(380, mapCard.offsetHeight - 60)}px` : ''; };
  requestAnimationFrame(() => { fit(); scrollToRow(now, false); });
  toToday.addEventListener('click', () => scrollToRow(now, true));
  if (!all.length) { mapCard.remove(); return; }

  fetch(`${base}geo/flight-map.json`).then((r) => r.json()).then((GEO) => drawMaps(GEO)).catch(() => mapCard.remove());

  function drawMaps(GEO) {
    const flat = proj(GEO.flat.projection), inset = proj(GEO.inset.projection), IB = GEO.inset.box;
    const inInset = (ll) => { const [x, y] = inset([ll[1], ll[0]]); return ll[1] > -35 && x >= IB[0] && x <= IB[0] + IB[2] && y >= IB[1] && y <= IB[1] + IB[3]; };
    const XY = {};
    const P = (key) => (XY[key] ||= (() => { const ll = PL[key].ll, p = inInset(ll) ? inset([ll[1], ll[0]]) : flat([ll[1], ll[0]]); return [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10]; })());
    const isInset = (key) => inInset(PL[key].ll);
    const label = (key) => (key.startsWith('@') ? PL[key].city : key);

    // Flat map.
    const svg = el('svg:svg', { class: 'flat', role: 'img', 'aria-label': `Map of ${D.place}’s season` });
    const borderP = el('svg:path', { class: 'border', d: ringsD(GEO.flat.border) });
    svg.append(el('svg:rect', { class: 'water', x: -400, y: -300, width: 1800, height: 1200 }), el('svg:path', { class: 'land', d: ringsD(GEO.flat.land) }), borderP);
    let insetFrame = null, insetG = null;
    if (all.some((g) => isInset(g.city))) {
      const cid = `inset-${home}`;
      insetFrame = el('svg:rect', { class: 'inset-frame', x: IB[0], y: IB[1], width: IB[2], height: IB[3], rx: 6 });
      insetG = el('svg:g', {}, el('svg:defs', {}, el('svg:clipPath', { id: cid }, el('svg:rect', { x: IB[0], y: IB[1], width: IB[2], height: IB[3], rx: 6 }))),
        el('svg:g', { 'clip-path': `url(#${cid})` }, el('svg:rect', { class: 'water', x: IB[0], y: IB[1], width: IB[2], height: IB[3] }),
          el('svg:path', { class: 'land', d: ringsD(GEO.inset.land) }), el('svg:path', { class: 'inset-border', d: ringsD(GEO.inset.border) })),
        insetFrame, el('svg:text', { class: 'inset-label', x: IB[0] + 7, y: IB[1] + 11, text: 'EUROPE' }));
      svg.append(insetG);
    }
    const dots = Object.keys(D.teamPlaces).filter((a) => PL[a]).map((a) => { const p = P(a), c = el('svg:circle', { class: 'lg-dot', cx: p[0], cy: p[1] }); svg.append(c); return c; });
    // One leg into every game played somewhere new.
    const legs = [];
    all.forEach((g, j) => {
      const from = j ? all[j - 1].city : home;
      if (from === g.city) return;
      const a = P(from), b = P(g.city), c = quad(a, b), d = `M${a[0]} ${a[1]}Q${c[0].toFixed(1)} ${c[1].toFixed(1)} ${b[0]} ${b[1]}`;
      const baseP = el('svg:path', { class: `leg${g.final ? '' : ' fut'}`, d }), tr = el('svg:path', { class: `trail${g.final ? '' : ' fut'}`, d });
      svg.append(baseP); legs.push({ j, a, b, c, d, base: baseP, tr, len: 0 });
    });
    legs.forEach((l) => svg.append(l.tr));
    const legInto = new Map(legs.map((l) => [l.j, l]));
    const hl = el('svg:path', { class: 'hl-leg' }); svg.append(hl);
    const cityEls = new Map();
    for (const a of [home, ...new Set(all.map((g) => g.city))].filter((v, i, s) => s.indexOf(v) === i)) {
      const g = all.find((x) => x.city === a && a !== home);
      const txt = isInset(a) ? el('svg:text', { x: 0, y: 17, 'text-anchor': 'middle', text: label(a) }) : el('svg:text', { x: 8, y: -6, text: a === home ? `${a} · home` : label(a) });
      const e = el('svg:g', { class: `city${a === home ? ' home' : ''}`, style: g ? `--oc:${g.oc}` : null }, el('svg:circle', { r: a === home ? 6 : 4.5 }), txt);
      svg.append(e); cityEls.set(a, e);
    }
    const plane = el('svg:g', { class: 'plane' }, el('svg:path', { d: PLANE, transform: 'scale(1.1)' })); svg.append(plane);

    // Globe.
    const GW = 600;
    const gsvg = el('svg:svg', { class: 'globe', role: 'img', 'aria-label': `Globe showing ${D.place}’s travel` });
    const gSphere = el('svg:path', { class: 'g-sphere' }), gGrat = el('svg:path', { class: 'g-grat' }), gLand = el('svg:path', { class: 'g-land' });
    const gLegs = el('svg:g'), gCities = el('svg:g');
    const gPlane = el('svg:g', { class: 'plane' }, el('svg:path', { d: PLANE, transform: 'scale(1.1)' }));
    gsvg.append(el('svg:rect', { class: 'g-sky', x: -100, y: -100, width: 900, height: 900 }), gSphere, gGrat, gLand, gLegs, gCities, gPlane);
    const LANDG = { type: 'MultiPolygon', coordinates: GEO.globe.map((Pg) => Pg.map((r) => { const c = []; for (let i = 0; i < r.length; i += 2) c.push([r[i], r[i + 1]]); return c; })) };
    const GRAT = geoGraticule10();
    const lonlat = (a) => [PL[a].ll[1], PL[a].ll[0]];
    maps.replaceChildren(svg, gsvg);

    let cur = null, gState = null, anim = 0, mode = null, stage = null, planeK = 1, planePos = P(home), planeAng = 0;
    const A = () => { const r = svg.getBoundingClientRect(); return r.width / Math.max(1, r.height); };
    const viewPts = (v) => { const p = v.map(P); if (v.some(isInset)) p.push([IB[0], IB[1]], [IB[0] + IB[2] - 50, IB[1] + IB[3]]); return p; };
    const drawPlane = () => plane.setAttribute('transform', `translate(${planePos[0].toFixed(1)} ${planePos[1].toFixed(1)}) rotate(${((planeAng * 180) / Math.PI).toFixed(1)}) scale(${planeK})`);
    const setTrail = (l, fr) => { l.len ||= l.tr.getTotalLength(); l.tr.style.strokeDasharray = `${l.len} ${l.len}`; l.tr.style.strokeDashoffset = `${l.len * (1 - fr)}`; };
    function setView(v) {
      cur = v; svg.setAttribute('viewBox', v.map((x) => x.toFixed(1)).join(' '));
      const k = v[2] / Math.max(1, svg.getBoundingClientRect().width);
      dots.forEach((d) => d.setAttribute('r', 2 * k));
      borderP.setAttribute('stroke-width', 0.8 * k); borderP.setAttribute('stroke-dasharray', `${3 * k} ${3 * k}`);
      for (const l of legs) { l.base.setAttribute('stroke-width', 1.4 * k); l.tr.setAttribute('stroke-width', 2.4 * k); }
      hl.setAttribute('stroke-width', 3.2 * k); insetFrame?.setAttribute('stroke-width', 1 * k);
      for (const [a, e] of cityEls) { const p = P(a); e.setAttribute('transform', `translate(${p[0]} ${p[1]}) scale(${k})`); }
      planeK = k; drawPlane();
    }

    function drawGlobe(st) {
      gState = st;
      const r = gsvg.getBoundingClientRect(), H = Math.round(GW / Math.max(0.5, r.width / Math.max(1, r.height))), k = GW / Math.max(1, r.width);
      gsvg.setAttribute('viewBox', `0 0 ${GW} ${H}`);
      const R0 = Math.min(GW, H) * 0.4 * st.z, rot = [-st.c[0], -st.c[1] + (st.full ? 0 : 12)];
      const pj = geoOrthographic().rotate(rot).translate([GW / 2, H / 2]).scale(R0).clipAngle(90).precision(0.5), path = geoPath(pj);
      gSphere.setAttribute('d', path({ type: 'Sphere' })); gGrat.setAttribute('d', path(GRAT)); gLand.setAttribute('d', path(LANDG));
      const rr = geoRotation(rot);
      // A point at height h above the globe: the orthographic view is a straight drop, so height pushes it out from the middle.
      const P3 = (p, h = 0) => { const [l, f] = rr(p).map((v) => (v * Math.PI) / 180), x = Math.cos(f) * Math.sin(l), y = Math.sin(f), z = Math.cos(f) * Math.cos(l), q = R0 * (1 + h); return [GW / 2 + q * x, H / 2 - q * y, z > 0 || (1 + h) * Math.hypot(x, y) > 1]; };
      const line = (l, t0, t1) => { let d = '', pen = false; const n = Math.max(8, Math.ceil(80 * (t1 - t0))); for (let i = 0; i <= n; i++) { const t = t0 + ((t1 - t0) * i) / n, [x, y, v] = P3(l.ip(t), l.alt * Math.sin(Math.PI * t)); if (v) { d += `${pen ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`; pen = true; } else pen = false; } return d; };
      gLegs.replaceChildren(...st.legs.flatMap((l) => [st.full ? null : el('svg:path', { class: 'g-leg', d: line(l, 0, 1), 'stroke-width': 1.4 * k }), l.fr > 0 ? el('svg:path', { class: `g-trail${l.fut ? ' fut' : ''}`, d: line(l, 0, l.fr), 'stroke-width': (st.full ? 1.8 : 2.6) * k }) : null]),
        ...st.legs.filter((l) => l.j === st.hl).map((l) => el('svg:path', { class: 'g-hl', d: line(l, 0, 1), 'stroke-width': 3.4 * k })));
      gCities.replaceChildren(...st.cities.map((a) => {
        const [x, y, v] = P3(lonlat(a)); if (!v) return null;
        const g = all.find((q) => q.city === a && a !== home);
        return el('svg:g', { class: `city${a === home ? ' home' : ''}${st.on === a || (st.hl != null && all[st.hl].city === a) ? ' on' : ''}`, style: g ? `--oc:${g.oc}` : null, transform: `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${k})` },
          el('svg:circle', { r: a === home ? 6 : 4.5 }), el('svg:text', { x: 8, y: -6, text: a === home ? `${a} · home` : label(a) }));
      }));
      if (st.plane) {
        const { l, t } = st.plane, h = (u) => l.alt * Math.sin(Math.PI * u), a = P3(l.ip(t), h(t)), b = P3(l.ip(Math.min(1, t + 0.01)), h(Math.min(1, t + 0.01))), c0 = P3(l.ip(Math.max(0, t - 0.01)), h(Math.max(0, t - 0.01)));
        const dx = t < 0.99 ? b[0] - a[0] : a[0] - c0[0], dy = t < 0.99 ? b[1] - a[1] : a[1] - c0[1];
        gPlane.style.display = a[2] ? '' : 'none';
        gPlane.setAttribute('transform', `translate(${a[0].toFixed(1)} ${a[1].toFixed(1)}) rotate(${((Math.atan2(dy, dx) * 180) / Math.PI).toFixed(1)}) scale(${k})`);
      } else gPlane.style.display = 'none';
    }

    function playGlobe(st, animate, full) {
      const seq = legs.filter((l) => st.js.includes(l.j)).map((l) => {
        const a = lonlat(l.j ? all[l.j - 1].city : home), b = lonlat(all[l.j].city), dist = geoDistance(a, b);
        return { j: l.j, a, b, dist, ip: geoInterpolate(a, b), alt: Math.min(0.14, 0.11 * dist), fr: 0, to: all[l.j].city, fut: !all[l.j].final };
      });
      const cities = [home, ...new Set(seq.map((l) => l.to))].filter((v, i, s) => s.indexOf(v) === i);
      if (full) {
        // The whole season: the camera stays close over North America and only swings out for a trip abroad.
        const gr = gsvg.getBoundingClientRect(), gH = GW / Math.max(0.5, gr.width / Math.max(1, gr.height)), narrow = gr.width < 500;
        const NA = { c: [narrow ? -89 : -94.5, 41.5], z: ((3.75 * 400) / Math.min(GW, gH)) * (narrow ? 0.78 : 1) };
        const baseSt = { legs: seq, cities, full: true };
        if (!animate || !motion()) { seq.forEach((l) => (l.fr = 1)); drawGlobe({ ...baseSt, ...NA, plane: null }); return; }
        const camAt = (c) => (abroadOf(c) ? { c: lonlat(c), z: 2.1 } : NA);
        const durs = seq.map((l) => (230 + 2400 * l.dist) * 0.55), stop = 45, intro = 700;
        const total = intro + durs.reduce((s, d) => s + d + stop, 0) + 900, t0 = performance.now(), from = gState ? { c: gState.c, z: gState.z } : NA;
        const step = (ts) => {
          let u = ts - t0;
          if (u < intro || !seq.length) { const e = ease(clamp(u / intro)); drawGlobe({ ...baseSt, c: geoInterpolate(from.c, NA.c)(e), z: lerp(from.z, NA.z, e), plane: null }); }
          else {
            u -= intro;
            let n = 0; while (n < seq.length - 1 && u > durs[n] + stop) { seq[n].fr = 1; u -= durs[n] + stop; n++; }
            const l = seq[n], t = clamp(u / durs[n]), e = ease(t);
            l.fr = e;
            if (n === seq.length - 1 && u > durs[n] + stop) { const e2 = ease(clamp((u - durs[n] - stop) / 900)), c1 = camAt(l.to); drawGlobe({ ...baseSt, c: geoInterpolate(c1.c, NA.c)(e2), z: lerp(c1.z, NA.z, e2), plane: null }); }
            else {
              const c0 = camAt(n ? seq[n - 1].to : home), c1 = camAt(l.to), dip = Math.max(0, l.dist - 0.5) * 1.7;
              drawGlobe({ ...baseSt, c: geoInterpolate(c0.c, c1.c)(e), z: Math.max(0.95, lerp(c0.z, c1.z, e) - dip * Math.sin(Math.PI * e)), plane: { l, t: e } });
            }
          }
          if (ts - t0 < total) anim = requestAnimationFrame(step); else anim = 0;
        };
        anim = requestAnimationFrame(step);
        return;
      }
      // A trip abroad: start close over home, pull out as the plane climbs, come down close over the far city.
      const far = seq.reduce((f, l) => (geoDistance(lonlat(home), l.b) > geoDistance(lonlat(home), f) ? l.b : f), lonlat(home));
      const restC = geoInterpolate(lonlat(home), far)(0.5);
      if (!animate || !motion() || !seq.length) { seq.forEach((l) => (l.fr = 1)); drawGlobe({ c: restC, z: 1, legs: seq, cities, plane: seq.length ? { l: seq[seq.length - 1], t: 1 } : null }); return; }
      const ZE = 2.3, intro = 700, durs = seq.map((l) => 700 + 2600 * l.dist), stop = 900;
      const total = intro + durs.reduce((s, d) => s + d + stop, 0), t0 = performance.now(), cStart = gState ? gState.c : restC, zStart = gState ? gState.z : 1;
      const step = (ts) => {
        let u = ts - t0;
        if (u < intro) { const e = ease(u / intro); drawGlobe({ c: geoInterpolate(cStart, seq[0].a)(e), z: lerp(zStart, ZE, e), legs: seq, cities, plane: { l: seq[0], t: 0 } }); }
        else {
          u -= intro;
          let n = 0; while (n < seq.length - 1 && u > durs[n] + stop) { seq[n].fr = 1; u -= durs[n] + stop; n++; }
          const l = seq[n], t = clamp(u / durs[n]), e = ease(t);
          l.fr = e;
          const zm = Math.max(1, ZE - 1.6 * l.dist), z = lerp(ZE, zm, Math.sin(Math.PI * e));
          drawGlobe({ c: l.ip(e), z, legs: seq, cities, on: t >= 1 ? l.to : null, plane: { l, t: e } });
        }
        if (ts - t0 < total) anim = requestAnimationFrame(step); else anim = 0;
      };
      anim = requestAnimationFrame(step);
    }

    // ---- Stories ----
    function story(md) {
      const U = units(), d0 = (x) => n0(x * U.k);
      const b2bs = (gs) => { const n = gs.filter((g) => g.b2b).length; return [String(n), pl(n, 'back-to-back', 'back-to-backs')]; };
      if (md === 'so') {
        const js = []; for (let j = 0; j < now; j++) js.push(j);
        const done = all.slice(0, now), m = done.reduce((s, g) => s + g.mi, 0), aw = done.filter((g) => g.away).length;
        const where = now ? all[now - 1].city : home, nxt = all[now];
        return { js, end: where, view: [home, ...done.map((g) => g.city)], speed: 0.6, focus: now,
          stats: [[record(done), `record in ${done.length} ${pl(done.length, 'game', 'games')}`], [d0(m), `${U.many} flown`], [String(aw), pl(aw, 'game away from home', 'games away from home')], b2bs(done)],
          note: [el('b', { text: done.length ? `${D.place} has played ${done.length} ${pl(done.length, 'game', 'games')}.` : `${D.place} hasn’t played yet.` }), ` They’re ${where === home ? 'home' : 'in ' + placeOf(where)} now${nxt ? `, ${nxt.isHome ? 'hosting' : 'off to'} ${D.teamPlaces[nxt.opp]} next` : ''}.`] };
      }
      if (md === 'trip' || md === 'abroad') {
        const t = md === 'abroad' ? abroadTrip : nextTrip, js = []; for (let j = t[0]; j <= t[1]; j++) js.push(j);
        const back = all[t[1] + 1];
        if (back && !back.away) js.push(t[1] + 1);
        const gs = all.slice(t[0], t[1] + 1), fl = Math.max(...gs.map((g) => g.mi)), days = dd(gs[0].date, gs[gs.length - 1].date) + 1;
        return { js, end: back && !back.away ? home : gs[gs.length - 1].city, view: [home, ...gs.map((g) => g.city)], speed: 1, focus: t[0], start: home,
          stats: [[`${gs.length}`, `${pl(gs.length, 'game', 'games')} away in ${days} ${pl(days, 'day', 'days')}`], [d0(tripMiles(t)), `${U.many} there and back`], b2bs(gs), [d0(fl), `${U.many}, longest flight`]],
          note: [el('b', { text: `${mdF.format(gs[0].t)} to ${mdF.format(gs[gs.length - 1].t)}:` }), ` ${stops(gs).join(', ')}${back && !back.away ? ', then home' : ''}.`] };
      }
      const js = all.map((_, j) => j), m = all.reduce((s, g) => s + g.mi, 0) + mi(all[all.length - 1].city, home);
      const long = trips.reduce((b, t) => (t[1] - t[0] > b[1] - b[0] ? t : b), trips[0] || [0, -1]), ln = long[1] - long[0] + 1;
      const ab = trips.find(tripAbroad), abG = ab ? all.slice(ab[0], ab[1] + 1).filter((g) => abroadOf(g.city)) : [];
      const abNote = abG.length ? ` On ${abG.length === 1 ? mdF.format(abG[0].t) : `${mdF.format(abG[0].t)} and ${abG[abG.length - 1].t.getMonth() === abG[0].t.getMonth() ? abG[abG.length - 1].t.getDate() : mdF.format(abG[abG.length - 1].t)}`} they play ${WORDS[abG.length] ?? abG.length} ${pl(abG.length, 'game', 'games')} in ${placeOf(abG[0].city)}, ${abroadOf(abG[0].city)}.` : '';
      return { js, end: all[all.length - 1].city, view: [home, ...all.map((g) => g.city)], speed: 0.28, focus: now,
        stats: [[ord(D.rank.n), `${U.most} of ${D.rank.of} teams`], [d0(m), `${U.many} in all`], b2bs(all), [String(ln), pl(ln, 'game on the longest trip', 'games on the longest trip')]],
        note: [el('b', { text: 'The whole season:' }), ` solid lines are trips already made, faint ones are still to come.${trips.length ? ` The longest road trip starts ${mdF.format(all[long[0]].t)}.` : ''}${abNote}`] };
    }

    function play(md, animate = true) {
      cancelAnimationFrame(anim); anim = 0; mode = md; stage = story(md); svg.classList.toggle('full', md === 'full');
      stats.replaceChildren(...stage.stats.map(([v, l]) => el('div', { class: 'sm-stat' }, el('b', { text: v }), el('span', { text: l }))));
      note.replaceChildren(...stage.note);
      const inStory = new Set(stage.js), trip = md === 'trip' ? nextTrip : md === 'abroad' ? abroadTrip : null;
      rows.forEach((r, j) => r.classList.toggle('inmode', md !== 'full' && inStory.has(j) && (!trip || j <= trip[1])));
      const vis = new Set(stage.view); for (const [a, e] of cityEls) e.classList.toggle('off', !vis.has(a));
      legs.forEach((l) => { setTrail(l, 0); l.base.style.display = md === 'full' || inStory.has(l.j) ? '' : 'none'; });
      scrollToRow(stage.focus, animate);
      const gl = isGlobe(md); mapCard.classList.toggle('globe-on', gl);
      if (gl) { playGlobe(stage, animate, md === 'full'); return; }
      if (insetG) insetG.style.display = stage.view.some(isInset) ? '' : 'none';
      const seq = legs.filter((l) => inStory.has(l.j));
      const target = fitView(viewPts(stage.view), A(), 380, 60), from = cur || target;
      const startCity = stage.start ?? (seq[0] ? (seq[0].j ? all[seq[0].j - 1].city : home) : stage.end);
      if (!animate || !motion()) {
        setView(target); seq.forEach((l) => setTrail(l, 1));
        planePos = P(stage.end); const last = seq[seq.length - 1]; if (last) { const d = qtan(last.a, last.c, last.b, 0.98); planeAng = Math.atan2(d[1], d[0]); } drawPlane(); return;
      }
      planePos = P(startCity); drawPlane();
      // Fly each leg in turn: time in proportion to its length, with a short stop at each city.
      const durs = seq.map((l) => Math.max(160, Math.hypot(l.b[0] - l.a[0], l.b[1] - l.a[1]) * 2.2, all[l.j].mi * 0.16) * stage.speed), stop = 140 * stage.speed;
      const total = 650 + durs.reduce((s, d) => s + d + stop, 0), t0 = performance.now();
      const step = (ts) => {
        let u = ts - t0;
        setView(from.map((x, k) => lerp(x, target[k], ease(clamp(u / 650)))));
        u -= 650;
        for (let n = 0; n < seq.length; n++) {
          const l = seq[n], fr = clamp(u / durs[n]);
          setTrail(l, ease(fr));
          if (u > 0 && u < durs[n] + stop) { const te = ease(fr); planePos = qpt(l.a, l.c, l.b, te); const d = qtan(l.a, l.c, l.b, clamp(te, 0.02, 0.98)); planeAng = Math.atan2(d[1], d[0]); }
          u -= durs[n] + stop;
        }
        drawPlane();
        if (ts - t0 < total) anim = requestAnimationFrame(step); else { anim = 0; planePos = P(stage.end); drawPlane(); }
      };
      anim = requestAnimationFrame(step);
    }

    // Pointing at a game finds it on the map.
    let hot = null;
    function focusGame(j) {
      if (isGlobe(mode) && gState && !anim) drawGlobe({ ...gState, hl: j });
      if (hot != null) rows[hot]?.classList.remove('hl');
      hot = j;
      for (const [a, e] of cityEls) e.classList.toggle('on', j != null && a === all[j].city);
      if (j == null) { hl.classList.remove('on'); return; }
      rows[j]?.classList.add('hl');
      const l = legInto.get(j);
      if (l) { hl.setAttribute('d', l.d); hl.classList.add('on'); } else hl.classList.remove('on');
      const e = cityEls.get(all[j].city); if (e) { e.classList.remove('off'); e.parentNode.append(e); }
    }
    ol.addEventListener('pointerover', (e) => { const r = e.target.closest('.r'); if (r) focusGame(+r.dataset.j); });
    ol.addEventListener('pointerleave', () => { focusGame(null); if (stage) { const vis = new Set(stage.view); for (const [a, e] of cityEls) e.classList.toggle('off', !vis.has(a)); } });
    ol.addEventListener('focusin', (e) => { const r = e.target.closest('.r'); if (r) focusGame(+r.dataset.j); });
    ol.addEventListener('click', (e) => { const r = e.target.closest('.r'); if (r && !e.target.closest('a')) focusGame(+r.dataset.j); });
    modeSel.addEventListener('change', () => play(modeSel.value));
    again.addEventListener('click', () => play(modeSel.value));

    const first = nextTrip && dd(today, all[nextTrip[0]].date) <= 21 ? 'trip' : now ? 'so' : 'full';
    modeSel.value = first;
    // Rest frame: the route already drawn, so the section reads before anything moves; it plays once when in view.
    requestAnimationFrame(() => { fit(); play(first, false); scrollToRow(now, false); });
    let seen = false;
    const io = new IntersectionObserver((es) => { if (!seen && es[0].isIntersecting) { seen = true; io.disconnect(); if (motion()) play(modeSel.value); } }, { threshold: 0.5 });
    io.observe(maps);
    new ResizeObserver(() => { fit(); if (stage && !anim) { if (isGlobe(mode)) gState && drawGlobe(gState); else setView(fitView(viewPts(stage.view), A(), 380, 60)); } }).observe(mapCard);
    // The footer's units switch: redraw the distances.
    new MutationObserver(() => { list(); if (stage) { const keep = anim; play(mode, false); if (keep) cancelAnimationFrame(keep); } scrollToRow(now, false); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-spell'] });
  }
}
