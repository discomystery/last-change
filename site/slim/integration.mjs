// After `astro build`, trims the built site without changing what any page shows or does. GitHub Pages caps a site at
// 1 GB, and on 2026-10-23 the build was 510 MB, growing about 1 MB a game, mostly from things every page repeats:
//
// 1. Identical inline <script> and <style> blocks found on many pages (the spelling switch, settings, section menu...)
//    move into one shared file each under _shared/, named by a hash of their text, and the page points at it. A
//    plain <script src> runs exactly where the inline script did (parser-blocking, same order, same globals), so
//    timing is unchanged; the browser fetches it once and reuses it on every page.
// 2. Astro's scoped-style markers (data-astro-cid-xxxxxxxx on every element) get short names (data-s-1a), the same
//    rename in every HTML, CSS and JS file of the build.
// 3. Tooltip texts repeated on many pages (stat and trait explanations) move into one shared dictionary: the page
//    carries data-tk="n" instead, and a small script loaded first in <head> puts the full data-tip back on each
//    element as the page is parsed (before any page script runs, and before the spelling switch reads it), so the
//    page ends up with exactly the attributes it was built with.
// 4. Identical SVG drawings found on many pages (the logo, the shot-area shapes) move into one shared script each,
//    which the page loads at the drawing's place: it puts the drawing in right there and removes itself, so the page
//    ends up with the same elements in the same place, and the browser fetches the drawing once for every page.
// 5. Data files that only the build reads (previews, careers, game logs...) are left out of the published site; the
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
// A tooltip text joins the dictionary when this many pages carry it and it is at least this long.
const MIN_TIP_PAGES = 20;
const MIN_TIP_CHARS = 30;

// An SVG drawing moves out when this many pages carry the identical drawing and it is at least this long.
const MIN_SVG_PAGES = 20;
const MIN_SVG_BYTES = 1000;

const SERVED_ONLY_INDEX = ['previews', 'recaps'];
const BUILD_ONLY = ['careers', 'gamelog', 'matchups', 'intros', 'lines.json', 'trend.json', 'roster.json',
  'fingerprints.json', 'goalies.json', 'goal_sources.json', 'records.json', 'arena_factors.json', 'war.json'];

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
const TIP = / data-tip="([^"]*)"/g;
const SCRIPTS = /(<script\b[\s\S]*?<\/script>|<style\b[\s\S]*?<\/style>)/;
// Applies fn to the page's markup only, never to the text of its scripts and styles.
const outside = (html, fn) => html.split(SCRIPTS).map((part, i) => (i % 2 ? part : fn(part))).join('');
// Drawings inside these are left alone: a script there would not run (template) or would mean something else.
const KEEP = /(<script\b[\s\S]*?<\/script>|<style\b[\s\S]*?<\/style>|<template\b[\s\S]*?<\/template>|<noscript\b[\s\S]*?<\/noscript>|<textarea\b[\s\S]*?<\/textarea>)/;
const outsideAll = (html, fn) => html.split(KEEP).map((part, i) => (i % 2 ? part : fn(part))).join('');
const SVG = /<svg\b[^>]*>(?:(?!<svg\b)[\s\S])*?<\/svg>/g;
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
const unescape = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) =>
  e[0] === '#' ? String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : +e.slice(1)) : ENT[e] ?? m);
// Puts each data-tip back from the dictionary as elements arrive (while the page is parsed, or later from scripts).
const EXPAND = `const T=window.lcTips;
const put=(el)=>{el.setAttribute('data-tip',T[parseInt(el.getAttribute('data-tk'),36)]);el.removeAttribute('data-tk');};
const scan=(n)=>{if(n.nodeType!==1)return;if(n.hasAttribute('data-tk'))put(n);if(n.firstElementChild)n.querySelectorAll('[data-tk]').forEach(put);};
new MutationObserver((l)=>l.forEach((m)=>m.addedNodes.forEach(scan))).observe(document.documentElement,{childList:true,subtree:true});`;

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
        const svgPages = new Map();
        for (const f of pages) {
          const once = new Set();
          const text = rename(readFileSync(f, 'utf8'));
          for (const m of text.matchAll(BLOCK)) {
            if (m[2].length < MIN_BYTES || once.has(m[0])) continue;
            once.add(m[0]);
            seen.set(m[0], (seen.get(m[0]) ?? 0) + 1);
          }
          const drawings = new Set();
          outsideAll(text, (part) => { for (const m of part.matchAll(SVG)) if (m[0].length >= MIN_SVG_BYTES) drawings.add(m[0]); return part; });
          for (const v of drawings) svgPages.set(v, (svgPages.get(v) ?? 0) + 1);
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

        // Tooltip texts that repeat across pages, most used first so they get the shortest keys.
        const tipPages = new Map();
        for (const f of pages) {
          const once = new Set();
          outside(readFileSync(f, 'utf8'), (part) => { for (const m of part.matchAll(TIP)) once.add(m[1]); return part; });
          for (const t of once) tipPages.set(t, (tipPages.get(t) ?? 0) + 1);
        }
        const tips = [...tipPages].filter(([t, n]) => n >= MIN_TIP_PAGES && unescape(t).length >= MIN_TIP_CHARS)
          .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([t]) => t);
        const tipKey = new Map(tips.map((t, i) => [t, i.toString(36)]));
        let tipTag = '';
        if (tips.length) {
          const body = `(()=>{window.lcTips=${JSON.stringify(tips.map(unescape))};\n${EXPAND}})();\n`;
          const name = `tips-${hash(body)}.js`;
          mkdirSync(out, { recursive: true });
          writeFileSync(join(out, name), body);
          tipTag = `<script src="${base}_shared/${name}"></script>`;
        }
        const shortTips = (html) => {
          let used = false;
          const next = outside(html, (part) => part.replace(TIP, (m, t) => {
            const k = tipKey.get(t);
            if (k === undefined) return m;
            used = true;
            return ` data-tk="${k}"`;
          }));
          if (!used) return html;
          const at = /<meta charset="[^"]*">/.test(next) ? /<meta charset="[^"]*">/ : /<head[^>]*>/;
          return next.replace(at, (h) => h + tipTag);
        };

        // Each repeated drawing as a script that writes it in at its own place.
        const frags = new Map();
        for (const [v, n] of svgPages) {
          if (n < MIN_SVG_PAGES) continue;
          const body = `document.currentScript.insertAdjacentHTML('beforebegin',${JSON.stringify(v).replace(/</g, '\\u003c')});document.currentScript.remove();\n`;
          const name = `svg-${hash(body)}.js`;
          mkdirSync(out, { recursive: true });
          writeFileSync(join(out, name), body);
          frags.set(v, `<script src="${base}_shared/${name}"></script>`);
        }
        const shareDrawings = (html) => (frags.size ? outsideAll(html, (part) => part.replace(SVG, (v) => frags.get(v) ?? v)) : html);

        for (const f of files) {
          const text = readFileSync(f, 'utf8');
          let next = rename(text);
          if (f.endsWith('.html')) next = shortTips(shareDrawings(next.replace(BLOCK, (b) => shared.get(b) ?? b)));
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
        logger.info(`${shared.size} shared blocks, ${tips.length} shared tooltips, ${frags.size} shared drawings, ${short.size} style markers renamed: ${pages.length} pages and assets ${mb(before)} MB -> ${mb(after)} MB; ${mb(dropped)} MB of build-only data left out`);
      },
    },
  };
}
