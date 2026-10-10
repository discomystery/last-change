// Player pages: labels, tooltips, scale ends and plain-language wording for every rating.
import { clock } from './format';

export type Est = { v: number; pct: number; lo: number; hi: number; ok: boolean; of: number; rank: number; vs?: string; index?: number | null; raw?: number; raw_pct?: number };
export type EdgeEst = { v: number; pct: number; ok: boolean; of: number; avg: number; vs?: string; rank?: number };
export type Mode = 'blend' | 'season';
export type Area = { shots: number; goals: number; share: number; avg: number };
export type Misses = { n: number; wide: number; high: number; post: number; short: number; other: number; left: number; right: number; crossbar: number } | null;
export type Unit = { label: string; role?: string | null; both?: boolean; share?: number; mates: { id: number; name: string }[] };
export type Role = {
  team_games: number; gp?: number; toi_rank?: number; toi_of?: number; toi?: { toi: number; toi5: number; toi_pp: number; toi_pk: number };
  line?: { label: string; mates: { id: number; name: string }[]; toi_sec: number | null; share: number; pct: number | null };
  pp?: Unit; pk?: PkRole | null; faceoff?: string; notes: string[];
};
export type PkRole = { kills: number; role: 'starter' | 'second' | 'spot' | null; kills_in?: number; starts?: number; per_kill?: number; draw?: boolean };
/** Penalty-kill roles: kills are loose, so players are grouped by when they go on rather than into fixed units. */
export const pkRoleWord = { starter: 'Starts the kill', second: 'Second wave', spot: 'Spot duty' } as const;
export const pkRoleTip = {
  starter: 'Starts the kill: on the ice when most of the kills he plays begin, usually for the defensive-zone faceoff.',
  second: 'Second wave: kills often, but usually comes on after the first change rather than at the start.',
  spot: 'Spot duty: used in some kills (10% to 40% of them), for at least 20 seconds at a time.',
} as const;
export type Stats = { gp: number; g: number; a: number; p: number; sog: number; toi: number };
export type Skater = {
  id: number; name: string; first: string; last: string; number: number | null; pos: string; team: string; shoots: string | null; age: number | null;
  group: 'F' | 'D'; moved_from: string | null; sub: 'C' | 'W' | 'D'; competition: Competition | null; games: number; games_last: number; generated_at: string; stats: { season?: Stats; last?: Stats };
  traits: Record<string, Record<Mode, Est>>; role: Role; edge: Record<Mode, Record<string, EdgeEst>> | null;
  shots: [number, number, number, number, number][]; areas: Record<Mode, { n: number; areas: Record<string, Area> }>; misses: Record<Mode, Misses>;
};

export const posWord: Record<string, string> = { C: 'Center', L: 'Left wing', R: 'Right wing', D: 'Defenseman', G: 'Goalie' };
export const groupWord = { F: 'forwards', D: 'defensemen' } as const;
/** Who a rating compares him with: all forwards, centers or wingers only (where the two really differ), or defensemen. */
export const peerWord: Record<string, string> = { F: 'forwards', D: 'defensemen', C: 'centers', W: 'wingers' };
export type Competition = Record<Mode, Record<'all' | 'home' | 'road', { v: number; pct: number } | null>>;
const sign = (v: number, d = 2) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`;

/** `up` / `down`: how a comparison reads above and below the middle (EDGE measures are not better or worse, just more or less). */
/** `short`: the number with its unit in a few words, for compact cards; `say` keeps the full sentence for tooltips. */
export type TraitInfo = { label: string; tip: string; lo: string; hi: string; say: (v: number) => string; short: (v: number) => string; group: string; raw?: (v: number) => string; rawShort?: (v: number) => string; up?: string; down?: string };
export const TRAITS: Record<string, TraitInfo> = {
  shooting: { group: 'Scoring', short: (v) => `${v.toFixed(1)} shot attempts /60`, label: 'Shooting volume', lo: 'Rarely shoots', hi: 'Fires away', say: (v) => `${v.toFixed(1)} shot attempts per 60 at 5-on-5`,
    tip: 'Shot attempts he takes himself per 60 minutes at 5-on-5, counting shots that are blocked or miss the net.' },
  chances: { group: 'Scoring', short: (v) => `${v.toFixed(2)} expected goals /60`, label: 'Creating his own chances', lo: 'Few chances', hi: 'Dangerous', say: (v) => `${v.toFixed(2)} expected goals from his own shots per 60 at 5-on-5`,
    tip: 'How dangerous his own shots are, added up: expected goals from shots he takes, per 60 minutes at 5-on-5. Shot volume and shot location both count.' },
  finishing: { group: 'Scoring', short: (v) => `${sign(v, 1)} goals above expected /100 shots`, label: 'Finishing', lo: 'Below expected', hi: 'Sniper', say: (v) => `${sign(v, 1)} goals above expected per 100 shots`,
    tip: 'Goals he scores compared with what an average shooter would score from the same shots, per 100 unblocked shots (empty nets left out). Finishing takes a long time to tell apart from luck, so small samples are pulled hard toward average.' },
  playmaking: { group: 'Scoring', short: (v) => `${v.toFixed(2)} first assists /60`, label: 'Setting up goals', lo: 'Rarely sets up', hi: 'Playmaker', say: (v) => `${v.toFixed(2)} first assists per 60 at 5-on-5`,
    tip: 'First assists (the last pass before a goal) per 60 minutes at 5-on-5.' },
  powerPlay: { group: 'Special teams', short: (v) => `${v.toFixed(2)} expected goals /60`, label: 'Power-play chances', lo: 'Quiet', hi: 'Threat', say: (v) => `${v.toFixed(2)} expected goals from his own shots per 60 on the power play`,
    tip: 'Expected goals from shots he takes himself, per 60 minutes on the power play. Only rated for players with real power-play time. Passers on the power play will rate lower here than their value.' },
  offImpact: { group: 'Driving play at 5-on-5', short: (v) => `${sign(v)} expected goals /60`, label: 'Creating chances', lo: 'Along for the ride', hi: 'Drives play',
    say: (v) => `adds ${sign(v)} expected goals per 60 to his team’s chances at 5-on-5`,
    tip: 'How much better his team’s chances get at 5-on-5 when he is on the ice, in expected goals per 60. It separates him from his linemates and opponents by comparing every shift of this season and last, and allows for where shifts start and the score.' },
  defImpact: { group: 'Driving play at 5-on-5', short: (v) => (Math.abs(v) < 0.01 ? 'no measurable change' : `${v > 0 ? '−' : '+'}${Math.abs(v).toFixed(2)} expected goals against /60`), label: 'Preventing chances', lo: 'Chances against rise', hi: 'Shuts it down',
    say: (v) => (Math.abs(v) < 0.01 ? 'about average: no measurable change in the opponent’s chances at 5-on-5' : v > 0 ? `takes ${v.toFixed(2)} expected goals per 60 off the opponent’s chances at 5-on-5` : `opponents get ${Math.abs(v).toFixed(2)} more expected goals per 60 at 5-on-5 with him out there`),
    tip: 'How much he cuts the opponent’s chances at 5-on-5, in expected goals per 60, after accounting for his linemates, the opponents he faces (so facing top lines is not held against him), where his shifts start and the score. It sees chances, not the quality of defensive plays that public data does not record.' },
  shThreat: { group: 'Special teams', short: (v) => `${v.toFixed(2)} expected goals for /60`, label: 'Shorthanded threat', lo: 'Pure defense', hi: 'Power kill', say: (v) => `his team creates ${v.toFixed(2)} expected goals per 60 while killing penalties with him on the ice`,
    tip: 'Chances his team creates while shorthanded with him on the ice, in expected goals per 60 of penalty-kill time: the “power kill”. It holds up well from season to season, so it is a real skill, not luck. Only rated for players with real penalty-kill time.' },
  hits: { group: 'Physical and puck', short: (v) => `hitting score ${Math.round(v)}`, rawShort: (v) => `${v.toFixed(2)} hits /60`, label: 'Hitting', lo: 'Avoids contact', hi: 'Physical', say: (v) => `a hitting score of ${Math.round(v)}`, raw: (v) => `${v.toFixed(2)} hits recorded per 60`,
    tip: 'Hits per 60 minutes, as an arena-adjusted score (100 is average for his position) because some arenas’ scorers count hits far more generously than others.' },
  blocks: { group: 'Physical and puck', short: (v) => `blocking score ${Math.round(v)}`, rawShort: (v) => `${v.toFixed(2)} blocks /60`, label: 'Shot blocking', lo: 'Rarely blocks', hi: 'Shot blocker', say: (v) => `a shot-blocking score of ${Math.round(v)}`, raw: (v) => `${v.toFixed(2)} blocked shots recorded per 60`,
    tip: 'Opponents’ shots he blocks per 60 minutes, as an arena-adjusted score (100 is average for his position).' },
  takeaways: { group: 'Physical and puck', short: (v) => `takeaway score ${Math.round(v)}`, rawShort: (v) => `${v.toFixed(2)} takeaways /60`, label: 'Takeaways', lo: 'Rare', hi: 'Puck thief', say: (v) => `a takeaway score of ${Math.round(v)}`, raw: (v) => `${v.toFixed(2)} takeaways recorded per 60`,
    tip: 'Times he takes the puck off an opponent per 60 minutes, as an arena-adjusted score (100 is average for his position). Scorers vary a lot on this one.' },
  drawsPenalties: { group: 'Penalties and faceoffs', short: (v) => `${v.toFixed(2)} penalties drawn /60`, label: 'Drawing penalties', lo: 'Rarely', hi: 'Draws calls', say: (v) => `${v.toFixed(2)} penalties drawn per 60`,
    tip: 'Minor and major penalties called on opponents against him, per 60 minutes in all situations.' },
  discipline: { group: 'Penalties and faceoffs', short: (v) => `${v.toFixed(2)} penalties taken /60`, label: 'Staying out of the box', lo: 'Often penalized', hi: 'Clean', say: (v) => `${v.toFixed(2)} penalties taken per 60`,
    tip: 'Minor and major penalties he takes per 60 minutes in all situations. Fewer is better. Misconducts and bench minors are left out.' },
  faceoffs: { group: 'Penalties and faceoffs', short: (v) => `${v.toFixed(1)}% won`, label: 'Faceoffs', lo: 'Loses draws', hi: 'Wins draws', say: (v) => `wins ${v.toFixed(1)}% of his faceoffs`,
    tip: 'Share of faceoffs he wins, in all situations. Only rated for players who take faceoffs regularly.' },
};
export const GROUPS = ['Driving play at 5-on-5', 'Scoring', 'Special teams', 'Physical and puck', 'Penalties and faceoffs'];

export const EDGE: Record<string, TraitInfo> = {
  topSpeed: { group: 'EDGE', short: (v) => `${v.toFixed(1)} mph`, up: 'faster than', down: 'slower than', label: 'Top speed', lo: 'Slower', hi: 'Burner', say: (v) => `${v.toFixed(1)} mph at his fastest`, tip: 'His fastest skating speed, measured by the NHL’s puck and player tracking (NHL EDGE).' },
  bursts: { group: 'EDGE', short: (v) => `${v.toFixed(1)} bursts /60`, up: 'more often than', down: 'less often than', label: 'Fast bursts', lo: 'Rare', hi: 'Frequent', say: (v) => `${v.toFixed(1)} bursts over 20 mph per 60`, tip: 'How often he gets above 20 mph, per 60 minutes on the ice. Measured by NHL EDGE.' },
  shotSpeed: { group: 'EDGE', short: (v) => `${v.toFixed(1)} mph`, up: 'harder than', down: 'softer than', label: 'Hardest shot', lo: 'Softer', hi: 'Cannon', say: (v) => `${v.toFixed(1)} mph on his hardest shot`, tip: 'The speed of his hardest shot, measured by NHL EDGE.' },
  distance: { group: 'EDGE', short: (v) => `${v.toFixed(2)} miles /60`, up: 'farther than', down: 'less far than', label: 'Distance skated', lo: 'Shorter', hi: 'Covers ground', say: (v) => `${v.toFixed(2)} miles skated per 60`, tip: 'How far he skates per 60 minutes on the ice. Measured by NHL EDGE.' },
  oz: { group: 'EDGE', short: (v) => `${v.toFixed(1)}% of his time`, up: 'more than', down: 'less than', label: 'Time in the offensive zone', lo: 'Defending', hi: 'Attacking', say: (v) => `${v.toFixed(1)}% of his even-strength time in the offensive zone`,
    tip: 'Share of his even-strength ice time the puck spends in the offensive zone. Measured by NHL EDGE. His linemates and his usage (who he plays against, where his shifts start) shape this a lot.' },
};

export const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
};
/** League-rank tag for the very best in a comparison group: 1st, 2nd, 3rd, Top 5, Top 10. Nothing below that. */
export const rankTag = (rank?: number, ok = true) => (!ok || !rank || rank > 10 ? null : rank <= 3 ? ordinal(rank) : rank <= 5 ? 'Top 5' : 'Top 10');
export const rankTip = (rank: number, of: number, label: string, vs: string) => `${rank === 1 ? 'The best' : `${ordinal(rank)}-best`} of ${of} ${vs} at ${label.toLowerCase()}.`;
/** Where a percentile sits, said from its own end so it never reads like a rank: 98 → "top 2%", 17 → "bottom 17%". */
export const topShare = (pct: number) => {
  const p = Math.round(pct);
  return p >= 45 && p <= 55 ? 'average' : p > 55 ? `top ${Math.max(1, 100 - p)}%` : `bottom ${Math.max(1, p)}%`;
};
export const most = (n: number) => (n === 1 ? 'most' : `${ordinal(n)}-most`);

/** Strengths and weak spots: confident departures from the position average, biggest first. */
export function standouts(p: Skater, mode: Mode) {
  const out: { key: string; info: TraitInfo; pct: number; v: number; est?: Est; edge?: boolean; vs: string; rank?: number; of: number }[] = [];
  for (const [key, info] of Object.entries(TRAITS)) {
    const e = p.traits[key]?.[mode];
    if (!e || !e.ok) continue;
    if ((e.pct >= 75 && e.lo >= 55) || (e.pct <= 25 && e.hi <= 45)) out.push({ key, info, pct: e.pct, v: e.index ?? e.v, est: e, vs: peerWord[e.vs ?? p.group], rank: e.rank, of: e.of });
  }
  for (const [key, info] of Object.entries(EDGE)) {
    const e = p.edge?.[mode]?.[key];
    if (!e || !e.ok) continue;
    if (e.pct >= 85 || e.pct <= 15) out.push({ key, info, pct: e.pct, v: e.v, edge: true, vs: peerWord[e.vs ?? p.group], rank: e.rank, of: e.of });
  }
  const best = out.filter((x) => x.pct >= 50).sort((a, b) => b.pct - a.pct).slice(0, 4);
  const worst = out.filter((x) => x.pct < 50).sort((a, b) => a.pct - b.pct).slice(0, 4);
  return { best, worst };
}

export const roleTip: Record<string, string> = {
  Quarterback: 'Quarterback: the defenseman who runs the unit from the blue line.',
  Trigger: 'Trigger: the unit’s main shooter, with the most dangerous power-play shots on the team over this season and last.',
  Distributor: 'Distributor: the main passer, with the most first assists on the team’s power-play goals over this season and last.',
  'Net-front': 'Net-front: parks in front of the net. At least 40% of his power-play shots come from within 15 feet.',
  Point: 'Point: a second defenseman on the unit, alongside the quarterback.',
};

export const toiLine = (t: NonNullable<Role['toi']>) => {
  const parts = [`${clock(t.toi5)} at 5-on-5`];
  if (t.toi_pp >= 15) parts.push(`${clock(t.toi_pp)} on the power play`);
  if (t.toi_pk >= 15) parts.push(`${clock(t.toi_pk)} shorthanded`);
  return parts;
};
