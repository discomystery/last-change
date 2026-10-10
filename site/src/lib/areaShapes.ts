// Shapes of the shot areas on the player page's zone map, shared by every AreaMap on a page.
// The rings and wedges match pipeline/export/players.py `area()` (RINGS, WEDGES); keep the two in step.
// The outlines are drawn once per page by AreaShapes.astro and every map points at them, because they are the same
// for every player and were most of the page's size when each map carried its own copy.
export const sx = (y: number) => 42.5 - y;
export const sy = (x: number) => 100 - x;
const [CREASE, INNER, MIDDLE, OUTER] = [6, 22, 46, 60];
const [LOW, SLOT, CORNER] = [28, 15, 72];
const FAR = 95; // past every board, so the rink outline trims the open-ended areas
export type Pt = [number, number];
// Rink point (feet) at distance d from the middle of the net, a degrees off straight out (positive toward his left).
export const polar = (d: number, a: number): Pt => { const r = (a * Math.PI) / 180; return [sx(d * Math.sin(r)), sy(89 - d * Math.cos(r))]; };
// Outline of the ring between d0 and d1 from angle a0 to a1, arcs sampled every 3 degrees (within 0.05 ft of the true arc).
function sector(d0: number, d1: number, a0: number, a1: number): Pt[] {
  const steps = Math.max(2, Math.ceil(Math.abs(a1 - a0) / 3));
  const arc = (d: number, from: number, to: number) => Array.from({ length: steps + 1 }, (_, i) => polar(d, from + ((to - from) * i) / steps));
  return d0 > 0 ? [...arc(d1, a0, a1), ...arc(d0, a1, a0)] : [...arc(d1, a0, a1), polar(0, 0)];
}
// Closed path through the outline with every sharp corner swapped for a curve of about r feet.
function soft(pts: Pt[], r: number): string {
  const n = pts.length;
  const dist = (p: Pt, q: Pt) => Math.hypot(q[0] - p[0], q[1] - p[1]);
  const S = [0];
  for (let i = 1; i <= n; i++) S.push(S[i - 1] + dist(pts[i - 1], pts[i % n]));
  const L = S[n];
  const turn = (i: number) => {
    const [p, q, w] = [pts[(i + n - 1) % n], pts[i], pts[(i + 1) % n]];
    const a = Math.atan2(q[1] - p[1], q[0] - p[0]), b = Math.atan2(w[1] - q[1], w[0] - q[0]);
    return Math.abs(((b - a + 3 * Math.PI) % (2 * Math.PI)) - Math.PI);
  };
  const corners = pts.map((_, i) => i).filter((i) => turn(i) > 0.4);
  const at = (s: number): Pt => {
    s = ((s % L) + L) % L;
    let i = 0;
    while (S[i + 1] < s) i++;
    const t = (s - S[i]) / (S[i + 1] - S[i] || 1), [p, q] = [pts[i], pts[(i + 1) % n]];
    return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
  };
  const gap = (a: number, b: number) => (((S[b] - S[a]) % L) + L) % L || L;
  const k = corners.length;
  const rr = corners.map((c, j) => Math.min(r, 0.45 * gap(corners[(j + k - 1) % k], c), 0.45 * gap(c, corners[(j + 1) % k])));
  const f = (p: Pt) => `${+p[0].toFixed(1)} ${+p[1].toFixed(1)}`;
  let d = `M${f(at(S[corners[0]] + rr[0]))}`;
  for (let j = 0; j < k; j++) {
    const c = corners[j], next = corners[(j + 1) % k], rn = rr[(j + 1) % k];
    const end = S[c] + gap(c, next);
    for (let i = (c + 1) % n; i !== next; i = (i + 1) % n) {
      const s = S[c] + gap(c, i);
      if (s > S[c] + rr[j] && s < end - rn) d += ` L${f(pts[i])}`;
    }
    d += ` L${f(at(end - rn))} Q${f(pts[next])} ${f(at(end + rn))}`;
  }
  return `${d} Z`;
}
const both = (key: string, shape: (s: number) => Pt[]) => ({ [`${key}L`]: shape(1), [`${key}R`]: shape(-1) });
const span = (d0: number, d1: number, a0: number, a1: number) => (s: number) => (s > 0 ? sector(d0, d1, a0, a1) : sector(d0, d1, -a1, -a0));
const SHAPES: Record<string, Pt[]> = {
  crease: sector(0, CREASE, -90, 90),
  behind: sector(0, INNER, 90, 270),
  lowSlot: sector(CREASE, INNER, -LOW, LOW),
  ...both('netSide', span(CREASE, INNER, LOW, 90)),
  ...both('corner', span(INNER, FAR, CORNER, 180)),
  highSlot: sector(INNER, MIDDLE, -SLOT, SLOT),
  ...both('circle', span(INNER, MIDDLE, SLOT, CORNER)),
  ...both('outer', span(MIDDLE, OUTER, SLOT, CORNER)),
  point: sector(MIDDLE, FAR, -SLOT, SLOT),
  ...both('point', span(OUTER, FAR, SLOT, CORNER)),
};
export const paths: Record<string, string> = Object.fromEntries(Object.entries(SHAPES).map(([k, pts]) => [k, soft(pts, 3)]));
export const AREA_KEYS = Object.keys(paths);
export const shapeId = (k: string) => `ams-${k}`;
