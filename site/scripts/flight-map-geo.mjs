// Map shapes for the team page's season map (components/SeasonMap.astro), written to public/geo/flight-map.json.
// Run by hand only when the shapes need to change: `node scripts/flight-map-geo.mjs` from site/.
// Three parts:
//   flat:  North America as SVG paths in a 1000 x 600 frame (conic conformal fitted to the 32 arenas), plus the projection,
//          so the page can place any venue with the same numbers.
//   inset: a small Europe map set in the Atlantic east of the Carolinas, where the flat frame has no land, with its projection.
//   globe: world land as longitude/latitude rings for the spinning globe (thinned to about a quarter of a degree).
// Coastlines: Natural Earth 1:50m via world-atlas.
import fs from 'node:fs';
import * as topo from 'topojson-client';
import * as d3 from 'd3-geo';

const here = (p) => new URL(p, import.meta.url);
const arenas = JSON.parse(fs.readFileSync(here('../src/data/arenas.json'))).teams;
const world = JSON.parse(fs.readFileSync(here('../node_modules/world-atlas/countries-50m.json')));
const landWorld = JSON.parse(fs.readFileSync(here('../node_modules/world-atlas/land-50m.json')));
const land = topo.feature(world, world.objects.land);
const borders = topo.mesh(world, world.objects.countries, (a, b) => a !== b);
const r1 = (v) => Math.round(v * 10) / 10;

// Projected rings as flat [x, y, x, y, ...] arrays, dropping points closer than `gap` to the last one kept.
function rings(geom, proj, gap, minLen) {
  const out = [];
  d3.geoStream(geom, proj.stream({
    polygonStart() {}, polygonEnd() {}, sphere() {},
    lineStart() { this.cur = []; },
    point(x, y) { const c = this.cur, l = c.length; if (l >= 2 && (x - c[l - 2]) ** 2 + (y - c[l - 1]) ** 2 < gap) return; c.push(r1(x), r1(y)); },
    lineEnd() { if (this.cur.length >= minLen) out.push(this.cur); },
  }));
  return out;
}
const params = (p) => ({ parallels: p.parallels(), rotate: p.rotate(), scale: p.scale(), translate: p.translate() });

// Flat North America.
const W = 1000, H = 600;
const pts = { type: 'MultiPoint', coordinates: Object.values(arenas).map(([la, lo]) => [lo, la]) };
const flat = d3.geoConicConformal().parallels([33, 50]).rotate([97, 0]).fitExtent([[60, 50], [W - 60, H - 40]], pts);
flat.clipExtent([[-300, -250], [1300, 850]]);
const inFrame = (r) => {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (let i = 0; i < r.length; i += 2) { x0 = Math.min(x0, r[i]); x1 = Math.max(x1, r[i]); y0 = Math.min(y0, r[i + 1]); y1 = Math.max(y1, r[i + 1]); }
  return x1 > -600 && x0 < 1600 && y1 > -500 && y0 < 1100 && x1 - x0 + y1 - y0 > 6;
};
const flatLand = rings(land, flat, 6, 16).filter(inFrame), flatBorder = rings(borders, flat, 6, 6).filter(inFrame);

// Europe inset.
const BOX = [884, 296, 112, 124];
const ext = { type: 'MultiPoint', coordinates: [[-9, 43], [28, 43], [-9, 66], [30, 66]] };
const inset = d3.geoConicConformal().parallels([45, 62]).rotate([-10, 0]).fitExtent([[BOX[0] + 4, BOX[1] + 14], [BOX[0] + BOX[2] - 4, BOX[1] + BOX[3] - 4]], ext);
const insetParams = params(inset);
inset.clipExtent([[BOX[0], BOX[1]], [BOX[0] + BOX[2], BOX[1] + BOX[3]]]);
const insetLand = rings(land, inset, 0.15, 8), insetBorder = rings(borders, inset, 0.15, 4);

// Globe land, thinned; Antarctica left off (the camera never looks there, and thinning can flip its ring inside out).
const globe = [];
const gl = topo.feature(landWorld, landWorld.objects.land);
for (const poly of gl.features.flatMap((f) => (f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates))) {
  const P = [];
  for (const ring of poly) {
    const k = []; let lx = 1e9, ly = 1e9;
    ring.forEach(([x, y], i) => { if (i === ring.length - 1 || Math.hypot(x - lx, y - ly) > 0.22) { k.push(r1(x), r1(y)); lx = x; ly = y; } });
    if (k.length >= 16 && Math.min(...k.filter((_, i) => i % 2)) > -60) P.push(k);
  }
  if (P.length) globe.push(P);
}

const flatParams = params(flat);
const out = {
  source: 'Natural Earth 1:50m coastlines via world-atlas',
  flat: { w: W, h: H, projection: flatParams, land: flatLand, border: flatBorder },
  inset: { box: BOX, projection: insetParams, land: insetLand, border: insetBorder },
  globe,
};
fs.mkdirSync(here('../public/geo/'), { recursive: true });
fs.writeFileSync(here('../public/geo/flight-map.json'), JSON.stringify(out));
console.log('flight-map.json', fs.statSync(here('../public/geo/flight-map.json')).size, 'bytes');
