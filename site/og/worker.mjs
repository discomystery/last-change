// One of the integration's workers: draws each card it is handed and writes the PNG.
import { parentPort, workerData } from 'node:worker_threads';
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { render } from './render.mjs';

let failed = 0;
for (const { out, spec, cached } of workerData.jobs) {
  try {
    const png = await render(spec);
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, png);
    copyFileSync(out, cached);
  } catch (e) {
    failed++;
    console.error(`[og] ${out}: ${e.message}`);
  }
}
parentPort.postMessage({ failed });
