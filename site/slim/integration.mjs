// After `astro build`, trims the built site without changing what any page shows or does. GitHub Pages caps a site at
// 1 GB, and on 2026-10-23 the build was 510 MB, growing about 1 MB a game, mostly from things every page repeats:
//
// 1. Identical inline <script> and <style> blocks found on many pages (the spelling switch, settings, section menu...)
//    move into one shared file each under _shared/, named by a hash of their text, and the page points at it. A
//    plain <script src> runs exactly where the inline script did (parser-blocking, same order, same globals), so
//    timing is unchanged; the browser fetches it once and reuses it on every page.
// 2. Astro's scoped-style markers (data-astro-cid-xxxxxxxx on every element) get short names (data-s-1a), the same
//    rename in every HTML, CSS and JS file of the build.
// 3. Data files that only the build reads (previews, careers, game logs...) are left out of the published site; the
//    pipeline keeps them on the data branch. Files the browser fetches stay: see SERVED_ONLY_INDEX and BUILD_ONLY.
import { mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// A block moves out when this many pages carry the identical text and it is long enough to be worth a request.
const MIN_PAGES = 20;
const MIN_BYTES = 300;

// Folders of data/ the browser never fetches. For the first group only the folder's index.json is read in the
// browser (Games, Home); the per-game files are read at build time only.
const SERVED_ONLY_INDEX = ['previews', 'recaps'];
const BUILD_ONLY = ['careers', 'gamelog', 'matchups', 'intros', 'lines.json', 'trend.json', 'roster.json',
  'fingerprints.json', 'goalies.json', 'goal_sources.json', 'records.json', 'arena_factors.json'];

function* walk(dir, skip = new Set()) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) { if (!skip.has(p)) yield* walk(p, skip); }
    else yield p;
  }
}

const hash = (s) => createHash('sha256').update(s).digest('hex').slice(0, 12);
const BLOCK = /<(script|style)>([\s\S]*?)<\/\1>/g;
const CID = /data-astro-cid-([a-z0-9]+)/g;

export default function slim() {
  let base = '/';
  return {
    name: 'slim',
    hooks: {
      'astro:config:done': ({ config }) => { base = config.base.endsWith('/') ? config.base : `${config.base}/`; },
      'astro:build:done': async ({ dir, logger }) => {
        const root = fileURLToPath(dir);
        const skip = new Set([join(root, 'data'), join(root, 'og'), join(root, 'geo')]);
        const files = [...walk(root, skip)].filter((f) => /\.(html|css|js)$/.test(f));
        const pages = files.filter((f) => f.endsWith('.html'));
        let before = 0, after = 0;

        // Every scoped-style marker in the build, then which inline blocks repeat once the markers are renamed.
        const cids = new Set();
        for (const f of files) for (const m of readFileSync(f, 'utf8').matchAll(CID)) cids.add(m[1]);
        const short = new Map([...cids].sort().map((c, i) => [c, i.toString(36)]));
        const rename = (text) => text.replace(CID, (_, c) => `data-s-${short.get(c)}`);
        const seen = new Map();
        for (const f of pages) {
          const once = new Set();
          for (const m of rename(readFileSync(f, 'utf8')).matchAll(BLOCK)) {
            if (m[2].length < MIN_BYTES || once.has(m[0])) continue;
            once.add(m[0]);
            seen.set(m[0], (seen.get(m[0]) ?? 0) + 1);
          }
        }
        const shared = new Map();
        const out = join(root, '_shared');
        for (const [block, n] of seen) {
          if (n < MIN_PAGES) continue;
          const [, kind, body] = block.match(/^<(script|style)>([\s\S]*)<\/\1>$/);
          const name = `${hash(body)}.${kind === 'script' ? 'js' : 'css'}`;
          mkdirSync(out, { recursive: true });
          writeFileSync(join(out, name), body);
          shared.set(block, kind === 'script'
            ? `<script src="${base}_shared/${name}"></script>`
            : `<link rel="stylesheet" href="${base}_shared/${name}">`);
        }
        for (const f of files) {
          const text = readFileSync(f, 'utf8');
          let next = rename(text);
          if (f.endsWith('.html')) next = next.replace(BLOCK, (b) => shared.get(b) ?? b);
          before += Buffer.byteLength(text); after += Buffer.byteLength(next);
          if (next !== text) writeFileSync(f, next);
        }

        let dropped = 0;
        const data = join(root, 'data');
        const drop = (p) => { for (const f of statSync(p).isDirectory() ? walk(p) : [p]) dropped += statSync(f).size; rmSync(p, { recursive: true, force: true }); };
        for (const d of SERVED_ONLY_INDEX) {
          const p = join(data, d);
          if (!existsSync(p)) continue;
          for (const f of readdirSync(p)) if (f !== 'index.json') drop(join(p, f));
        }
        for (const d of BUILD_ONLY) if (existsSync(join(data, d))) drop(join(data, d));

        const mb = (b) => (b / 1e6).toFixed(1);
        logger.info(`${shared.size} shared blocks, ${short.size} style markers renamed: ${pages.length} pages and assets ${mb(before)} MB -> ${mb(after)} MB; ${mb(dropped)} MB of build-only data left out`);
      },
    },
  };
}
