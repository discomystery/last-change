// Player portraits: the NHL's own cut-out photos, loaded straight from the league's servers (never copied here).
// Set PORTRAITS to false to take every portrait off the site at once (for example if the NHL asks).
import { readFileSync } from 'node:fs';
import index from '../../public/data/players/index.json';

export const PORTRAITS = true;
// For screenshots where the NHL's photo server is out of reach: PUBLIC_PORTRAIT_STANDIN=1 swaps in a grey stand-in.
const standIn = import.meta.env.PUBLIC_PORTRAIT_STANDIN
  ? `data:image/png;base64,${readFileSync(`${process.cwd()}/scripts/portrait-standin.png`).toString('base64')}`
  : null;

const photos = new Map((index as { id: number; photo?: string | null }[]).map((p) => [p.id, p.photo ?? null]));
export const photoOf = (id: number | null | undefined): string | null => {
  if (!PORTRAITS || id == null || !photos.has(id)) return null;
  return standIn ?? photos.get(id) ?? null;
};
