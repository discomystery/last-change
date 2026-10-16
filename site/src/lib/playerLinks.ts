// Links from a player's name to his page, for every player who has dressed this season.
import index from '../../public/data/players/index.json';

const known = new Set((index as { id: number }[]).map((p) => p.id));
export const playerHref = (id: number | undefined | null) => (id != null && known.has(id) ? `${import.meta.env.BASE_URL}player/${id}/` : null);

// Sweater numbers from the roster listing, for line and pair cards.
const numbers = new Map((index as { id: number; number?: number | null }[]).filter((p) => p.number != null).map((p) => [p.id, p.number as number]));
export const sweater = (id: number | undefined | null) => (id != null ? numbers.get(id) ?? null : null);

// Last names for line and pair cards. A last name shared by two players on the same club
// (team pages) or in the same game (previews) keeps its first initial.
type Entry = { id: number; last?: string; team?: string };
const lastOf = new Map((index as Entry[]).filter((p) => p.last).map((p) => [p.id, p.last as string]));
export const clashes = (teams: string[]) => {
  const seen = new Map<string, number>();
  for (const p of index as Entry[]) if (p.last && p.team && teams.includes(p.team)) seen.set(p.last, (seen.get(p.last) ?? 0) + 1);
  return new Set([...seen].filter(([, n]) => n > 1).map(([l]) => l));
};
export const shortName = (id: number | undefined | null, name: string, clash: Set<string>) => {
  const last = id != null ? lastOf.get(id) : undefined;
  return last && !clash.has(last) ? last : name;
};
