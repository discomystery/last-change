// Where each game is played and how far a team flies to get there, for the team page's season map
// (components/SeasonMap.astro). A game is placed by its venue, not by the home team: the Global Series games in Europe
// and any other game more than 75 miles from the home club's arena get their own city. Outdoor games near the home rink
// stay in that city. Distances are great-circle miles, home to the first game, game to game, and back home at the end.
import arenas from '../data/arenas.json';

export type Venue = { city: string; country: string; ll: [number, number] };
export const TEAM_LL = arenas.teams as Record<string, [number, number]>;
export const ELSEWHERE = arenas.elsewhere as Record<string, Venue>;
export const AWAY_MILES = 75;

type Sched = { id: number; date: string; start: string; home: string; away: string; venue?: string | null };

export function milesBetween(a: [number, number], b: [number, number]) {
  const r = Math.PI / 180, [p1, l1] = [a[0] * r, a[1] * r], [p2, l2] = [b[0] * r, b[1] * r];
  return 3958.8 * 2 * Math.asin(Math.sqrt(Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((l2 - l1) / 2) ** 2));
}

// The place a game is played: a club's abbreviation for its own city, or "@" plus the venue name for anywhere else.
export function placeKey(g: Sched): string {
  const v = g.venue ? ELSEWHERE[g.venue] : undefined;
  return v && TEAM_LL[g.home] && milesBetween(v.ll, TEAM_LL[g.home]) > AWAY_MILES ? `@${g.venue}` : g.home;
}
export const placeLL = (key: string): [number, number] => (key.startsWith('@') ? ELSEWHERE[key.slice(1)].ll : TEAM_LL[key]);

// A team's games in order (regular season and playoffs), each with where it is played.
export function teamGames<T extends Sched>(team: string, games: T[]) {
  return games
    .filter((g) => (g.home === team || g.away === team) && /^\d{4}0[23]/.test(String(g.id)))
    .sort((a, b) => a.start.localeCompare(b.start))
    .map((g) => ({ ...g, place: placeKey(g) }));
}

// Miles flown over the whole schedule, home and back.
export function seasonMiles(team: string, games: Sched[]) {
  const gs = teamGames(team, games);
  let at = team, mi = 0;
  for (const g of gs) { mi += milesBetween(placeLL(at), placeLL(g.place)); at = g.place; }
  return mi + milesBetween(placeLL(at), placeLL(team));
}

// Every club's full-schedule miles, most first.
export const milesRanking = (teams: string[], games: Sched[]) =>
  teams.map((t) => ({ team: t, miles: Math.round(seasonMiles(t, games)) })).sort((a, b) => b.miles - a.miles);

// Venues in the schedule that are neither a club's usual arena nor listed above, so the build can say so.
export function unknownVenues(games: Sched[]) {
  const usual: Record<string, Record<string, number>> = {};
  for (const g of games) if (g.venue) ((usual[g.home] ??= {})[g.venue] = (usual[g.home][g.venue] ?? 0) + 1);
  const out = new Set<string>();
  for (const g of games) {
    if (!g.venue || ELSEWHERE[g.venue]) continue;
    const top = Object.entries(usual[g.home] ?? {}).sort((a, b) => b[1] - a[1])[0];
    if (top && top[0] !== g.venue) out.add(`${g.venue} (${g.away} at ${g.home}, ${g.date})`);
  }
  return [...out];
}
