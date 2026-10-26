// Small country flags for player bios, copied at build time from the flag-icons package (MIT), one file per country
// the bio line knows (lib/flags.ts), so a page loads only the flag it shows.
import type { APIRoute } from 'astro';
import { ISO2 } from '../../lib/flags';

const files = import.meta.glob('../../../node_modules/flag-icons/flags/4x3/*.svg', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const byCode = Object.fromEntries(Object.entries(files).map(([f, svg]) => [f.split('/').pop()!.replace('.svg', ''), svg]));

export function getStaticPaths() {
  return [...new Set(Object.values(ISO2))].filter((cc) => byCode[cc]).map((cc) => ({ params: { cc } }));
}

export const GET: APIRoute = ({ params }) => new Response(byCode[params.cc!], { headers: { 'Content-Type': 'image/svg+xml' } });
