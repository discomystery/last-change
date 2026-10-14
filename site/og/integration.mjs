// After `astro build`, draws every page's link-preview image (og/render.mjs) from the card the page wrote into itself
// (<script type="application/json" data-og>, see Base.astro and lib/og.ts) and saves it as dist/og/{page}.png, the
// address the page's og:image tag names. Split across worker threads: about 0.2 s a card on one core. Cards already
// drawn by an earlier build (same card, same drawing code) are copied from og/.cache instead, which the workflows keep
// between runs (actions/cache), so a nightly redraws only what changed.
import { Worker } from 'node:worker_threads';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';
import { cpus } from 'node:os';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const BATCH = 25;
const CARD = /<script type="application\/json" data-og>([\s\S]*?)<\/script>/;

function* htmlFiles(dir) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) yield* htmlFiles(p);
    else if (f === 'index.html') yield p;
  }
}

export default function ogImages() {
  return {
    name: 'og-images',
    hooks: {
      'astro:build:done': async ({ dir, logger }) => {
        const root = fileURLToPath(dir);
        const cache = fileURLToPath(new URL('./.cache/', import.meta.url));
        mkdirSync(cache, { recursive: true });
        const code = ['render.mjs', 'worker.mjs'].map((f) => readFileSync(new URL(`./${f}`, import.meta.url))).join('');
        const jobs = [], keep = new Set();
        let reused = 0;
        for (const file of htmlFiles(root)) {
          const m = readFileSync(file, 'utf8').match(CARD);
          if (!m) continue;
          const route = relative(root, join(file, '..')).split(sep).join('/');
          const out = join(root, 'og', `${route || 'home'}.png`);
          const key = `${createHash('sha1').update(code).update(m[1]).digest('hex')}.png`;
          keep.add(key);
          if (existsSync(join(cache, key))) {
            mkdirSync(dirname(out), { recursive: true });
            copyFileSync(join(cache, key), out);
            reused++;
          } else jobs.push({ out, spec: JSON.parse(m[1]), cached: join(cache, key) });
        }
        // Only this build's cards stay in the cache.
        for (const f of readdirSync(cache)) if (!keep.has(f)) rmSync(join(cache, f));
        const t = Date.now();
        // Each worker draws a small batch and exits: resvg keeps some native memory per image (about 15 MB) until its
        // thread ends, so long-lived workers would run the machine out of memory.
        const n = Math.max(1, Math.min(cpus().length, 8));
        if (!jobs.length) return logger.info(`${reused} link-preview images, all unchanged`);
        const batches = [];
        for (let i = 0; i < jobs.length; i += BATCH) batches.push(jobs.slice(i, i + BATCH));
        const results = [];
        const run = (part) => new Promise((ok, bad) => {
          const w = new Worker(new URL('./worker.mjs', import.meta.url), { workerData: { jobs: part } });
          w.once('message', ok);
          w.once('error', bad);
        });
        await Promise.all(Array.from({ length: n }, async () => {
          while (batches.length) results.push(await run(batches.shift()));
        }));
        const failed = results.reduce((a, r) => a + r.failed, 0);
        logger.info(`${jobs.length - failed} link-preview images drawn in ${((Date.now() - t) / 1000).toFixed(1)} s on ${n} threads, ${reused} unchanged`);
        if (failed) throw new Error(`${failed} link-preview images failed`);
      },
    },
  };
}
