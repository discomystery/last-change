// Links from a player's name to his page, for every player who has dressed this season.
import index from '../../public/data/players/index.json';

const known = new Set((index as { id: number }[]).map((p) => p.id));
export const playerHref = (id: number | undefined | null) => (id != null && known.has(id) ? `${import.meta.env.BASE_URL}player/${id}/` : null);

// Sweater numbers from the roster listing, for line and pair cards.
const numbers = new Map((index as { id: number; number?: number | null }[]).filter((p) => p.number != null).map((p) => [p.id, p.number as number]));
export const sweater = (id: number | undefined | null) => (id != null ? numbers.get(id) ?? null : null);
