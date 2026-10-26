import { defineConfig } from 'astro/config';
import ogImages from './og/integration.mjs';
import slim from './slim/integration.mjs';

// The live site is served from the root of its own domain. To build for a sub-path instead
// (the old discomystery.github.io/last-change/ address), set SITE_URL and SITE_BASE.
export default defineConfig({
  site: process.env.SITE_URL || 'https://sparelinechange.com',
  base: process.env.SITE_BASE || '/',
  trailingSlash: 'always',
  // Link-preview images for every page (og/), drawn once the pages are built; then the build is trimmed (slim/).
  integrations: [ogImages(), slim()],
});
