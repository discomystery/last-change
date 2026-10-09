// Phase 1 mock data. Names, records, date and venue are real (see mock-context.json).
// Every statistic, line grouping, role tag and insight below is INVENTED so the layout
// can be reviewed before the pipeline exists. Nothing here is a real measurement.
import ctx from './mock-context.json';

export type Abbr = 'CAR' | 'EDM';
type Skater = { id: number; name: string; pos: string; num: number; toi: string };

function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rng = mulberry32(20261022);
const between = (lo: number, hi: number) => lo + rng() * (hi - lo);
const round = (v: number, d = 1) => Number(v.toFixed(d));
const clamp = (v: number, lo = 1, hi = 99) => Math.max(lo, Math.min(hi, Math.round(v)));
const secs = (toi: string) => {
  const [m, s] = toi.split(':').map(Number);
  return m * 60 + s;
};
const byToi = (a: Skater, b: Skater) => secs(b.toi) - secs(a.toi);
const chunk = <T,>(arr: T[], n: number) => arr.reduce<T[][]>((acc, x, i) => (i % n ? acc[acc.length - 1].push(x) : acc.push([x]), acc), []);

export const game = ctx.game;
export const fetchedAt = ctx.fetchedAt;
export const away = ctx.game.away as Abbr;
export const home = ctx.game.home as Abbr;
export const order: Abbr[] = [away, home];

export const teams = Object.fromEntries(
  (['CAR', 'EDM'] as Abbr[]).map((a) => {
    const t = ctx.teams[a];
    return [a, { abbr: a, name: t.name, place: t.place, coach: t.coach, record: t.record, fromGame: t.fromGame }];
  }),
) as Record<Abbr, { abbr: Abbr; name: string; place: string; coach: string | null; record: { w: number; l: number; otl: number; gp: number }; fromGame: { id: number; date: string } }>;

// ---------- Style fingerprint ----------
export type Dim = { key: string; label: string; tip: string; CAR: number; EDM: number; neutral?: boolean };
export const fingerprint: Dim[] = [
  { key: 'volume', label: 'Shot volume', tip: 'How many shot attempts the team takes per hour of 5-on-5 play.', CAR: 96, EDM: 71 },
  { key: 'quality', label: 'Shot quality', tip: 'How dangerous the average unblocked shot is, by expected goals.', CAR: 22, EDM: 88 },
  { key: 'suppression', label: 'Shot suppression', tip: 'How few shot attempts the team allows. Further right means fewer allowed.', CAR: 94, EDM: 48 },
  { key: 'qualityAllowed', label: 'Quality allowed', tip: 'How dangerous the average shot against is. Further right means safer shots against.', CAR: 31, EDM: 55 },
  { key: 'pace', label: 'Pace', tip: 'Total shots both ways per hour. High-pace teams play track meets.', CAR: 78, EDM: 66, neutral: true },
  { key: 'rush', label: 'Rush offense', tip: 'Share of scoring chances that come off the rush rather than from zone time.', CAR: 35, EDM: 91, neutral: true },
  { key: 'rebounds', label: 'Second chances', tip: 'Share of scoring chances that come from rebounds.', CAR: 74, EDM: 52, neutral: true },
  { key: 'turnover', label: 'Off-turnover offense', tip: 'Share of scoring chances within five seconds of forcing a turnover.', CAR: 81, EDM: 63, neutral: true },
  { key: 'point', label: 'Point-shot reliance', tip: 'Share of shot attempts taken by defensemen or from long range.', CAR: 85, EDM: 30, neutral: true },
  { key: 'forecheck', label: 'Forecheck pressure', tip: 'Hits, takeaways and forced giveaways in the offensive zone. A rough stand-in for forechecking, adjusted for each arena’s scorer.', CAR: 97, EDM: 44 },
  { key: 'physical', label: 'Physicality', tip: 'Hits per hour in close games, adjusted for each arena’s scorer.', CAR: 62, EDM: 58, neutral: true },
  { key: 'breakdowns', label: 'Breakdowns avoided', tip: 'How rarely the team gives up a dangerous rush or turnover chance. Further right means fewer breakdowns.', CAR: 28, EDM: 40 },
  { key: 'pp', label: 'Power play', tip: 'Expected goals per hour with the man advantage.', CAR: 58, EDM: 95 },
  { key: 'pk', label: 'Penalty kill', tip: 'Expected goals allowed per hour shorthanded. Further right means stingier.', CAR: 90, EDM: 45 },
  { key: 'powerKill', label: 'Power kill', tip: 'How dangerous the team is while shorthanded.', CAR: 93, EDM: 38 },
  { key: 'discipline', label: 'Discipline', tip: 'Penalties drawn minus penalties taken, per hour.', CAR: 66, EDM: 41 },
  { key: 'goalie', label: 'Goaltending', tip: 'Goals saved above expected per hour, all goalies combined.', CAR: 37, EDM: 52 },
  { key: 'depth', label: 'Depth', tip: 'How much the bottom lines play and how well they do with it.', CAR: 92, EDM: 33 },
];
export const fingerprintBands = Object.fromEntries(
  fingerprint.map((d) => [d.key, { CAR: Math.round(between(9, 17)), EDM: Math.round(between(9, 17)) }]),
) as Record<string, Record<Abbr, number>>;

// Offensive strength of one team paired with the matching defensive dimension of the other.
const clashPairs: [string, string, string][] = [
  ['rush', 'breakdowns', 'rush offense against breakdowns avoided'],
  ['volume', 'suppression', 'shot volume against shot suppression'],
  ['quality', 'qualityAllowed', 'shot quality against quality allowed'],
  ['pp', 'pk', 'power play against penalty kill'],
  ['powerKill', 'pp', 'shorthanded threat against power play'],
];
const dim = (k: string) => fingerprint.find((d) => d.key === k)!;
export const clashes = clashPairs
  .flatMap(([off, def, phrase]) =>
    order.map((a) => {
      const b = a === 'CAR' ? 'EDM' : 'CAR';
      const o = dim(off)[a];
      const dv = dim(def)[b];
      return { attacker: a as Abbr, defender: b as Abbr, off, def, phrase, o, d: dv, gap: o - dv };
    }),
  )
  .sort((x, y) => y.gap - x.gap)
  .slice(0, 3);
export const clashKeys = new Set(clashes.flatMap((c) => [c.off, c.def]));

// ---------- Lines and pairs ----------
const roleTagsF = ['Scoring', 'Two-way', 'Shutdown', 'Energy'];
const roleTagsD = ['Shutdown', 'Puck-moving', 'Sheltered'];
function units(a: Abbr) {
  const t = ctx.teams[a];
  const deep = a === 'CAR';
  const fToi = deep ? [13.4, 12.6, 11.9, 9.8] : [15.8, 13.9, 10.4, 7.6];
  const dToi = deep ? [17.9, 16.4, 13.6] : [19.2, 16.1, 12.3];
  const mk = (players: Skater[], i: number, kind: 'L' | 'P') => {
    const xgf = round(between(2.1, 3.3) - i * 0.15, 2);
    const xga = round(between(2.0, 3.0) + (deep ? -0.2 : 0.1), 2);
    return {
      label: `${kind}${i + 1}`,
      players: players.map((p) => p.name),
      toi: kind === 'L' ? fToi[i] : dToi[i],
      xgf,
      xga,
      xgfPct: round((xgf / (xgf + xga)) * 100, 1),
      ozs: Math.round(between(38, 64)),
      qoc: clamp(between(88, 96) - i * 24 + between(-6, 6)),
      role: kind === 'L' ? roleTagsF[(i + (deep ? 2 : 0)) % 4] : roleTagsD[i],
      minutes: Math.round((kind === 'L' ? fToi[i] : dToi[i]) * t.record.gp * between(0.5, 0.8)),
    };
  };
  const fwd = chunk([...t.forwards].sort(byToi) as Skater[], 3).slice(0, 4).map((p, i) => mk(p, i, 'L'));
  const def = chunk([...t.defense].sort(byToi) as Skater[], 2).slice(0, 3).map((p, i) => mk(p, i, 'P'));
  return { fwd, def };
}
export const lines = { CAR: units('CAR'), EDM: units('EDM') };

// Share of each HOME forward line's 5v5 time spent against each AWAY forward line.
export const matchupMatrix = [
  [21, 17, 46, 16],
  [34, 31, 19, 16],
  [27, 28, 21, 24],
  [18, 24, 14, 44],
];
// Share of each HOME defense pair's 5v5 time spent against each AWAY forward line.
export const pairMatrix = [
  [41, 29, 18, 12],
  [26, 31, 24, 19],
  [14, 20, 31, 35],
];
export const meetings = [
  { date: 'Mar 3, 2026', site: 'at CAR', score: 'CAR 3, EDM 2 (OT)', note: 'Top lines shared 6:40 at 5v5; expected goals 0.4 to 0.5 in that time.' },
  { date: 'Nov 18, 2025', site: 'at EDM', score: 'EDM 4, CAR 1', note: 'Top lines shared 9:10 at 5v5; expected goals 0.3 to 0.9 in that time.' },
];

// ---------- Special teams ----------
const ppRoles = ['Trigger', 'Distributor', 'Net-front', 'Bumper', 'Flank'];
function special(a: Abbr) {
  const t = ctx.teams[a];
  const f = [...t.forwards].sort(byToi) as Skater[];
  const d = [...t.defense].sort(byToi) as Skater[];
  const pp1 = [f[0], f[1], f[2], f[3], d[0]];
  const pp2 = [f[4], f[5], f[6], f[7], d[1]];
  const strong = a === 'EDM';
  return {
    pp: [pp1, pp2].map((u, i) => ({
      label: `PP${i + 1}`,
      share: i === 0 ? (strong ? 71 : 58) : strong ? 29 : 42,
      players: u.map((p, j) => ({ name: p.name, role: ppRoles[j] })),
    })),
    pk: chunk([f[8], f[9], d[2], d[3], f[10], f[11], d[4], d[5]], 4).map((u, i) => ({ label: `PK${i + 1}`, share: i === 0 ? 54 : 46, players: u.map((p) => p.name) })),
    rates: [
      { label: 'Power play', tip: 'Expected goals per hour at 5-on-4.', value: strong ? '9.4' : '7.1', unit: 'xGF/60', pct: strong ? 95 : 58 },
      { label: 'Penalty kill', tip: 'Expected goals allowed per hour at 4-on-5. Further right means stingier.', value: strong ? '7.9' : '5.8', unit: 'xGA/60', pct: strong ? 45 : 90 },
      { label: 'Power kill', tip: 'Expected goals the team creates per hour while shorthanded.', value: strong ? '0.5' : '1.3', unit: 'SH xGF/60', pct: strong ? 38 : 93 },
      { label: 'Discipline', tip: 'Penalties drawn minus penalties taken, per hour.', value: strong ? '−0.21' : '+0.18', unit: 'net/60', pct: strong ? 41 : 66 },
    ],
    projectedOpps: strong ? 2.6 : 3.4,
    shots: shotCloud(strong ? [0.34, 0.18, 0.2, 0.2, 0.08] : [0.2, 0.2, 0.16, 0.14, 0.3]),
  };
}
// Offensive-zone points in feet: x from the blue line (25) to the end boards (100), y across the rink.
function shotCloud(weights: number[]) {
  const centers = [
    { x: 67, y: -21, xg: 0.11 },
    { x: 67, y: 21, xg: 0.09 },
    { x: 74, y: 0, xg: 0.16 },
    { x: 84, y: 0, xg: 0.22 },
    { x: 36, y: 0, xg: 0.03 },
  ];
  const pts: { x: number; y: number; xg: number }[] = [];
  centers.forEach((c, i) => {
    const n = Math.round(weights[i] * 70);
    for (let k = 0; k < n; k++) {
      pts.push({
        x: Math.min(97, Math.max(27, c.x + between(-7, 7))),
        y: Math.min(40, Math.max(-40, c.y + between(-8, 8) * (i === 4 ? 2.2 : 1))),
        xg: round(Math.max(0.01, c.xg * between(0.5, 1.6)), 2),
      });
    }
  });
  return pts;
}
export const specialTeams = { CAR: special('CAR'), EDM: special('EDM') };

// ---------- Goalies ----------
function goalie(a: Abbr, i: number) {
  const t = ctx.teams[a];
  const sorted = [...t.goalies].sort((x, y) => secs(y.toi) - secs(x.toi));
  const g = sorted[i];
  const gsax60 = round(between(-0.35, 0.4), 2);
  const xga60 = round(a === 'CAR' ? between(2.1, 2.4) : between(2.5, 2.9), 2);
  const games = Array.from({ length: 24 }, () => round(between(-1, 1) + between(-1, 1) + between(-0.9, 0.9) + gsax60, 1));
  const bins = [-3, -2, -1, 0, 1, 2].map((lo) => ({ lo, n: games.filter((v) => Math.max(-3, Math.min(2.99, v)) >= lo && Math.max(-3, Math.min(2.99, v)) < lo + 1).length }));
  return {
    name: g.name,
    probable: i === 0,
    starts: games.length,
    gsax: round(gsax60 * games.length * 0.97, 1),
    gsax60,
    xga60,
    tiers: [
      { tier: 'Low danger', sv: round(between(0.972, 0.984), 3), gsax: round(gsax60 * 24 * 0.2 + between(-0.4, 0.4), 1) },
      { tier: 'Medium danger', sv: round(between(0.9, 0.925), 3), gsax: round(gsax60 * 24 * 0.3 + between(-0.5, 0.5), 1) },
      { tier: 'High danger', sv: round(between(0.78, 0.84), 3), gsax: round(gsax60 * 24 * 0.47 + between(-0.6, 0.6), 1) },
    ],
    workload: [
      { label: 'Shots faced per 60', tip: 'Shots on goal he faces per hour.', value: round(a === 'CAR' ? between(24, 26.5) : between(28, 31), 1) },
      { label: 'Danger per shot', tip: 'Average expected-goal value of an unblocked shot against him.', value: round(a === 'CAR' ? between(0.078, 0.086) : between(0.066, 0.074), 3) },
      { label: 'Breakdown chances per 60', tip: 'Dangerous rush or turnover chances he faces per hour. A stand-in for chances faced alone; public data cannot see where defenders are.', value: round(between(2.2, 3.6), 1) },
      { label: 'Median gap between shots', tip: 'The typical wait, in game-clock time, between one shot and the next.', value: `${Math.round(a === 'CAR' ? between(62, 78) : between(44, 56))} s` },
      { label: 'Shots after 3+ quiet minutes', tip: 'Share of shots that arrive after he has gone at least three minutes without one.', value: `${Math.round(a === 'CAR' ? between(11, 16) : between(5, 9))}%` },
      { label: 'Rebounds above expected', tip: 'Positive means he gives up fewer rebounds than a typical goalie would on the same shots.', value: (between(-4, 5) > 0 ? '+' : '−') + round(Math.abs(between(0.5, 4.5)), 1) },
    ],
    consistency: { qs: Math.round(between(46, 64)), rbs: Math.round(between(8, 19)), disasters: bins[0].n + bins[1].n > 3 ? 3 : 2, steals: 2 },
    bins,
  };
}
export const goalies = { CAR: [goalie('CAR', 0), goalie('CAR', 1)], EDM: [goalie('EDM', 0), goalie('EDM', 1)] };
export const leagueGoalies = Array.from({ length: 58 }, () => ({ x: round(between(2.0, 3.15), 2), y: round((between(-0.5, 0.5) + between(-0.4, 0.4)) * 0.7, 2) }));

// ---------- How they win ----------
export const goalSources = [
  { label: 'Sustained zone time', tip: 'Goals that did not come from any of the quicker-strike situations below.', CAR: 34, EDM: 24, lg: 29 },
  { label: 'Power play', tip: 'Goals scored with the man advantage.', CAR: 17, EDM: 27, lg: 21 },
  { label: 'Rush', tip: 'Goals scored quickly after carrying the puck up ice.', CAR: 9, EDM: 19, lg: 13 },
  { label: 'Rebound', tip: 'Goals scored on a second chance right after a save.', CAR: 13, EDM: 9, lg: 11 },
  { label: 'Off a turnover', tip: 'Goals within five seconds of a giveaway or takeaway.', CAR: 12, EDM: 8, lg: 9 },
  { label: 'Off a faceoff win', tip: 'Goals within five seconds of winning an offensive-zone draw.', CAR: 6, EDM: 5, lg: 6 },
  { label: 'Empty net', tip: 'Goals into an empty net.', CAR: 5, EDM: 6, lg: 7 },
  { label: 'Shorthanded', tip: 'Goals scored while killing a penalty.', CAR: 4, EDM: 2, lg: 3 },
];
export const recipes: Record<Abbr, { text: string; sample: string }[]> = {
  CAR: [
    { text: 'When Carolina’s forecheck pressure is above its own median, they win 66% of the time, compared with 45% otherwise.', sample: '58 and 61 games · 80% range 57–74%' },
    { text: 'When Carolina allows two or fewer breakdown chances while the game is tied, they win 63%.', sample: '71 games · 80% range 55–70%' },
    { text: 'Against top-ten opponents, scoring first matters more than usual: 70% when they do, 31% when they don’t.', sample: '27 and 22 games · wide range, treat with care' },
  ],
  EDM: [
    { text: 'When Edmonton’s rush share of chances is above its own median, they win 64% of the time, compared with 47% otherwise.', sample: '60 and 59 games · 80% range 56–72%' },
    { text: 'When Edmonton gets four or more power plays, they win 68%.', sample: '44 games · 80% range 58–77%' },
    { text: 'When the starter saves at least one goal above expected, they win 79%.', sample: '39 games · 80% range 69–87%' },
  ],
};

// ---------- Key players ----------
const sigPool = [
  ['Shot attempts', 'Individual shot attempts per hour at 5-on-5.'],
  ['Primary assists', 'Passes that directly set up a goal, per hour.'],
  ['Hits', 'Hits per hour, adjusted for each arena’s scorer.'],
  ['Takeaways', 'Takeaways per hour, adjusted for each arena’s scorer.'],
  ['Blocks', 'Blocked shots per hour, adjusted for each arena’s scorer.'],
  ['Defensive-zone starts', 'How often the coach starts his shift in the defensive zone.'],
  ['Shorthanded ice time', 'Penalty-kill minutes per game.'],
  ['Rush shots', 'Share of his own shots that come off the rush.'],
  ['Dangerous giveaways avoided', 'How rarely he turns the puck over in the middle of his own zone.'],
  ['Competition faced', 'How much he plays against the other team’s top players.'],
];
const roleMix: [string, string][] = [
  ['Playmaker', 'Two-way'],
  ['Sniper', 'Playmaker'],
  ['Shutdown', 'Two-way'],
  ['Puck-mover', 'Offensive'],
];
function keyPlayers(a: Abbr) {
  const t = ctx.teams[a];
  const f = [...t.forwards].sort(byToi) as Skater[];
  const d = [...t.defense].sort(byToi) as Skater[];
  return [f[0], f[1], d[0], d[1]].map((p, i) => {
    const main = Math.round(between(55, 80));
    const picks = [...sigPool].sort(() => rng() - 0.5).slice(0, 3);
    return {
      name: p.name,
      pos: p.pos === 'D' ? 'Defense' : p.pos === 'C' ? 'Center' : 'Wing',
      roles: [
        { name: roleMix[i][0], pct: main },
        { name: roleMix[i][1], pct: Math.round((100 - main) * 0.7) },
      ],
      signature: picks.map(([label, tip]) => ({ label, tip, pct: clamp(between(82, 99)) })),
    };
  });
}
export const keyPlayers_ = { CAR: keyPlayers('CAR'), EDM: keyPlayers('EDM') };

// ---------- What to watch ----------
const nm = (a: Abbr) => teams[a].place;
export const insights = [
  ...clashes.slice(0, 2).map((c) => ({
    head: `${nm(c.attacker)}’s ${c.phrase.split(' against ')[0]} meets a soft spot`,
    body: `${nm(c.attacker)} ranks in the ${ordinal(c.o)} percentile for ${c.phrase.split(' against ')[0]}; ${nm(c.defender)} is in the ${ordinal(c.d)} for ${c.phrase.split(' against ')[1]}.`,
    cite: 'Style fingerprint · 5-on-5 · last season blended with 5 games this season',
  })),
  {
    head: `${nm(home)} decides the matchups`,
    body: `At home, ${teams[home].coach ?? 'the home coach'} has sent his top line out against the visitors’ third line 46% of the time, well above what line rotation alone would give.`,
    cite: 'Matchup matrix · home games only · 41 games',
  },
  {
    head: 'Strength against strength on special teams',
    body: `${nm('EDM')}’s power play (95th percentile) faces a ${nm('CAR')} penalty kill in the 90th that also attacks shorthanded (93rd).`,
    cite: 'Special teams · 5-on-4 and 4-on-5 · 310 and 295 minutes',
  },
];
export function ordinal(n: number) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// ---------- Fingerprint cards: plain-language stats, end labels, and the season switch ----------
type FpMeta = { group: string; lo: string; hi: string; at0: number; at100: number; say: (v: number) => string; raw?: (pct: number) => string };
const f1 = (v: number) => v.toFixed(1);
export const fpGroups = ['Offense', 'Defense', 'Tempo and edge', 'Special teams'];
const fpMeta: Record<string, FpMeta> = {
  volume: { group: 'Offense', lo: 'Selective', hi: 'Relentless', at0: 48, at100: 68, say: (v) => `${f1(v)} shot attempts per hour` },
  quality: { group: 'Offense', lo: 'Perimeter', hi: 'Point-blank', at0: 0.062, at100: 0.092, say: (v) => `one expected goal for every ${f1(1 / v)} shots` },
  rush: { group: 'Offense', lo: 'Cycle', hi: 'Rush', at0: 6, at100: 18, say: (v) => `${f1(v)}% of chances come off the rush` },
  rebounds: { group: 'Offense', lo: 'One-and-done', hi: 'Crashers', at0: 5, at100: 13, say: (v) => `${f1(v)}% of chances come from rebounds` },
  turnover: { group: 'Offense', lo: 'Patient', hi: 'Opportunist', at0: 5, at100: 14, say: (v) => `${f1(v)}% of chances come right after a turnover` },
  point: { group: 'Offense', lo: 'Down low', hi: 'Point-heavy', at0: 26, at100: 44, say: (v) => `${f1(v)}% of shot attempts come from the point` },
  suppression: { group: 'Defense', lo: 'Porous', hi: 'Stingy', at0: 66, at100: 48, say: (v) => `${f1(v)} shot attempts allowed per hour` },
  qualityAllowed: { group: 'Defense', lo: 'Exposed', hi: 'Sheltered', at0: 0.092, at100: 0.062, say: (v) => `opponents need ${f1(1 / v)} shots per expected goal` },
  breakdowns: { group: 'Defense', lo: 'Fire drill', hi: 'Composed', at0: 4.2, at100: 2.0, say: (v) => `${f1(v)} breakdown chances allowed per hour` },
  goalie: { group: 'Defense', lo: 'Sieve', hi: 'Wall', at0: -0.45, at100: 0.45, say: (v) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)} goals saved above expected per hour` },
  pace: { group: 'Tempo and edge', lo: 'Slog', hi: 'Track meet', at0: 78, at100: 98, say: (v) => `${f1(v)} shots per hour, both teams combined` },
  forecheck: { group: 'Tempo and edge', lo: 'Passive', hi: 'Hounding', at0: 78, at100: 124, say: (v) => `forecheck score ${Math.round(v)} (100 is league average)`, raw: (p) => `${f1(9 + p * 0.12 + 1.1)} forecheck plays recorded per hour` },
  physical: { group: 'Tempo and edge', lo: 'Finesse', hi: 'Bruising', at0: 76, at100: 128, say: (v) => `hit score ${Math.round(v)} (100 is league average)`, raw: (p) => `${f1(16 + p * 0.16 - 1.4)} hits recorded per hour` },
  depth: { group: 'Tempo and edge', lo: 'Top-heavy', hi: 'Deep', at0: 28, at100: 40, say: (v) => `${f1(v)}% of 5-on-5 ice time goes to depth players` },
  pp: { group: 'Special teams', lo: 'Harmless', hi: 'Lethal', at0: 5.5, at100: 9.6, say: (v) => `${f1(v)} expected goals per hour on the power play` },
  pk: { group: 'Special teams', lo: 'Leaky', hi: 'Airtight', at0: 9.0, at100: 5.4, say: (v) => `${f1(v)} expected goals allowed per hour shorthanded` },
  powerKill: { group: 'Special teams', lo: 'Bunkered', hi: 'Predatory', at0: 0.3, at100: 1.4, say: (v) => `${f1(v)} expected goals created per hour shorthanded` },
  discipline: { group: 'Special teams', lo: 'Reckless', hi: 'Clean', at0: -0.6, at100: 0.6, say: (v) => `${Math.abs(v).toFixed(2)} ${v >= 0 ? 'more penalties drawn than taken' : 'more penalties taken than drawn'} per hour` },
};
const fpView = (key: string, pct: number, band: number) => {
  const meta = fpMeta[key];
  const value = meta.at0 + (pct / 100) * (meta.at100 - meta.at0);
  const rank = Math.max(1, Math.min(32, Math.round(32 - (pct / 100) * 31)));
  return { pct, band, text: meta.say(value), raw: meta.raw ? meta.raw(pct) : null, rank: ordinal(rank) };
};
export const fpCards = fingerprint.map((d) => {
  const meta = fpMeta[d.key];
  const per = (a: Abbr) => ({
    blend: fpView(d.key, d[a], fingerprintBands[d.key][a]),
    season: fpView(d.key, clamp(d[a] + between(-24, 24)), Math.round(between(26, 34))),
  });
  return { key: d.key, label: d.label, tip: d.tip, group: meta.group, lo: meta.lo, hi: meta.hi, clash: clashKeys.has(d.key), CAR: per('CAR'), EDM: per('EDM') };
});

// Worked example for the arena-scorer explainer (invented numbers).
export const arenaExample = {
  arena: game.venue,
  event: 'hits',
  here: 46.8,
  elsewhere: 39.0,
  raw: 1.2,
  shrunk: 1.15,
  rawCount: 31,
};

// ---------- Post-game recap (invented result for layout review) ----------
export type Verdict = 'held' | 'partly' | 'missed';
export const recap = {
  score: { CAR: 3, EDM: 4, note: 'Final' },
  claims: [
    {
      said: insights[0].head,
      detail: insights[0].body,
      verdict: 'held' as Verdict,
      measure: 'Edmonton rush chances',
      usual: '6.1 per game',
      tonight: '9',
      happened: 'Edmonton got nine chances off the rush worth 1.4 expected goals, and scored twice on them.',
    },
    {
      said: insights[1].head,
      detail: insights[1].body,
      verdict: 'missed' as Verdict,
      measure: 'Edmonton shots per expected goal',
      usual: '11.3',
      tonight: '14.1',
      happened: 'Carolina kept Edmonton to the outside at even strength. Edmonton’s shots were less dangerous than usual, not more.',
    },
    {
      said: insights[2].head,
      detail: insights[2].body,
      verdict: 'partly' as Verdict,
      measure: 'Edmonton top line against Carolina third line',
      usual: '46% of its time',
      tonight: '38%',
      happened: 'The matchup was there in the first period and faded as Carolina changed lines faster after icings and whistles.',
    },
    {
      said: insights[3].head,
      detail: insights[3].body,
      verdict: 'held' as Verdict,
      measure: 'Edmonton power play / Carolina shorthanded',
      usual: '9.4 and 1.3 expected goals per hour',
      tonight: '10.4 and 3.5',
      happened: 'Edmonton scored once in three power plays. Carolina answered with three shorthanded shot attempts and a goal of its own.',
    },
  ],
  surprises: [
    { head: 'Carolina won the rebound battle by a mile', body: 'Seven rebound chances to Edmonton’s one. Carolina averages 3.2 a game.' },
    { head: 'Almost no hitting', body: 'The teams combined for 24 arena-adjusted hits, the fewest in any Carolina game this season.' },
  ],
  jobs: [
    { team: 'CAR' as Abbr, name: teams.CAR && lines.CAR.fwd[0].players[0], role: 'Playmaker', verdict: 'held' as Verdict, line: '2 primary assists against about 0.4 expected for his ice time.' },
    { team: 'CAR' as Abbr, name: lines.CAR.def[0].players[1], role: 'Shutdown', verdict: 'held' as Verdict, line: 'Carolina allowed 0.3 expected goals in his 14 minutes against Edmonton’s top line.' },
    { team: 'CAR' as Abbr, name: lines.CAR.fwd[1].players[0], role: 'Hitter', verdict: 'missed' as Verdict, line: '1 hit against about 4 expected for his ice time.' },
    { team: 'EDM' as Abbr, name: lines.EDM.fwd[0].players[0], role: 'Rush threat', verdict: 'held' as Verdict, line: '5 rush shots against about 2 expected.' },
    { team: 'EDM' as Abbr, name: lines.EDM.def[0].players[1], role: 'Puck-mover', verdict: 'partly' as Verdict, line: '2 dangerous giveaways against about 0.8 expected, but 3 shot assists.' },
    { team: 'EDM' as Abbr, name: lines.EDM.fwd[1].players[0], role: 'Forechecker', verdict: 'missed' as Verdict, line: 'No takeaways or hits in the offensive zone; usually about 3.' },
  ],
  goalies: [
    { team: 'CAR' as Abbr, name: goalies.CAR[0].name, line: 'Allowed 4 on 2.9 expected goals. Three of the four came off breakdown chances.', gsax: '−1.1' },
    { team: 'EDM' as Abbr, name: goalies.EDM[0].name, line: 'Allowed 3 on 3.4 expected goals while facing 38 shots.', gsax: '+0.4' },
  ],
};

// Placeholder fingerprint for any one team (team page layout review). Seeded by the abbreviation.
export function teamFingerprint(abbr: string) {
  const r = mulberry32([...abbr].reduce((h, ch) => h * 31 + ch.charCodeAt(0), 7));
  return fingerprint.map((d) => {
    const meta = fpMeta[d.key];
    const pct = clamp(4 + r() * 92);
    const season = clamp(pct + (r() - 0.5) * 44);
    return { key: d.key, label: d.label, tip: d.tip, group: meta.group, lo: meta.lo, hi: meta.hi,
      blend: fpView(d.key, pct, Math.round(9 + r() * 8)), season: fpView(d.key, season, Math.round(26 + r() * 8)) };
  });
}
