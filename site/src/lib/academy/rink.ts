// @ts-nocheck
// The Academy's rink drawer, run in the browser: one NHL rink (200 by 85 feet) drawn from above or from a seat in the
// stands, plus the players, officials and puck that skate on it. Feet on the ice: x 0-200 along the rink, y 0 (far
// boards) to 85 (near boards), z up. Every lesson draws with these, so the rink looks the same on every page.

const NS = 'http://www.w3.org/2000/svg';
export const el = (parent, tag, attrs = {}, text) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
};
export const r2 = (v) => Math.round(v * 100) / 100;
export const motion = () => document.documentElement.classList.contains('motion');

// ---------- Viewpoints ----------
// "Above" draws feet as they are. A seat is a camera at C looking at T; P gives screen x, y and a size factor
// (how big one foot looks there), and inv turns a screen point back into a spot on the ice.
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dotp = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm = (a) => { const l = Math.hypot(...a); return a.map((v) => v / l); };
function seat(C, T) {
  const fw = norm(sub(T, C)), rt = norm([-fw[1], fw[0], 0]);
  const up = [fw[1] * rt[2] - fw[2] * rt[1], fw[2] * rt[0] - fw[0] * rt[2], fw[0] * rt[1] - fw[1] * rt[0]];
  const F = Math.hypot(...sub(T, C));
  return {
    top: false,
    P: (x, y, z = 0) => { const d = sub([x, y, z], C), zc = dotp(d, fw); return [F * dotp(d, rt) / zc, -F * dotp(d, up) / zc, F / zc]; },
    inv: (sx, sy) => {
      const dir = [0, 1, 2].map((i) => fw[i] + (sx / F) * rt[i] - (sy / F) * up[i]);
      const t = -C[2] / dir[2];
      return [C[0] + t * dir[0], C[1] + t * dir[1]];
    },
    // Nearer things are drawn last, so they stand in front.
    depth: (x, y) => dotp(sub([x, y, 0], C), fw),
  };
}
export const VIEWS = {
  above: { top: true, P: (x, y) => [x, y, 1], inv: (sx, sy) => [sx, sy], depth: () => 0 },
  center: seat([100, 145, 70], [100, 42, 0]),
  corner: seat([-30, 125, 62], [96, 40, 0]),
  end: seat([-62, 42.5, 52], [104, 42.5, 0]),
};
export const VIEW_NAMES = { above: 'from above', center: 'from a seat at center ice', corner: 'from a corner seat', end: 'from behind the net' };

// ---------- Shapes on the ice ----------
const PI = Math.PI;
export const pathOf = (V, pts, closed = true, z = 0) => pts.map((p, i) => { const q = V.P(p[0], p[1], p[2] ?? z); return (i ? 'L' : 'M') + r2(q[0]) + ' ' + r2(q[1]); }).join('') + (closed ? 'Z' : '');
export const rectPts = (x, y, w, h) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
export const arcPts = (cx, cy, r, a0, a1, n) => Array.from({ length: n + 1 }, (_, i) => { const a = a0 + (a1 - a0) * i / n; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; });
export const circlePts = (cx, cy, r, n = 64) => arcPts(cx, cy, r, 0, PI * 2, n).slice(0, -1);
// Rounded rectangle: the rink's corners have a 28 ft radius.
export const roundPts = (x0, y0, x1, y1, r, n = 10) => [
  ...arcPts(x1 - r, y0 + r, r, -PI / 2, 0, n), ...arcPts(x1 - r, y1 - r, r, 0, PI / 2, n),
  ...arcPts(x0 + r, y1 - r, r, PI / 2, PI, n), ...arcPts(x0 + r, y0 + r, r, PI, PI * 1.5, n),
];
const boardPts = () => roundPts(0, 0, 200, 85, 28, 14);
export const DOTS = {
  center: [100, 42.5],
  end: [[31, 20.5], [31, 64.5], [169, 20.5], [169, 64.5]],
  neutral: [[81, 20.5], [81, 64.5], [119, 20.5], [119, 64.5]],
};
export const NET = { left: 11, right: 189 };
// Off the ice: benches along the far boards, penalty boxes across from them along the near boards.
export const BENCHES = { home: [62, 92], away: [108, 138] };
export const BOXES = { home: [85, 95], away: [105, 115] };

// Fit a drawing to an area of the ice (feet), leaving room above for upright players. pad: [top, right, bottom, left] in feet.
export function fit(svg, V, area, pad = [2, 2, 2, 2]) {
  const pts = [];
  for (const [x, y] of [[area[0], area[1]], [area[2], area[1]], [area[2], area[3]], [area[0], area[3]]]) {
    pts.push(V.P(x, y)); if (!V.top) pts.push(V.P(x, y, 9));
  }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const w = x1 - x0, f = w / (area[2] - area[0]);
  x0 -= pad[3] * f; x1 += pad[1] * f; y0 -= pad[0] * f; y1 += pad[2] * f;
  svg.setAttribute('viewBox', [x0, y0, x1 - x0, y1 - y0].map(r2).join(' '));
  // --k: how wide the drawing is against the overhead one, so labels keep one size on screen.
  svg.style.setProperty('--k', r2((x1 - x0) / (area[2] - area[0] + pad[1] + pad[3])));
  svg.classList.toggle('seat', !V.top);
}

// The rink. Groups carry class names (g-boards, g-goal, g-crease, g-blue, g-red, g-marks, g-bench) so a lesson can
// light them up one at a time. Options: zones (tint the end zones), off (benches and penalty boxes).
let clipN = 0;
export function drawRink(svg, V, { off = false, zones = true, walls = true } = {}) {
  const id = 'rk' + (++clipN);
  const board = pathOf(V, boardPts());
  el(el(el(svg, 'defs'), 'clipPath', { id }), 'path', { d: board });
  if (off) bench(svg, V, 'far');
  const gBoards = el(svg, 'g', { class: 'g-boards' });
  el(gBoards, 'path', { class: 'ice', d: board });
  const ice = el(svg, 'g', { 'clip-path': `url(#${id})` });
  if (zones) {
    const z = el(ice, 'g', { class: 'g-zones' });
    el(z, 'path', { class: 'zone z-left', d: pathOf(V, rectPts(-5, -5, 80, 95)) });
    el(z, 'path', { class: 'zone z-mid', d: pathOf(V, rectPts(76, -5, 48, 95)) });
    el(z, 'path', { class: 'zone z-right', d: pathOf(V, rectPts(125, -5, 80, 95)) });
  }
  const marks = el(ice, 'g', { class: 'g-marks' });
  for (const [cx, cy] of DOTS.end) {
    el(marks, 'path', { class: 'ring-r', d: pathOf(V, circlePts(cx, cy, 15)) });
    el(marks, 'path', { class: 'dot-r', d: pathOf(V, circlePts(cx, cy, 1, 16)) });
  }
  for (const [cx, cy] of DOTS.neutral) el(marks, 'path', { class: 'dot-r', d: pathOf(V, circlePts(cx, cy, 1, 16)) });
  el(marks, 'path', { class: 'ring-b', d: pathOf(V, circlePts(100, 42.5, 15)) });
  el(marks, 'path', { class: 'dot-b', d: pathOf(V, circlePts(100, 42.5, 0.6, 12)) });
  for (const gx of [NET.left, NET.right]) {
    const s = gx < 100 ? -1 : 1;
    el(marks, 'path', { class: 'trap', d: pathOf(V, [[gx, 31.5], [gx + s * 11, 28.5]], false) + pathOf(V, [[gx, 53.5], [gx + s * 11, 56.5]], false) });
  }
  const goal = el(ice, 'g', { class: 'g-goal' });
  const crease = el(ice, 'g', { class: 'g-crease' });
  for (const gx of [NET.left, NET.right]) {
    el(goal, 'path', { class: 'goal', d: pathOf(V, rectPts(gx - 0.2, -2, 0.4, 89)) });
    const out = gx < 100 ? 1 : -1;
    el(crease, 'path', { class: 'crease', d: pathOf(V, arcPts(gx, 42.5, 6, -PI / 2 * out, PI / 2 * out, 16)) });
  }
  const blue = el(ice, 'g', { class: 'g-blue' });
  el(blue, 'path', { class: 'bl', d: pathOf(V, rectPts(75, -2, 1, 89)) });
  el(blue, 'path', { class: 'bl', d: pathOf(V, rectPts(124, -2, 1, 89)) });
  const red = el(ice, 'g', { class: 'g-red' });
  el(red, 'path', { class: 'rl', d: pathOf(V, rectPts(99.5, -2, 1, 89)) });
  // Nets: a frame from above; posts and a crossbar 4 ft high from a seat.
  const nets = el(svg, 'g', { class: 'g-goal g-nets' });
  for (const gx of [NET.left, NET.right]) {
    const out = gx < 100 ? -1 : 1;
    if (V.top) el(nets, 'path', { class: 'net', d: pathOf(V, [[gx, 39.5], [gx + out * 3.4, 39.5], [gx + out * 3.4, 45.5], [gx, 45.5]], false) });
    else {
      el(nets, 'path', { class: 'net-back', d: pathOf(V, [[gx, 39.5, 0], [gx + out * 3.4, 40.5, 0], [gx + out * 3.4, 44.5, 0], [gx, 45.5, 0], [gx, 45.5, 4], [gx + out * 1.4, 44.5, 4], [gx + out * 1.4, 40.5, 4], [gx, 39.5, 4]]) });
      el(nets, 'path', { class: 'net', d: pathOf(V, [[gx, 39.5, 0], [gx, 39.5, 4], [gx, 45.5, 4], [gx, 45.5, 0]], false) });
    }
  }
  if (walls && !V.top) wall(gBoards, V);
  if (off) bench(svg, V, 'near');
  return { ice };
}

// The boards seen from a seat: a low wall (42 inches) all the way round. Only the far half is drawn, because the near
// half would stand in front of the ice it surrounds.
function wall(g, V) {
  const pts = boardPts(), w = el(g, 'g', { class: 'walls' });
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    if (V.depth(mid[0], mid[1]) < V.depth(100, 42.5)) continue;
    el(w, 'path', { class: 'wall', d: pathOf(V, [[a[0], a[1], 0], [b[0], b[1], 0], [b[0], b[1], 3.5], [a[0], a[1], 3.5]]) });
  }
}

// Benches (far side) and penalty boxes (near side), drawn outside the boards.
function bench(svg, V, side) {
  const g = el(svg, 'g', { class: 'g-bench' });
  if (side === 'far') {
    for (const [x0, x1] of Object.values(BENCHES)) el(g, 'path', { class: 'bench', d: pathOf(V, rectPts(x0, -6, x1 - x0, 5)) });
  } else {
    for (const [x0, x1] of Object.values(BOXES)) el(g, 'path', { class: 'box', d: pathOf(V, rectPts(x0, 86, x1 - x0, 5)) });
    el(g, 'path', { class: 'box-mid', d: pathOf(V, rectPts(96.5, 86, 7, 5)) });
  }
  return g;
}

// A label at a spot on the ice (or just above it, at height z).
export const label = (g, V, cls, x, y, s, z = 0) => {
  const p = V.P(x, y, z);
  return el(g, 'text', { class: cls, x: r2(p[0]), y: r2(p[1]), 'text-anchor': 'middle' }, s);
};

// ---------- People and the puck ----------
// A dot from above; a small upright figure from a seat. kind: home (dark sweater), away (white), ref (stripes);
// goalies are drawn wider. Each has set(x, y) and remembers where it is.
export function figure(g, V, kind, { goalie = false, tag = '' } = {}) {
  const grp = el(g, 'g', { class: `fig ${kind}${goalie ? ' gk' : ''}` });
  const o = { g: grp, V, x: 0, y: 0, kind, goalie };
  if (V.top) {
    el(grp, 'circle', { class: 'halo', r: 3.4 });
    el(grp, 'circle', { class: 'disc', r: goalie ? 2.2 : 1.8 });
    if (tag) el(grp, 'text', { class: 'tag' }, tag);
    o.set = (x, y) => { o.x = x; o.y = y; grp.setAttribute('transform', `translate(${r2(x)} ${r2(y)})`); };
  } else {
    const halo = el(grp, 'ellipse', { class: 'halo' });
    const sh = el(grp, 'ellipse', { class: 'shadow' });
    const legs = el(grp, 'path', { class: 'legs' });
    const body = el(grp, 'path', { class: 'body' });
    const inner = el(grp, 'path', { class: 'inner' });
    const head = el(grp, 'circle', { class: 'head' });
    const t = tag ? el(grp, 'text', { class: 'tag' }, tag) : null;
    const wide = goalie ? 1.35 : 1;
    o.set = (x, y) => {
      o.x = x; o.y = y;
      const f = V.P(x, y), hip = V.P(x, y, 2.4), sh2 = V.P(x, y, 5.6), hd = V.P(x, y, 6.6), s = f[2];
      halo.setAttribute('cx', r2(f[0])); halo.setAttribute('cy', r2(f[1])); halo.setAttribute('rx', r2(3.6 * s)); halo.setAttribute('ry', r2(1.5 * s));
      sh.setAttribute('cx', r2(f[0])); sh.setAttribute('cy', r2(f[1])); sh.setAttribute('rx', r2(1.8 * s * wide)); sh.setAttribute('ry', r2(0.7 * s));
      legs.setAttribute('d', `M${r2(f[0] - 0.55 * s * wide)} ${r2(f[1])}L${r2(hip[0])} ${r2(hip[1])}L${r2(f[0] + 0.55 * s * wide)} ${r2(f[1])}`);
      legs.setAttribute('stroke-width', r2(0.9 * s * wide));
      const d = `M${r2(hip[0])} ${r2(hip[1])}L${r2(sh2[0])} ${r2(sh2[1])}`;
      body.setAttribute('d', d); body.setAttribute('stroke-width', r2(3.1 * s * wide));
      inner.setAttribute('d', d); inner.setAttribute('stroke-width', r2(2.1 * s * wide));
      head.setAttribute('cx', r2(hd[0])); head.setAttribute('cy', r2(hd[1])); head.setAttribute('r', r2(1.05 * s));
      if (t) { t.setAttribute('x', r2((hip[0] + sh2[0]) / 2)); t.setAttribute('y', r2((hip[1] + sh2[1]) / 2)); t.style.fontSize = r2(2.2 * s) + 'px'; }
    };
  }
  return o;
}
export function puck(g, V) {
  const p = el(g, 'ellipse', { class: 'puck' });
  const o = { g: p, x: 0, y: 0 };
  o.set = (x, y) => {
    o.x = x; o.y = y;
    const q = V.P(x, y), s = q[2];
    p.setAttribute('cx', r2(q[0])); p.setAttribute('cy', r2(q[1]));
    p.setAttribute('rx', r2(0.9 * s)); p.setAttribute('ry', r2((V.top ? 0.9 : 0.42) * s));
  };
  return o;
}
// Draw people back to front from a seat (from above the order doesn't matter).
export const sortDepth = (V, g, things) => {
  if (V.top) return;
  things.slice().sort((a, b) => V.depth(b.x, b.y) - V.depth(a.x, a.y)).forEach((t) => g.appendChild(t.g));
};

export const toSvg = (svg, e) => { const p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); };
export const ease = (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
export const lerp = (a, b, k) => a + (b - a) * k;

// Start something once it scrolls into view (straight away without motion, or with no IntersectionObserver).
export function whenSeen(node, fn, threshold = 0.4) {
  if (!motion() || !('IntersectionObserver' in window)) return;
  const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { io.disconnect(); setTimeout(fn, 300); } }, { threshold });
  io.observe(node);
}
